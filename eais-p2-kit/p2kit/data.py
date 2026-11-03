"""Train data search tools: departures, find_trains and train_stops over one SQLite file.

Every train row has a train_id, the id of the train run: a hash of its service day, category, number, first stop and
planned time there. It depends only on the run, so it stays the same when we rebuild the store, and two records of the
same run in the store have the same id."""
import difflib
import hashlib
import math
import re
import sqlite3
import unicodedata
from datetime import date as Date, datetime, timedelta

MAX_WINDOW_H = 4
MAX_ROWS = 40


def norm(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def _delay(plan, act):
    if not plan or not act:
        return None
    d = (datetime.fromisoformat(act) - datetime.fromisoformat(plan)).total_seconds() / 60
    return math.floor(d + 1e-9)


def _t(s):
    """Times as 'YYYY-MM-DD HH:MM' for planned and 'YYYY-MM-DD HH:MM:SS' for recorded values."""
    return s


class ToolError(Exception):
    pass


def train_id(day, cat, num, first_station, first_time):
    """The id of a train run, e.g. "tr-3f9a1c0b7e2d", from its service day, category, number, first stop and planned time
    there ("YYYY-MM-DD HH:MM")."""
    key = "|".join(str(x or "") for x in (day, cat, num, first_station, (first_time or "")[:16]))
    return "tr-" + hashlib.sha256(key.encode()).hexdigest()[:12]


class TrainData:
    def __init__(self, path):
        self.db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
        self._runs = {}  # train_id -> the run (run()), for every run that a tool returned
        self.days = [r[0] for r in self.db.execute("select distinct day from trains order by 1")]
        self._names = [r[0] for r in self.db.execute("select name from stations")]
        self._norms = {}
        for n in self._names:
            self._norms.setdefault(norm(n), n)

    # ---- argument checks -------------------------------------------------------------
    def station(self, name):
        n = norm(name)
        if not n:
            raise ToolError("station is empty")
        hits = sorted({r[0] for r in self.db.execute("select name from aliases where alias=?", (n,))})
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            raise ToolError(f"station '{name}' is ambiguous; use one of: {', '.join(hits[:10])}")
        cands = [v for k, v in self._norms.items() if k.startswith(n + " ") or (" " + n + " ") in (" " + k + " ")]
        close = difflib.get_close_matches(n, list(self._norms), n=8, cutoff=0.75)
        sug = []
        for c in sorted(cands, key=len)[:8] + [self._norms[c] for c in close]:
            if c not in sug:
                sug.append(c)
        raise ToolError(f"unknown station '{name}'" + (f"; did you mean: {', '.join(sug[:10])}" if sug else "; no similar station names"))

    def _day(self, d):
        try:
            return Date.fromisoformat(str(d).strip()).isoformat()
        except ValueError:
            raise ToolError(f"date must be YYYY-MM-DD, got '{d}'")

    def _window(self, d, t0, t1):
        def hm(t):
            m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", str(t or ""))
            if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
                raise ToolError(f"time must be HH:MM, got '{t}'")
            return int(m.group(1)), int(m.group(2))
        day = Date.fromisoformat(self._day(d))
        h0, m0 = hm(t0)
        h1, m1 = hm(t1)
        a = datetime(day.year, day.month, day.day, h0, m0)
        b = datetime(day.year, day.month, day.day, h1, m1)
        if b == a:
            raise ToolError(f"to_time {t1} must differ from from_time {t0}; an earlier to_time runs into the next day")
        if b < a:
            b += timedelta(days=1)
        note = None
        if b - a > timedelta(hours=MAX_WINDOW_H):
            b = a + timedelta(hours=MAX_WINDOW_H)
            note = f"window cut to {MAX_WINDOW_H} hours"
        return a.strftime("%Y-%m-%d %H:%M:%S"), b.strftime("%Y-%m-%d %H:%M:%S"), note

    def _check_day(self, d):
        if d not in self.days:
            raise ToolError(f"no train data for {d}; days with data: {', '.join(self.days)}")

    # ---- row formatting ----------------------------------------------------------------
    @staticmethod
    def _train_name(cat, num):
        # A train without a category (NULL or empty in the store) has only its number as its name.
        return f"{cat or ''} {num}".strip()

    def _ends(self, tid):
        """[(first stop, planned time), (last stop, planned time)] of the store's train tid, times as 'YYYY-MM-DD HH:MM'."""
        return self.db.execute("""select station, substr(coalesce(dep_plan, arr_plan), 1, 16) from stops where tid = ?
                                  and seq in ((select min(seq) from stops where tid = ?), (select max(seq) from stops where tid = ?))
                                  order by seq""", (tid, tid, tid)).fetchall()

    def _id(self, tid):
        """The train_id of the store's train tid, which we remember with its run for run(). The store's records with the
        same id are one run, and the run has the lines of all of them, whichever record a tool returned first."""
        day, cat, num = self.db.execute("select day, cat, num from trains where tid = ?", (tid,)).fetchone()
        first = self._ends(tid)[0]
        rid = train_id(day, cat, num, *first)
        if rid not in self._runs:
            same = [(self._ends(t)[-1], ln) for t, ln in self.db.execute(
                "select tid, line from trains where day = ? and cat is ? and num is ?", (day, cat, num)) if self._ends(t)[0] == first]
            last = max(((t or "", st) for (st, t), _ in same))[1]  # of the record that runs longest
            self._runs[rid] = {"train_id": rid, "train": self._train_name(cat, num), "number": num,
                               "lines": sorted({ln for _, ln in same if ln}), "service_day": day, "from": first[0], "to": last}
        return rid

    def run(self, rid):
        """The run of a train_id that a tool returned: train_id, train (the name as the tools write it), number, lines
        (of all the store's records of the run), service_day, from and to (its first and last stop)."""
        return self._runs.get(rid)

    def _stop(self, r):
        (station, country, src, ap, aa, ak, dp, da, dk, ca, cd) = r
        out = {"station": station, "country": country}
        if ap:
            out.update(arr_planned=ap[:16], arr_recorded=aa, arr_delay_min=_delay(ap, aa), arr_kind=ak or None,
                       arr_cancelled=bool(ca))
        if dp:
            out.update(dep_planned=dp[:16], dep_recorded=da, dep_delay_min=_delay(dp, da), dep_kind=dk or None,
                       dep_cancelled=bool(cd))
        out["source"] = src
        return out

    # ---- tools ---------------------------------------------------------------------------
    def departures(self, station, date, from_time, to_time):
        st = self.station(station)
        d = self._day(date)
        a, b, note = self._window(d, from_time, to_time)
        rows = self.db.execute("""
          select t.tid, t.cat, t.num, t.line, t.operator, s.dep_plan, s.dep_act, s.dep_kind, s.canc_d, s.src,
            (select station from stops z where z.tid = s.tid order by z.seq desc limit 1) dest
          from stops s join trains t using (tid) where s.station = ? and s.dep_plan >= ? and s.dep_plan <= ?
          order by s.dep_plan, t.num""", (st, a, b)).fetchall()
        res = [{"train": self._train_name(c, n), "train_id": self._id(tid), "line": ln or None, "operator": op, "to": dest,
                "dep_planned": dp[:16], "dep_recorded": da, "dep_delay_min": _delay(dp, da), "dep_kind": dk or None,
                "dep_cancelled": bool(cc), "source": src}
               for tid, c, n, ln, op, dp, da, dk, cc, src, dest in rows[:MAX_ROWS]]
        if not res:
            self._check_day(d)  # an empty answer for a day without data would mislead
        out = {"station": st, "from": a[:16], "to": b[:16], "departures": res}
        if len(rows) > MAX_ROWS:
            out["truncated"] = f"{len(rows)} departures, showing the first {MAX_ROWS}; use a shorter window"
        if note:
            out["note"] = note
        return out

    def find_trains(self, origin, destination, date, from_time, to_time):
        o, de = self.station(origin), self.station(destination)
        d = self._day(date)
        a, b, note = self._window(d, from_time, to_time)
        rows = self.db.execute("""
          select t.tid, t.cat, t.num, t.line, t.operator, x.dep_plan, x.dep_act, x.dep_kind, x.canc_d,
                 y.arr_plan, y.arr_act, y.arr_kind, y.canc_a
          from stops x join stops y on y.tid = x.tid and y.seq > x.seq and y.station = ? join trains t on t.tid = x.tid
          where x.station = ? and x.dep_plan >= ? and x.dep_plan <= ? and y.arr_plan is not null
          order by x.dep_plan""", (de, o, a, b)).fetchall()
        res = []
        for tid, c, n, ln, op, dp, da, dk, dc, ap, aa, ak, ac in rows[:MAX_ROWS]:
            res.append({"train": self._train_name(c, n), "train_id": self._id(tid), "line": ln or None, "operator": op,
                        "dep_planned": dp[:16], "dep_recorded": da, "dep_delay_min": _delay(dp, da), "dep_cancelled": bool(dc),
                        "arr_planned": ap[:16], "arr_recorded": aa, "arr_delay_min": _delay(ap, aa), "arr_kind": ak or None,
                        "arr_cancelled": bool(ac)})
        if not res:
            self._check_day(d)  # an empty answer for a day without data would mislead
        out = {"origin": o, "destination": de, "from": a[:16], "to": b[:16], "trains": res,
               "note_direct_only": "direct trains only; for a change, search each leg"}
        if len(rows) > MAX_ROWS:
            out["truncated"] = f"{len(rows)} trains, showing the first {MAX_ROWS}"
        if note:
            out["note"] = note
        return out

    def train_stops(self, train, date):
        # One space between words first, so that no two parts of the pattern can match the same spaces: the old
        # pattern took seconds on a long run of spaces, holding up the runner.
        m = re.fullmatch(r"([A-Za-zÀ-ÿ+ ]*)(\d+)", " ".join(str(train or "").split()))
        if not m:
            raise ToolError(f"train must look like 'ICE 2871' or '2871', got '{train}'")
        cat, num = m.group(1).strip().lower(), m.group(2)
        d = self._day(date)
        prev = (Date.fromisoformat(d) - timedelta(days=1)).isoformat()
        cands = self.db.execute("select tid, day, cat, num, line, operator, cats from trains where num = ? and day in (?, ?)",
                                (num, d, prev)).fetchall()
        found = [x for x in cands if x[1] == d]
        # A train that started the day before and still ran on this date, e.g., a night train or a train boarded after
        # midnight, comes after the trains that started on this date, also when a train of the same number started then.
        for tid, day, c, n, ln, op, cats in cands:
            if day == prev and self.db.execute("select 1 from stops where tid=? and substr(coalesce(dep_plan, arr_plan),1,10)=? limit 1",
                                               (tid, d)).fetchone():
                found.append((tid, day, c, n, ln, op, cats))
        if cat:
            narrowed = [f for f in found if any(cat == x.lower() or cat in x.lower().split() or x.lower().startswith(cat)
                                                for x in (f[6] or f[2] or "").split(","))]
            note = None if narrowed or not found else f"no train with category '{cat}', showing all trains numbered {num}"
            found = narrowed or found
        else:
            note = None
        if not found:
            raise ToolError(f"no train numbered {num} on {d}")
        trains = []
        for tid, day, c, n, ln, op, cats in found[:5]:
            stops = self.db.execute("""select station, country, src, arr_plan, arr_act, arr_kind, dep_plan, dep_act, dep_kind, canc_a, canc_d
                                       from stops where tid=? order by seq""", (tid,)).fetchall()
            trains.append({"train": self._train_name(c, n), "train_id": self._id(tid), "line": ln or None, "operator": op, "service_day": day,
                           "stops": [self._stop(r) for r in stops]})
        out = {"trains": trains}
        if note:
            out["note"] = note
        if len(found) > 5:
            out["truncated"] = f"{len(found)} trains numbered {num}; add the category, e.g. 'ICE {num}'"
        return out
