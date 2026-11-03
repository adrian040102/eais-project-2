"""The team's process. The runner starts it once per run, and it runs the team's system there:

1. It loads the system file and tells the runner whether the system has `setup(tools)`.
2. On "setup", it calls `setup(tools)` once; on "solve", it calls `solve(request_text, tools)`.
3. Every method of `tools` sends one JSON line to the runner over the socket and waits for one JSON line back.

The runner starts a fresh process for each invocation, i.e. setup() once per run and solve() once per request, and
kills it, with every thread and process in it, when the invocation is done. Each invocation has a random id from the
runner. We tag the calls of its `tools` object with that id and refuse calls after the invocation returns; the
runner answers only calls with the id of the active invocation. `tools.cache_dir` is the run's cache folder, which
setup() can write and solve() can only read.

The runner (p2kit/isolation.py) holds the tools, the traveler's facts, the train store, the forms, the rule documents,
the model key or token, the counters and the log. This process holds none of them, so the team's code can't reach
them. It imports only the standard library and p2kit.errors.

Messages, one JSON object per line:
  runner -> child  {"op": "init", "topics": {...}}, {"op": "setup", "id": ID}, {"op": "solve", "id": ID, "text": "..."},
                   {"op": "exit"}, and for each call {"op": "result", "id": ID, "value": ...}
                   or {"op": "raise", "id": ID, "kind": "...", "message": "..."}
  child -> runner  {"op": "hello"}, {"op": "loaded", "ok": true, "setup": true},
                   {"op": "call", "id": ID, "name": "...", "args": [...], "kwargs": {...}},
                   {"op": "done", "id": ID, "error": null, "trace": null, "stdout": "..."}
"""
import contextlib
import importlib.util
import io
import json
import socket
import sys
import threading
import traceback
from pathlib import Path

from .errors import STOPS

OUTPUT_KEPT = 20000  # characters of a request's printed output that we send to the runner's log
RAISES = {"TypeError": TypeError, "PermissionError": PermissionError, **STOPS}


class Channel:
    def __init__(self, sock):
        self._r = sock.makefile("rb")
        self._w = sock.makefile("wb")
        self.lock = threading.Lock()  # one call (send and answer) at a time, and no call while "done" goes out

    def send(self, msg):
        self._w.write(json.dumps(msg, ensure_ascii=False).encode() + b"\n")
        self._w.flush()

    def recv(self):
        line = self._r.readline()
        if not line:
            raise EOFError("the runner closed the connection")
        return json.loads(line)


class ToolsProxy:
    """The `tools` object of one setup() or solve() call. Each method asks the runner, which runs the tool, counts
    the call and logs it, and returns only the result. The stop exceptions (BudgetExceeded, TimeLimit, CostLimit,
    ModelAccessError) come back as the same exceptions. After its setup() or solve() returns, the object refuses
    every call."""

    def __init__(self, channel, topics, invocation, cache_dir):
        self._channel = channel
        self._id = invocation
        self._active = True
        self.topics = dict(topics)
        self.cache_dir = cache_dir

    def _ask(self, name, *args, **kwargs):
        msg = {"op": "call", "id": self._id, "name": name, "args": list(args), "kwargs": kwargs}
        try:
            json.dumps(msg)
        except (TypeError, ValueError, RecursionError) as e:
            msg = {"op": "call", "id": self._id, "name": name,
                   "bad": f"arguments must be JSON values (dicts, lists, text, numbers): {type(e).__name__}"}
        with self._channel.lock:  # the check and the send under one lock, so no call can follow "done"
            if not self._active:
                raise RuntimeError("this tools object belongs to a setup() or solve() call that has returned; "
                                   "use the tools object of the current call")
            self._channel.send(msg)
            reply = self._channel.recv()
        if reply.get("id") != self._id:
            raise RuntimeError("the runner's answer belongs to another call")
        if reply.get("op") == "result":
            return reply.get("value")
        raise RAISES.get(reply.get("kind"), RuntimeError)(reply.get("message", ""))

    # tools, counted
    def ask_traveler(self, topic):
        return self._ask("ask_traveler", topic)

    def list_forms(self):
        return self._ask("list_forms")

    def get_form(self, company):
        return self._ask("get_form", company)

    def file_claim(self, company, fields):
        return self._ask("file_claim", company, fields)

    def no_claim(self, company):
        return self._ask("no_claim", company)

    def departures(self, station, date, from_time, to_time):
        return self._ask("departures", station, date, from_time, to_time)

    def find_trains(self, origin, destination, date, from_time, to_time):
        return self._ask("find_trains", origin, destination, date, from_time, to_time)

    def train_stops(self, train, date):
        return self._ask("train_stops", train, date)

    # model calls, counted
    def llm(self, messages, model="gpt-4o-mini", **options):
        return self._ask("llm", messages, model=model, **options)

    # free
    def list_rule_documents(self, company=None):
        return self._ask("list_rule_documents", company)

    def read_rule_document(self, doc_id):
        return self._ask("read_rule_document", doc_id)

    def budget(self):
        return self._ask("budget")


def load_system(path):
    spec = importlib.util.spec_from_file_location("team_system", path)
    if spec is None:
        raise ImportError(f"can't load {path}: give the path of a .py file")
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(Path(path).resolve().parent))
    spec.loader.exec_module(mod)
    if not callable(getattr(mod, "solve", None)):
        raise AttributeError(f"{path} has no solve(request_text, tools)")
    return mod


def main(fd, system_path):
    ch = Channel(socket.socket(fileno=fd))
    init = ch.recv()
    ch.send({"op": "hello"})
    try:
        system = load_system(system_path)
    except BaseException as e:
        ch.send({"op": "loaded", "ok": False, "error": type(e).__name__, "trace": traceback.format_exc()})
        return
    topics, cache_dir = init.get("topics") or {}, init.get("cache")
    ch.send({"op": "loaded", "ok": True, "setup": callable(getattr(system, "setup", None))})
    while True:
        msg = ch.recv()
        tools = ToolsProxy(ch, topics, msg.get("id"), cache_dir)
        if msg.get("op") == "setup":
            run = lambda: system.setup(tools)  # noqa: E731
        elif msg.get("op") == "solve":
            run = lambda: system.solve(msg.get("text", ""), tools)  # noqa: E731
        else:
            return
        buf, error, trace = io.StringIO(), None, None
        try:
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                run()
        except BaseException as e:  # the stops too: the runner already knows them
            error, trace = type(e).__name__, traceback.format_exc()
        with ch.lock:
            tools._active = False
            ch.send({"op": "done", "id": msg.get("id"), "error": error, "trace": trace,
                     "stdout": buf.getvalue()[-OUTPUT_KEPT:]})
