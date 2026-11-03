"""Build the train store data/trains.sqlite from the practice files, once, after you unzip the kit.

Usage, from the kit folder:
  python3 -I make_store.py [--data data] [--out data/trains.sqlite] [--force]

We read data/trains-<day>.csv.gz for every day and data/stations.csv, and write one SQLite file with the tables and
indexes that the search tools (p2kit/data.py) and run_practice.py use. The store is about 0.7 GB, and the build takes
about 25 seconds. We write to <out>.tmp and rename it at the end, so a failed run leaves no half store behind. We don't
replace an existing store unless you give --force.

The practice files have the same planned times and train names as our own store, so the search tools give the same
answers on both, apart from the trains of the test-day companies, which aren't in the files.
"""
import argparse
import csv
import gzip
import os
import sqlite3
import sys
import time
from datetime import date
from pathlib import Path

KIT = Path(__file__).resolve().parent
sys.path.insert(0, str(KIT))

from p2kit.data import norm  # noqa: E402

SCHEMA = """
create table trains(tid integer primary key, day text, cat text, num text, line text, operator text, cats text, srcs text);
create table stops(tid integer, seq integer, station text, country text, src text, arr_plan text, arr_act text, arr_kind text,
  dep_plan text, dep_act text, dep_kind text, canc_a integer, canc_d integer);
create table stations(name text, norm text, country text);
create table aliases(alias text, name text);
"""
INDEXES = """
create index stops_dep on stops(station, dep_plan);
create index stops_arr on stops(station, arr_plan);
create index stops_tid on stops(tid, seq);
create index trains_num on trains(num, day);
create index aliases_a on aliases(alias);
"""
COLUMNS = ["train", "line", "operator", "service_day", "seq", "station", "country", "source", "arr_planned", "arr_recorded",
           "arr_kind", "arr_cancelled", "dep_planned", "dep_recorded", "dep_kind", "dep_cancelled", "categories", "sources"]


def plan(s):
    """Planned times are 'YYYY-MM-DD HH:MM:SS' in the store; a time to the minute gets ':00'."""
    return (s + ":00" if len(s) == 16 else s) if s else None


def split_train(name):
    """'TGV INOUI 6127' -> ('TGV INOUI', '6127'); the number is the last word. A train without a category in our store
    has only its number: '31535' -> (None, '31535')."""
    cat, space, num = name.rpartition(" ")
    return (cat if space else None), num


def load_day(db, path):
    """Insert the trains and stops of one day file. A train starts at seq 1; its id is the day's base plus its place
    in the file, as in our own store."""
    day = path.name[len("trains-"):-len(".csv.gz")]
    base = (date.fromisoformat(day) - date(2026, 1, 1)).days * 1_000_000
    n_trains, stops = 0, []
    with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
        r = csv.reader(f)
        if next(r) != COLUMNS:
            raise SystemExit(f"{path} doesn't have the columns of a practice file")
        for (train, line, op, sday, seq, station, country, src, ap, aa, ak, ca, dp, da, dk, cd, cats, srcs) in r:
            if seq == "1":
                n_trains += 1
                cat, num = split_train(train)
                cats = None if cat is None else cats
                db.execute("insert into trains values (?,?,?,?,?,?,?,?)", (base + n_trains, sday, cat, num, line, op, cats, srcs or None))
            elif n_trains == 0:
                raise SystemExit(f"{path}: the first row isn't the first stop of a train")
            stops.append((base + n_trains, int(seq), station, country, src, plan(ap), aa or None, ak, plan(dp), da or None, dk,
                          int(ca), int(cd)))
            if len(stops) >= 50000:
                db.executemany("insert into stops values (?,?,?,?,?,?,?,?,?,?,?,?,?)", stops)
                stops = []
    db.executemany("insert into stops values (?,?,?,?,?,?,?,?,?,?,?,?,?)", stops)
    return day, n_trains


def build(data, out, log=print):
    data, out = Path(data), Path(out)
    days = sorted(data.glob("trains-*.csv.gz"))
    if not days or not (data / "stations.csv").is_file():
        raise SystemExit(f"no practice files in {data}: we need trains-<day>.csv.gz and stations.csv")
    tmp = out.with_name(out.name + ".tmp")
    if tmp.exists():
        tmp.unlink()
    t0 = time.time()
    db = sqlite3.connect(tmp)
    db.executescript("pragma journal_mode = off; pragma synchronous = off; pragma cache_size = -65536;" + SCHEMA)
    for i, path in enumerate(days, 1):
        day, n = load_day(db, path)
        db.commit()
        log(f"{i}/{len(days)} {day}: {n:,} trains ({time.time() - t0:.0f} s)", flush=True)
    with open(data / "stations.csv", encoding="utf-8", newline="") as f:
        r = csv.reader(f)
        if next(r) != ["station", "country", "search_keys"]:
            raise SystemExit(f"{data / 'stations.csv'} doesn't have the columns station, country, search_keys")
        stations, aliases = [], set()
        for name, country, keys in r:
            stations.append((name, norm(name), country))
            aliases.update((a, name) for a in keys.split("; ") if a)
    db.executemany("insert into stations values (?,?,?)", sorted(stations))
    db.executemany("insert into aliases values (?,?)", sorted(aliases))
    db.commit()
    log(f"stations: {len(stations):,}; now the indexes, the slowest step", flush=True)
    db.executescript(INDEXES)
    db.commit()
    db.close()
    os.replace(tmp, out)
    log(f"done: {out} ({out.stat().st_size / 1e9:.1f} GB, {time.time() - t0:.0f} s)", flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build data/trains.sqlite from the practice files.")
    ap.add_argument("--data", default=str(KIT / "data"), help="folder with the practice files (default: data/)")
    ap.add_argument("--out", default="", help="the store to write (default: trains.sqlite in the --data folder)")
    ap.add_argument("--force", action="store_true", help="replace an existing store")
    a = ap.parse_args(argv)
    out = Path(a.out) if a.out else Path(a.data) / "trains.sqlite"
    if out.exists() and not a.force:
        raise SystemExit(f"{out} already exists. To build it again, run: python3 -I make_store.py --force")
    build(a.data, out)


if __name__ == "__main__":
    main()
