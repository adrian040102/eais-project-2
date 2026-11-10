"""The chat page's server: runs your system on one request at a time and streams what happens to the page.

The page (chat_ui/static/, built from chat-ui/ in the course repository with Vercel's AI SDK UI) talks to this server
with the AI SDK's UI message stream protocol: Server-Sent Events, one JSON chunk per event
(https://ai-sdk.dev/docs/ai-sdk-ui/stream-protocol). Each request is one run_set() call with one request, as in
run_practice.py --only, with the same tools, budgets, log and score lines. Unlike those runs, the requests share one
cache folder (the hub's SetupCache), and we run setup(tools) again only after a file in the system's folder changes or
after "Rebuild index"; the data-status part and /api/state say when setup ran and why. We turn the runner's events
into chunks:

- each counted tool call and each free rule-document call is a tool part (tool-input-available when it starts,
  tool-output-available when it ends), with a data-time part for its start and end;
- each model call is a data-llm part, with the messages the system sent (each message's text at most OUTPUT_MAX
  characters) and the model's answer; the request's counters, cost and state are the data-status part; the accepted
  claims and the recorded no_claim answers are the data-claims part (a no_claim answer has no claim id and no
  fields); the end (status, score lines, traceback, output) is the data-result part. On a practice replay, the
  data-result part also has the request's answer from starter/practice/practice_requests.json, each expected claim (or
  no_claim) with the grader's checks as filed against expected (key_compare).

When your system asks the traveler and a person answers, the stream ends at the ask_traveler tool part, as for any
tool whose result the user supplies in the AI SDK. The page adds the answer with addToolOutput and sends it back,
and we hand it to the waiting run. The time the person takes doesn't count against the request's time limit.

Security: we listen on 127.0.0.1 only, accept only our own Host header, and want the run's random token in the
X-P2-Chat-Token header of every API call. The page reads the token from the address that chat.py opens.
"""
import hmac
import json
import mimetypes
import os
import queue
import re
import secrets
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from p2.data import TrainData
from p2.forms import FormDesk
from p2.rules import fold
from p2.runner import SetupCache, starter_inputs, starter_skip, run_set
from p2.llm import MAX_CHARS
from p2.score import DATA_TOOLS, NO_ANSWER, PENALTY_EXTRA, PENALTY_OVER, carried_penalty, no_claim_train_evidence
from p2.tools import MODEL_BUDGET, TOOL_BUDGET
from p2.traveler import TOPICS, UNKNOWN, Traveler, norm_topic

STATIC = Path(__file__).resolve().parent / "static"
OUTPUT_MAX = 64 << 10        # characters of a tool result or model answer that we send to the page
TEXT_MAX = 200_000           # characters of a request
ANSWER_MAX = 4_000           # characters of a traveler answer
BODY_MAX = 2 << 20           # bytes of a POST body
PING_S = 10                  # seconds between keep-alive comments on an idle stream
HISTORY_MAX = 50             # requests we keep for the page
STOP_WAIT_S = 10             # seconds a new request waits for a stopped one to end
STOP = object()              # put into the answer queue to end a wait


class Cancelled(BaseException):
    """The Stop button ended a model call or a wait between retries. It derives from BaseException, so that it goes
    past the runner's handlers and ends run_set() at once, through its cleanup (the child, the cache)."""


def cancellable_post(post):
    """post(url, headers, body, timeout) for the gateway of chat.py: in a run's thread, the HTTP call runs in a
    thread of its own, and we raise Cancelled as soon as the run's cancel event is set, without waiting for the
    answer (up to 120 seconds). The abandoned call ends on its own. Outside a run's thread, post as it is."""
    def call(url, headers, body, timeout):
        cancel = getattr(threading.current_thread(), "p2_cancel", None)
        if cancel is None:
            return post(url, headers, body, timeout)
        if cancel.is_set():
            raise Cancelled()
        box, done = {}, threading.Event()

        def work():
            try:
                box["value"] = post(url, headers, body, timeout)
            except BaseException as e:  # HTTPFailure, as post raises it, or anything else
                box["error"] = e
            finally:
                done.set()

        threading.Thread(target=work, daemon=True, name="model-call").start()
        while not done.wait(0.1):
            if cancel.is_set():
                raise Cancelled()
        if cancel.is_set():
            raise Cancelled()
        if "error" in box:
            raise box["error"]
        return box["value"]
    return call


def cancellable_sleep(seconds):
    """The gateway's wait between retries; in a run's thread, the Stop button ends it."""
    cancel = getattr(threading.current_thread(), "p2_cancel", None)
    if cancel is None:
        time.sleep(seconds)
    elif cancel.wait(seconds):
        raise Cancelled()


def now_ms():
    return int(time.time() * 1000)


def clean_text(s, limit):
    """Text without lone surrogates (they can't be written as UTF-8), at most limit characters."""
    return str(s).encode("utf-8", "replace").decode("utf-8")[:limit]


def capped(value):
    """value, or a cut-down preview if its JSON is longer than OUTPUT_MAX characters."""
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = repr(value)
    if len(text) <= OUTPUT_MAX:
        return value
    return {"cut": True, "chars": len(text), "preview": text[:OUTPUT_MAX]}


def message_view(m, left):
    """One message of a model call, as the page shows it: its role, its text, and the tool calls of an assistant
    message or the call id of a tool message, with the full length of each text in chars. Each text has at most
    OUTPUT_MAX characters, and all texts together at most left[0] characters, which we count down."""
    def cut(text):
        text = str(text)
        out = clean_text(text, min(OUTPUT_MAX, left[0]))
        left[0] -= len(out)
        return out, len(text)

    if not isinstance(m, dict):
        text, chars = cut(m if isinstance(m, str) else json.dumps(m, ensure_ascii=False, default=str))
        return {"role": "?", "text": text, "chars": chars}
    content = m.get("content")
    if isinstance(content, list):  # content parts, e.g. [{"type": "text", "text": "..."}]
        text = "\n".join(p["text"] if isinstance(p, dict) and isinstance(p.get("text"), str)
                         else json.dumps(p, ensure_ascii=False, default=str) for p in content)
    else:
        text = "" if content is None else content if isinstance(content, str) else json.dumps(content, default=str)
    text, chars = cut(text)
    out = {"role": str(m.get("role")), "text": text, "chars": chars}
    if m.get("name"):
        out["name"] = clean_text(m["name"], 200)
    if m.get("tool_call_id"):
        out["toolCallId"] = clean_text(m["tool_call_id"], 200)
    calls = m.get("tool_calls")
    if isinstance(calls, list):
        out["toolCalls"] = []
        for c in calls:
            c = c if isinstance(c, dict) else {"function": c}
            fn = c.get("function")
            fn = fn if isinstance(fn, dict) else {"arguments": fn}
            args = fn.get("arguments")
            args, chars = cut(args if isinstance(args, str) else json.dumps(args, ensure_ascii=False, default=str))
            out["toolCalls"].append({"id": clean_text(c.get("id") or "", 200), "name": clean_text(fn.get("name") or "", 200),
                                     "arguments": args, "chars": chars})
    return out


def model_messages(messages):
    """The messages of a model call for the page (message_view), at most MAX_CHARS characters of text in all, as the
    gateway allows for one call, so that a long session doesn't keep tens of MB. None if there are none. A message
    shape we don't know comes as its raw JSON, so that the page never fails a call that the runner accepts."""
    if messages is None:
        return None
    left = [MAX_CHARS]
    try:
        return [message_view(m, left) for m in (messages if isinstance(messages, list) else [messages])]
    except Exception:
        raw = json.dumps(messages, ensure_ascii=False, default=str)
        return [{"role": "?", "text": clean_text(raw, OUTPUT_MAX), "chars": len(raw)}]


def topic_groups(groups):
    """Required topic groups as text: "ticket_seller or ticket_structure; ticket_price", or "none"."""
    return "; ".join(" or ".join(g) for g in groups) or "none"


def looked_up_text(looked_up, runs):
    """The train runs looked up before a no_claim: the key's runs among them, or how many others."""
    hits = sorted(set(looked_up) & set(runs))
    if hits:
        return ", ".join(hits)
    return f"not the key's run ({len(looked_up)} other runs)" if looked_up else "none"


def charges(key, filings, row):
    """Every point that the grader takes off a request, with the reason, counted as grade_request() and points() do:
    each extra or duplicate filing and, when a claim is owed, each no_claim (PENALTY_EXTRA); when nothing is owed,
    each claim and each no_claim to a company the answer doesn't accept (PENALTY_EXTRA, in the penalty column); and
    each question over the limit (PENALTY_OVER)."""
    expected = {c["company"] for c in key.get("claims") or []}
    nc = key.get("no_claim") or {}
    accepted = nc.get("companies") or [nc.get("company")]
    out, seen = [], set()
    for f in filings:
        name = f"{f['form']}{' (' + f['claim_id'] + ')' if f.get('claim_id') else ''}"
        if f.get("no_claim"):
            if expected:
                text = f"no_claim to {f['form']}, although a claim is owed"
            elif f["form"] not in accepted:
                text = f"no_claim to {f['form']}, a company the answer doesn't name"
            else:
                continue
        elif not expected:
            text = f"Claim to {name}, although nothing is owed"
        elif f["form"] not in expected:
            text = f"Claim to {name}, a company the answer doesn't name"
        elif f["form"] in seen:
            text = f"Another claim to {name}. We grade only the last claim to a company"
        else:
            seen.add(f["form"])
            continue
        out.append({"text": text, "points": PENALTY_EXTRA, "filing": True})
    over = row.get("over") or 0
    if over:
        out.append({"text": f"{over} question{'s' if over > 1 else ''} over the limit of {row.get('limit')}",
                    "points": PENALTY_OVER * over})
    return out


def key_compare(key, grade, filings, row):
    """A practice request's answer key next to what the system filed, for the page: one item per expected claim, or
    one for the expected no_claim, each with the grader's checks as filed against expected. grade is the grader's
    grade_request() (None when the request was stopped before grading). filings: the run's accepted claims and
    recorded no_claims, with the company of the form, the answered topics before each one and, for a no_claim, the
    train runs looked up before it. Everything comes from the practice file that students have."""
    checks = (grade or {}).get("checks") or []
    claims = [f for f in filings if not f.get("no_claim")]
    items = []
    for c in key.get("claims") or []:
        chk = next((x for x in checks if x.get("company") == c["company"] and not x.get("no_claim")), None)
        mine = [f for f in claims if f["form"] == c["company"]]
        f = mine[-1] if mine else None  # the grader grades the last filing to the company
        ok = lambda k: None if chk is None else bool(chk.get(k))  # noqa: E731
        issues = (chk or {}).get("provenance_issues") or []
        slots, train = (f["slots"], f["train"]) if f else ({}, {})
        filed = lambda text: text if f else ""  # noqa: E731
        day = f", {train['service_day']}" if train.get("service_day") else ""
        rows = [
            {"check": "Company", "filed": f["form"] if f else "no claim filed", "expected": c["company"], "ok": ok("filed")},
            {"check": "Amount", "filed": filed(f"{slots.get('amount')} {slots.get('currency') or ''}".strip()),
             "expected": f"{c['amount']} {c.get('currency', 'EUR')}", "ok": ok("amount")},
            {"check": "Train", "filed": filed(f"{slots.get('train')} ({train.get('train_id', 'no run')}{day})"),
             "expected": f"{c.get('train')} ({', '.join(c.get('runs') or [])}, {key.get('day', '')})", "ok": ok("train")}]
        if slots.get("price") not in (None, ""):
            rows.append({"check": "Ticket price", "filed": str(slots["price"]), "expected": str(c.get("price")),
                         "ok": None if chk is None else not any(x.startswith("price") for x in issues)})
        rows.append({"check": "Questions before filing", "filed": filed(", ".join(f["asked"]) if f and f["asked"] else "none"),
                     "expected": topic_groups(c.get("required_topics") or []),
                     "ok": None if chk is None else bool(f) and not any(x.startswith("missing topic") for x in issues)})
        if c.get("payment"):
            rows.append({"check": "Payment (not scored)", "filed": filed(str(slots.get("payment") or "")),
                         "expected": c["payment"], "ok": None})
        delay = None
        if c.get("delay_min") is not None:
            delay = f"{c['delay_min']} minutes late: planned {c.get('planned_arrival')}, recorded {c.get('recorded_arrival')}"
        items.append({"kind": "claim", "company": c["company"], "ok": None if chk is None else bool(chk.get("ok")),
                      "rows": rows, "delay": delay})
    nc = key.get("no_claim")
    if nc is not None and not key.get("claims"):
        chk = next((x for x in checks if x.get("no_claim")), None)
        declared = [f for f in filings if f.get("no_claim")]
        accepted = nc.get("companies") or [nc.get("company")]
        mine = [f for f in declared if f["form"] in accepted]
        f = mine[-1] if mine else None
        runs = nc.get("required_runs") or []
        rows = [
            {"check": "no_claim to", "filed": ", ".join(d["form"] for d in declared) or "no no_claim recorded",
             "expected": " or ".join(map(str, accepted)), "ok": None if chk is None else bool(chk.get("declared"))},
            {"check": "Claims filed", "filed": ", ".join(x["form"] for x in claims) or "none", "expected": "none",
             "ok": None if chk is None else not chk.get("filed_anything")},
            {"check": "Train looked up before", "filed": looked_up_text(f["looked_up"], runs) if f else "",
             "expected": f"{nc.get('train')} ({', '.join(runs)}, {key.get('day', '')})",
             "ok": None if chk is None or f is None else bool(chk.get("provenance"))},
            {"check": "Questions before no_claim", "filed": ", ".join(f["asked"]) or "none" if f else "",
             "expected": topic_groups(key.get("required_topics") or []),
             "ok": None if chk is None else bool(chk.get("topics_ok"))}]
        items.append({"kind": "no_claim", "company": nc.get("company"), "ok": None if chk is None else bool(chk.get("ok")),
                      "rows": rows, "reason": nc.get("reason")})
    graded = grade is not None
    points = None
    if graded and row.get("points") is not None:
        points = round(row["points"] - carried_penalty(row), 4)
    found = charges(key, filings, row) if graded else []
    if graded and points is not None:
        # points() floors a request's points at 0 before the penalty column, so some deductions may not count
        unused = round(sum(x["points"] for x in found) - (grade["correct"] - points), 4)
        if unused > 0:
            found.append({"text": f"Request points don't go below 0, so {unused:.2f} of the deductions above don't count",
                          "points": -unused})
    return {"items": items, "charges": found, "notes": key.get("notes"),
            "graded": graded, "points": points, "max": row.get("max"), "questions": row.get("questions"),
            "limit": row.get("limit", key.get("limit")), "over": row.get("over"),
            "extraFilings": sum(1 for x in found if x.get("filing"))
            if graded else None}


class HumanTraveler(Traveler):
    """The traveler, played by you in the page. Same question limit and errors for unknown topics as the fact sheet
    traveler."""
    pauses_clock = True

    def __init__(self, limit, ask_human):
        super().__init__({}, limit)
        self._ask_human = ask_human

    def ask(self, topic):
        t = norm_topic(topic) if isinstance(topic, str) else None
        if len(self.asked) >= self.limit or t not in self.topics:
            return super().ask(topic)
        self.asked.append((t, True))
        answer = self._ask_human(t, len(self.asked), self.limit)
        return answer if answer.strip() else UNKNOWN


def apply_chunk(msg, c):
    """Apply one stream chunk to a UI message, as the page does, so that a reloaded page gets the same message."""
    parts, t = msg["parts"], c["type"]
    if t == "start-step":
        parts.append({"type": "step-start"})
    elif t == "tool-input-available":
        parts.append({"type": f"tool-{c['toolName']}", "toolCallId": c["toolCallId"], "state": "input-available",
                      "input": c["input"]})
    elif t in ("tool-output-available", "tool-output-error"):
        for p in parts:
            if p.get("toolCallId") == c["toolCallId"]:
                if t == "tool-output-available":
                    p.update(state="output-available", output=c["output"])
                else:
                    p.update(state="output-error", errorText=c["errorText"])
    elif t.startswith("data-"):
        part = {"type": t, "id": c.get("id"), "data": c["data"]}
        for i, p in enumerate(parts):
            if p["type"] == t and c.get("id") is not None and p.get("id") == c["id"]:
                parts[i] = part
                break
        else:
            parts.append(part)


class Run:
    """One request: its thread, its stream chunks and its state (running, waiting for the traveler, done)."""

    def __init__(self, hub, number, text, mode, replay=None, auto=True):
        self.hub, self.number, self.text, self.mode = hub, number, text, mode
        self.rid = replay["id"] if replay else f"C{number:02d}"
        self.tag = f"r{number}"  # prefix of the tool call ids, unique in the session
        self.message_id = f"m{number}-{secrets.token_hex(4)}"
        self.user = {"id": f"u{number}-{secrets.token_hex(4)}", "role": "user",
                     "parts": [{"type": "text", "text": text}],
                     "metadata": {"at": now_ms(), "mode": mode, "replay": self.rid if replay else None,
                                  "auto": auto if replay else None}}
        self.message = {"id": self.message_id, "role": "assistant", "parts": [], "metadata": {"at": now_ms()}}
        if replay:
            self.facts, self.key = replay["traveler"], replay["answer"]
        else:
            self.facts, self.key = {"answers": {}, "limit": hub.questions}, {"claims": [], "required_topics": []}
        self.human = not (replay and auto)
        self.chunks = []
        self.segments = 0  # streams so far: one, plus one per answer; a page compares it to see changes
        self.state = "running"
        self.final = False
        self.stopped = False
        self.answers = queue.Queue()
        self.waiting = None
        self.child = None
        self.started = now_ms()
        self.counts = {"tools": 0, "model": 0}
        self.usd = 0.0
        self.claims = []
        self.filings = []   # accepted claims and recorded no_claims, for key_compare
        self.answered = []  # topics the traveler answered so far, as the grader counts them
        self.no_data = []   # "no train data for <day>" errors of the train tools, for the end of the request
        self.looked_up = set()  # train runs that identify a train for a no_claim (score.no_claim_train_evidence)
        self.ends = {}  # what we learn about the end: stop, trace, stdout, row, load error
        self.lines = []
        self.in_flight = None
        self.last_start = None
        self.out = hub.out / f"{number:02d}-{self.rid}"
        self.cancel = threading.Event()  # set by Stop: ends a model call or a retry wait at once
        self.thread = threading.Thread(target=self._work, daemon=True, name=f"run-{number}")
        self.thread.p2_cancel = self.cancel

    # ---- chunks (call with the hub's lock held) ------------------------------------------------------------
    def emit(self, *chunks):
        for c in chunks:
            self.chunks.append(c)
            if not c["type"].startswith("_") and c["type"] not in ("start", "finish", "finish-step"):
                apply_chunk(self.message, c)
        self.hub.cond.notify_all()

    def status(self):
        h = self.hub
        return {"type": "data-status", "id": "status", "data": {
            "state": self.state, "startedAt": self.started, "tools": self.counts["tools"], "maxTools": TOOL_BUDGET,
            "model": self.counts["model"], "maxModel": MODEL_BUDGET, "usd": round(self.usd, 6),
            "budgetLeft": h.budget_left, "spent": round(h.gateway.spent, 6), "cap": h.gateway.max_cost,
            "inFlight": self.in_flight, "waiting": self.waiting, "rid": self.rid, "mode": self.mode,
            "human": self.human, "limit": self.facts["limit"], "segments": self.segments, "setup": h.setup_view()}}

    def open_segment(self):
        self.segments += 1
        self.emit({"type": "start", "messageId": self.message_id}, {"type": "start-step"})

    def close_segment(self):
        self.emit({"type": "finish-step"}, {"type": "finish"}, {"type": "_close"})

    # ---- the run ---------------------------------------------------------------------------------------------
    def _work(self):
        summary, crash = None, None
        try:
            self.out.mkdir(parents=True, exist_ok=True)
            summary = run_set(self.hub.system, set_name="chat", requests=[{"id": self.rid, "text": self.text}],
                              facts={self.rid: self.facts}, key={self.rid: self.key}, data=self.hub.data,
                              rule_dirs=self.hub.rule_dirs, form_dirs=self.hub.form_dirs, gateway=self.hub.gateway,
                              out=self.out, timeout_s=self.hub.timeout_s, show_checks=True, sandbox=self.hub.sandbox,
                              packages=self.hub.packages,
                              echo=self._echo, traveler_factory=self._traveler, on_event=self._on_event,
                              on_child=self._on_child, setup_cache=self.hub.setup_cache)
        except Cancelled:  # Stop during a model call; the page already shows "Stopped"
            pass
        except BaseException as e:  # a SandboxError, a full disk, a bug of ours: the page shows it
            crash = f"{type(e).__name__}: {e}\n\n{traceback.format_exc()}"
            self.hub.say(crash)
        finally:
            with self.hub.cond:
                self._finish(summary, crash)
                self.hub.run_ended(self)

    def _echo(self, line):
        self.lines.append(line)
        self.hub.say(line)

    def _traveler(self, rid, facts):
        if self.human:
            return HumanTraveler(facts["limit"], self._ask_human)
        return Traveler(facts["answers"], facts["limit"])

    def _on_child(self, child):
        self.child = child
        if self.stopped:
            threading.Thread(target=self._keep_interrupting, daemon=True).start()

    def _keep_interrupting(self):
        while self.thread.is_alive():
            c = self.child
            if c is not None:
                c.interrupt()
            time.sleep(0.2)

    def _ask_human(self, topic, number, limit):
        with self.hub.cond:
            if self.final:
                return UNKNOWN
            cid = self.last_start
            self.waiting = {"toolCallId": cid, "topic": topic, "meaning": TOPICS.get(topic, ""), "number": number,
                            "limit": limit, "since": now_ms()}
            self.state = "waiting"
            self.emit(self.status())
            self.close_segment()
            self.hub.changed()
        while True:  # a timeout, so that Ctrl+C works on every system
            try:
                answer = self.answers.get(timeout=0.5)
                break
            except queue.Empty:
                continue
        if answer is STOP:
            return UNKNOWN
        return answer

    def answer(self, tool_call_id, text):
        """Called by the server thread of the POST, with the lock held. Returns an error text or None."""
        if self.final or self.state != "waiting" or not self.waiting:
            return "We aren't waiting for an answer now."
        if tool_call_id != self.waiting["toolCallId"]:
            return "This question already has an answer."
        self.state = "running"
        self.waiting = None
        self.open_segment()
        self.emit(self.status())
        self.hub.changed()
        self.answers.put(clean_text(text, ANSWER_MAX))
        return None

    def stop(self):
        """The Stop button, with the lock held: end the stream now; the thread ends the system's process."""
        if self.final:
            return
        self.stopped = True
        self.cancel.set()
        if self.state == "waiting":
            self.open_segment()
        self.state = "running"
        self.answers.put(STOP)
        threading.Thread(target=self._keep_interrupting, daemon=True).start()
        self._finish(None, None)

    # ---- events of the runner (in the run's thread) ----------------------------------------------------------
    def _on_event(self, ev):
        with self.hub.cond:
            if self.final:
                return
            self._event(ev)

    def _call_id(self, n):
        return f"{self.tag}-{n}"

    def _event(self, ev):
        kind = ev.get("kind")
        if ev.get("req") == "setup":
            if kind == "loaded" and not ev.get("ok"):
                self.ends["load"] = ev
            elif kind == "setup":
                self.ends["setup"] = ev
            elif kind == "setup-check":
                self.hub.set_setup("running" if ev["rerun"] else "reused", ev["reason"])
                self.emit(self.status())
            elif kind == "setup-done":
                self.hub.set_setup("ran", ev["last"]["reason"])
                self.emit(self.status())
            return
        if kind == "start":
            call = ev["call"]
            cid = self._call_id(call["n"])
            self.last_start = cid
            self.in_flight = {"id": cid, "kind": call["kind"], "name": call["name"], "since": now_ms(),
                              "model": call.get("model")}
            if call["kind"] == "llm":
                self.counts["model"] += 1
                self.emit({"type": "data-llm", "id": cid, "data": {
                    "model": str(call.get("model")), "state": "running", "start": now_ms()}})
            else:
                self.counts["tools"] += 1
                self.emit({"type": "tool-input-available", "toolCallId": cid, "toolName": call["name"],
                           "input": capped(call.get("args"))},
                          {"type": "data-time", "id": cid, "data": {"start": now_ms(), "counted": True}})
            self.emit(self.status())
        elif kind in ("tool", "doc", "refused"):
            cid = self._call_id(ev["n"])
            end = int(ev.get("t", time.time()) * 1000)
            if self.in_flight and self.in_flight["id"] == cid:
                start = self.in_flight["since"]
                self.in_flight = None
            else:  # no start event: a free call, a refused call or arguments that weren't JSON
                start = end
                if kind == "tool":
                    self.counts["tools"] += 1
                self.emit({"type": "tool-input-available", "toolCallId": cid, "toolName": str(ev.get("name")),
                           "input": capped(ev.get("args"))})
            res = ev.get("result")
            if kind == "refused":
                self.emit({"type": "tool-output-error", "toolCallId": cid, "errorText": str(res.get("error"))})
            else:
                self.emit({"type": "tool-output-available", "toolCallId": cid, "output": capped(res)})
            self.emit({"type": "data-time", "id": cid, "data": {"start": start, "end": end, "counted": kind == "tool"}})
            name = ev.get("name")
            if kind == "tool" and name == "ask_traveler" and isinstance(res, str) and res not in NO_ANSWER:
                t = str((ev.get("args") or {}).get("topic", "")).strip().lower().replace(" ", "_").replace("-", "_")
                if t not in self.answered:
                    self.answered.append(t)
            if kind == "tool" and name in DATA_TOOLS:
                self.looked_up |= no_claim_train_evidence(name, res)
                err = res.get("error") if isinstance(res, dict) else None
                if isinstance(err, str) and err.startswith("no train data for") and err not in self.no_data:
                    self.no_data.append(err)
            if ev.get("name") == "file_claim" and isinstance(res, dict) and res.get("status") == "accepted":
                args = ev.get("args") or {}
                self._filed(args.get("company"), args.get("fields"), res)
                self.claims.append({"company": str(args.get("company")), "claim_id": res.get("claim_id"),
                                    "fields": capped(args.get("fields")), "at": end, "toolCallId": cid})
                self.emit({"type": "data-claims", "id": "claims", "data": {"claims": self.claims}})
            if ev.get("name") == "no_claim" and isinstance(res, dict) and res.get("status") == "recorded":
                self._filed(res.get("company"), None, res)
                self.claims.append({"company": f"No claim: {res.get('company')}", "claim_id": None,
                                    "fields": {}, "at": end, "toolCallId": cid, "no_claim": True})
                self.emit({"type": "data-claims", "id": "claims", "data": {"claims": self.claims}})
            self.emit(self.status())
        elif kind == "llm":
            cid = self._call_id(ev["n"])
            start = now_ms()
            if self.in_flight and self.in_flight["id"] == cid:
                start = self.in_flight["since"]
                self.in_flight = None
            else:
                self.counts["model"] += 1
            u = ev.get("usage") or {}
            res = ev.get("result") or {}
            usd = float(u.get("usd") or 0.0)
            self.usd += usd
            if "budget_left_usd" in u:
                self.hub.budget_left = u["budget_left_usd"]
            reply = None
            messages = model_messages(ev.get("messages"))
            if isinstance(res, dict) and res.get("choices"):
                reply = capped((res["choices"][0] or {}).get("message"))
            self.emit({"type": "data-llm", "id": cid, "data": {
                "model": str(ev.get("model")), "state": "error" if u.get("error") or "error" in res else "done",
                "start": start, "end": int(ev.get("t", time.time()) * 1000),
                "input": u.get("input_tokens", 0), "output": u.get("output_tokens", 0),
                "cached": u.get("cached_tokens", 0), "reasoning": u.get("reasoning_tokens", 0), "usd": usd,
                "error": clean_text(u.get("error") or res.get("error") or "", 2000) or None, "reply": reply,
                "messages": messages}})
            self.emit(self.status())
        elif kind == "stop":
            self.ends["stop"] = str(ev.get("reason"))
        elif kind == "exception":
            self.ends["trace"] = ev.get("trace")
            self.ends["end"] = ev.get("end")
        elif kind == "stdout":
            self.ends["stdout"] = ev.get("text") or ""
        elif kind == "grade":
            self.ends["row"] = ev.get("row")
            self.ends["grade"] = ev.get("grade")

    def _filed(self, company, fields, res):
        """Note an accepted claim or a recorded no_claim for key_compare: the form's company, the form's slots, the
        train run of the form, and what the grader looks at before it (answered topics, looked-up runs)."""
        desk = self.hub.desk
        form = desk.names().get(fold(str(company))) or str(company)
        entry = {"form": form, "asked": list(self.answered), "claim_id": res.get("claim_id")}
        if fields is None:
            entry.update(no_claim=True, looked_up=set(self.looked_up))
        else:
            fields = fields if isinstance(fields, dict) else {}
            entry["slots"] = {s: desk.slot(form, fields, s) if form in desk.forms else None
                              for s in ("amount", "currency", "train", "price", "payment")}
            entry["train"] = res.get("train") or {}
        self.filings.append(entry)

    # ---- the end ---------------------------------------------------------------------------------------------
    def _settle(self, call):
        """A call that was still running when the request ended (Stop, the cost cap, a refused token): end its row,
        so that the page shows no spinner after the end."""
        if not call or call["name"] == "ask_traveler":  # a question without an answer has no spinner
            return
        why = "You stopped the request." if self.stopped else (self.ends.get("stop") or "The request ended.")
        if call["kind"] == "llm":
            self.emit({"type": "data-llm", "id": call["id"], "data": {
                "model": str(call.get("model")), "state": "error", "start": call["since"], "end": now_ms(),
                "error": f"No answer: {clean_text(why, 2000)}"}})
        else:
            self.emit({"type": "tool-output-error", "toolCallId": call["id"],
                       "errorText": f"No result: {clean_text(why, 2000)}"})
            self.emit({"type": "data-time", "id": call["id"], "data": {"start": call["since"], "end": now_ms(),
                                                                       "counted": True}})

    def _finish(self, summary, crash):
        """Emit the result and close the stream, once (with the lock held)."""
        if self.final:
            return
        self.final = True
        if self.hub.setup["state"] == "running":  # Stop during setup: it runs again with the next request
            self.hub.set_setup("stopped", None)
        self.state = "done"
        self.waiting = None
        self._settle(self.in_flight)
        self.in_flight = None
        row = self.ends.get("row") or {}
        status = row.get("status") or ("error" if crash else "stopped" if self.stopped else "unknown")
        if self.stopped:
            kind = "stopped"
        elif crash:
            kind = "crash-runner"
        elif self.ends.get("load"):
            kind = "load"
        elif status == "ok":
            kind = "ok"
        elif status.startswith("error:"):
            kind = "crash"
        else:
            kind = status  # time, budget, cost-cap, no-model
        stop = self.ends.get("stop") or ""
        if kind == "cost-cap" and "course app" in stop:
            kind = "team-budget"
        load = self.ends.get("load") or {}
        trace = crash or self.ends.get("trace") or load.get("trace") or (self.ends.get("setup") or {}).get("trace")
        score = None
        if self.mode == "replay" and not self.stopped and not crash and self.lines:
            first = next((i for i, x in enumerate(self.lines) if x.startswith("id ")), 0)
            score = {"lines": self.lines[first:], "points": (summary or {}).get("totals", {}).get("points"), "max": row.get("max"),
                     "request_points": row.get("points"), "penalty": row.get("penalty", row.get("set_penalty", 0))}
        setup = self.ends.get("setup") or {}
        result = {"kind": kind, "status": status, "stop": stop or None, "trace": clean_text(trace, 20000) if trace else None,
                  "stdout": clean_text(self.ends.get("stdout") or "", 20000), "row": row or None, "score": score,
                  "setup": setup.get("status"), "log": str(self.out / "log.jsonl"), "end": now_ms(),
                  "claims": sum(1 for c in self.claims if not c.get("no_claim")), "questions": row.get("questions"),
                  "limit": row.get("limit"), "noData": [clean_text(x, 2000) for x in self.no_data[:3]]}
        if self.mode == "replay" and not crash:
            result["key"] = key_compare(self.key, self.ends.get("grade"), self.filings, row)
        self.emit(self.status(), {"type": "data-result", "id": "result", "data": result})
        self.close_segment()
        self.hub.changed()


MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()


def practice_label(r, width=70):
    """The label of a practice request in the page's list: its id, its travel day and the start of its longest line,
    e.g. "P016, 21 Oct: Hier, j'ai voyagé de Paris Gare de Lyon à Genève Cornavin avec le Lyria de 6h16…". The
    longest line is the one about the trip, not a greeting or a name."""
    lines = [x.strip() for x in r["text"].splitlines() if x.strip()][1:]  # without the date line
    body = max(lines, key=len) if lines else r["text"].strip()
    if len(body) > width:
        body = body[:width].rsplit(" ", 1)[0].rstrip(" ,.;:") + "…"
    day = str((r.get("answer") or {}).get("day") or "")
    try:
        when = f", {int(day[8:10])} {MONTHS[int(day[5:7]) - 1]}"
    except (ValueError, IndexError):
        when = ""
    return f"{r['id']}{when}: {body}"


class Hub:
    """The state of one chat.py process: the settings, the history and the current run."""

    def __init__(self, *, system, gateway, data, rule_dirs, form_dirs, out, practice, timeout_s=600, sandbox=False,
                 questions=6, say=print, packages=(), trains=None):
        self.system, self.gateway, self.data = system, gateway, data
        self.rule_dirs, self.form_dirs, self.out = rule_dirs, form_dirs, Path(out)
        self.practice = {r["id"]: r for r in practice}
        self.timeout_s, self.sandbox, self.questions = timeout_s, sandbox, questions
        self.packages = packages  # with the sandbox: the package mounts of isolation.team_packages
        self.desk = FormDesk(form_dirs)  # the forms' companies and slots, for key_compare
        self.say = say
        self.cond = threading.Condition()
        self.history = []        # finished requests: (user message, assistant message)
        self.run = None          # the current or last run
        self.number = 0
        self.version = 0
        self.budget_left = None
        self.token = secrets.token_urlsafe(24)
        self.client_ids = {}     # the page's id of a request -> our run, against double sends
        # One cache folder for every request; setup(tools) runs again when anything it can read changes.
        inputs = [*rule_dirs, *form_dirs, *([trains] if trains else []), *starter_inputs()]
        self.setup_cache = SetupCache(system, inputs, skip=[self.out, *starter_skip()])
        self.setup = {"state": "waiting", "reason": None, "t": now_ms()}
        self.trains = os.path.realpath(trains) if trains else None  # the store's file, which data has open
        self.trains_sum = self.setup_cache.file_sum(self.trains) if trains else None

    def fresh_data(self):
        """Open the train store again if its file has changed since we opened it, so that the searches of the next
        request use the new data (between requests, with the lock held). Returns an error text or None."""
        if self.trains is None:
            return None
        now = self.setup_cache.file_sum(self.trains)
        if now == self.trains_sum:
            return None
        try:
            data = TrainData(self.trains)
        except Exception as e:  # e.g. make_store.py is writing it
            return f"We can't open the train store {self.trains} again ({type(e).__name__}: {e}). Try again in a moment."
        old, self.data, self.trains_sum = self.data, data, now
        old.db.close()
        self.say(f"The train store changed, so we opened it again: {self.trains}")
        return None

    def set_setup(self, state, reason):
        """waiting (no request yet), running, ran, reused or stopped, and why setup runs or ran (with the lock held)."""
        self.setup = {"state": state, "reason": reason, "t": now_ms()}

    def setup_view(self):
        """What the page's header shows about setup(tools): the state, the last setup's status and time, and whether
        it runs again with the next request."""
        last = self.setup_cache.last or {}
        return {**self.setup, "status": last.get("status"),
                "at": int(last["at"] * 1000) if last.get("at") else None, "rebuild": self.setup_cache.forced}

    def rebuild(self):
        """The Rebuild index button (with the lock held): setup(tools) runs again with the next request."""
        self.setup_cache.rebuild()
        self.setup = {**self.setup, "t": now_ms()}
        self.changed()

    def close(self):
        """Delete the cache folder, when chat.py ends."""
        self.setup_cache.close()

    def changed(self):
        self.version += 1
        self.cond.notify_all()

    def busy(self):
        return self.run is not None and self.run.thread.is_alive()

    def start(self, body):
        """A new request from the page. Returns (run, None) or (None, error text)."""
        cid = str(body.get("clientId") or "")[:64]
        if cid and cid in self.client_ids:
            return None, "We already got this request."
        if self.busy():
            return None, ("We are still stopping the last request. Try again in a moment." if self.run.final
                          else "A request is running. Wait for it to end, or stop it.")
        mode = "replay" if body.get("mode") == "replay" else "free"
        replay = None
        if mode == "replay":
            replay = self.practice.get(str(body.get("replayId")))
            if replay is None:
                return None, "Pick a practice request."
            text = replay["text"]
        else:
            text = clean_text(body.get("text") or "", TEXT_MAX + 1)
            if not text.strip():
                return None, "Type a request first."
            if len(text) > TEXT_MAX:
                return None, f"The request is too long. The limit is {TEXT_MAX:,} characters."
        problem = self.fresh_data()
        if problem:
            return None, problem
        if self.run is not None:
            self.history.append((self.run.user, self.run.message))
            del self.history[:-HISTORY_MAX]
        self.number += 1
        run = Run(self, self.number, text, mode, replay, auto=bool(body.get("auto", True)))
        if cid:
            self.client_ids[cid] = run.number
        self.run = run
        run.open_segment()
        run.emit(run.status())
        self.changed()
        run.thread.start()
        return run, None

    def run_ended(self, run):
        self.changed()

    def state(self):
        """What a page needs after a reload. The current run's assistant message comes as a replayed stream."""
        msgs = [m for pair in self.history for m in pair]
        run = self.run
        if run is not None:
            msgs.append(run.user)
        return {"version": self.version, "messages": msgs,
                "run": None if run is None else {"state": run.state, "messageId": run.message_id,
                                                 "busy": run.thread.is_alive(), "segments": run.segments},
                "system": Path(self.system).name, "cap": self.gateway.max_cost, "spent": round(self.gateway.spent, 6),
                "budgetLeft": self.budget_left, "questions": self.questions, "timeout": self.timeout_s,
                "maxTools": TOOL_BUDGET, "maxModel": MODEL_BUDGET, "topics": TOPICS, "setup": self.setup_view(),
                "days": list(getattr(self.data, "days", [])),
                "practice": [{"id": r["id"], "text": r["text"], "limit": r["traveler"]["limit"],
                              "label": practice_label(r)} for r in self.practice.values()],
                "modelProblem": self.gateway.settings_problem()}


def sse(chunk):
    if chunk == "[DONE]":
        return b"data: [DONE]\n\n"
    return b"data: " + json.dumps(chunk, separators=(",", ":")).encode() + b"\n\n"


class Handler(BaseHTTPRequestHandler):
    hub = None
    server_version = "p2-chat"
    sys_version = ""

    def log_message(self, *args):
        pass

    # ---- checks ----------------------------------------------------------------------------------------------
    def _host_ok(self):
        port = self.server.server_address[1]
        return self.headers.get("Host", "") in (f"127.0.0.1:{port}", f"localhost:{port}")

    def _origin_ok(self):
        origin = self.headers.get("Origin")
        port = self.server.server_address[1]
        return origin is None or origin in (f"http://127.0.0.1:{port}", f"http://localhost:{port}")

    def _token_ok(self):
        return hmac.compare_digest(self.headers.get("X-P2-Chat-Token", "").encode(), self.hub.token.encode())

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _error(self, code, text):
        self._json(code, {"error": text})

    def _security_headers(self):
        self.send_header("Content-Security-Policy", "default-src 'self'; connect-src 'self'; img-src 'self' data:; "
                         "style-src 'self' 'unsafe-inline'; script-src 'self'; font-src 'self'; base-uri 'none'; "
                         "form-action 'none'; frame-ancestors 'none'")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")

    # ---- GET -------------------------------------------------------------------------------------------------
    def do_GET(self):
        if not self._host_ok():
            return self._error(403, "wrong host")
        path = urlsplit(self.path).path
        if path.startswith("/api/"):
            if not self._token_ok():
                return self._error(403, "Open the address from the terminal where you started chat.py.")
            if path == "/api/state":
                with self.hub.cond:
                    return self._json(200, self.hub.state())
            m = re.fullmatch(r"/api/chat/([\w.-]+)/stream", path)
            if m:
                return self._resume()
            return self._error(404, "not found")
        return self._static(path)

    def _static(self, path):
        if path in ("", "/"):
            path = "/index.html"
        target = (STATIC / path.lstrip("/")).resolve()
        if STATIC.resolve() not in target.parents or not target.is_file():
            target = STATIC / "index.html"
        if not target.is_file():
            return self._error(500, "chat_ui/static/index.html is missing")
        body = target.read_bytes()
        ctype = {".js": "text/javascript", ".css": "text/css", ".html": "text/html", ".svg": "image/svg+xml",
                 ".json": "application/json"}.get(target.suffix) or mimetypes.guess_type(target.name)[0] \
            or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith("text/") else ""))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store" if target.name == "index.html" else "max-age=3600")
        self._security_headers()
        self.end_headers()
        self.wfile.write(body)

    # ---- POST ------------------------------------------------------------------------------------------------
    def do_POST(self):
        if not self._host_ok() or not self._origin_ok():
            return self._error(403, "wrong host or origin")
        if not self._token_ok():
            return self._error(403, "Open the address from the terminal where you started chat.py.")
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return self._error(400, "bad length")
        if n > BODY_MAX:
            return self._error(413, "The message is too long.")
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
            if not isinstance(body, dict):
                raise ValueError
        except ValueError:
            return self._error(400, "We can't read this message.")
        path = urlsplit(self.path).path
        hub = self.hub
        if path == "/api/chat":
            last = hub.run
            if body.get("kind") != "answer" and last is not None and last.stopped:
                last.thread.join(timeout=STOP_WAIT_S)  # a stopped request ends within a second or so
            with hub.cond:
                if body.get("kind") == "answer":
                    run = hub.run
                    err = "We aren't waiting for an answer now." if run is None else \
                        run.answer(str(body.get("toolCallId")), str(body.get("answer") or ""))
                    if err:
                        return self._error(409, err)
                    start = max(i for i, c in enumerate(run.chunks) if c["type"] == "start")
                else:
                    run, err = hub.start(body)
                    if err:
                        return self._error(409, err)
                    start = 0
            return self._stream(run, start)
        if path == "/api/rebuild":
            with hub.cond:
                hub.rebuild()
                return self._json(200, {"ok": True, "setup": hub.setup_view()})
        if path == "/api/stop":
            with hub.cond:
                if hub.run is not None and not hub.run.final:
                    hub.run.stop()
            return self._json(200, {"ok": True})
        return self._error(404, "not found")

    # ---- streams ---------------------------------------------------------------------------------------------
    def _begin_stream(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("x-vercel-ai-ui-message-stream", "v1")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

    def _resume(self):
        """The page reloaded: replay the current request's message from its start, then follow it live."""
        hub = self.hub
        with hub.cond:
            run = hub.run
            if run is None:
                self.send_response(204)
                self.end_headers()
                return
            first = [{"type": "start", "messageId": run.message_id}]
            first += [c for c in run.chunks if c["type"] not in ("start", "finish", "_close")]
            tail = len(run.chunks)
            live = run.state == "running"
        self._begin_stream()
        try:
            for c in first:
                self.wfile.write(sse(c))
            if not live:
                self.wfile.write(sse({"type": "finish"}) + sse("[DONE]"))
                return
            self._follow(run, tail)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass

    def _stream(self, run, start):
        self._begin_stream()
        try:
            self._follow(run, start)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass

    def _follow(self, run, i):
        hub = self.hub
        while True:
            with hub.cond:
                if i >= len(run.chunks):
                    hub.cond.wait(timeout=PING_S)
                batch = run.chunks[i:]
            if not batch:
                self.wfile.write(b": ping\n\n")
                self.wfile.flush()
                continue
            out = []
            for c in batch:
                if c["type"] == "_close":
                    out.append(sse("[DONE]"))
                    self.wfile.write(b"".join(out))
                    self.wfile.flush()
                    return
                out.append(sse(c))
            self.wfile.write(b"".join(out))
            self.wfile.flush()
            i += len(batch)


def serve(hub, port=0):
    """A started server on 127.0.0.1 (port 0: any free port). Returns (server, thread)."""
    handler = type("BoundHandler", (Handler,), {"hub": hub})
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    server.daemon_threads = True
    t = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.5}, daemon=True)
    t.start()
    return server, t
