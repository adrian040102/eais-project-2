"""The tools object passed to solve(request_text, tools): the tools, the budgets and the log of one request.

Per request a system has 20 tool calls and 15 model calls:

- tools (each call counts, also a failed call or a rejected filing): ask_traveler, list_forms, get_form,
  file_claim, no_claim, departures, find_trains, train_stops;
- model calls: llm.

Free, not counted: list_rule_documents and read_rule_document (the rule documents, for the team's own retrieval),
budget() and topics.

The runner keeps this object in its own process and reads tool_calls, model_calls and events after the request.
The team's system runs in other processes, one per setup() or solve(), with a proxy that has the same methods
(p2kit/child.py) and sends each call here (p2kit/isolation.py). In setup(), only the rule documents and budget work.
"""
import json
import time
import traceback

from .data import ToolError
from .errors import BudgetExceeded, TimeLimit  # noqa: F401 (re-exported for the runners)
from .traveler import TOPICS

TOOL_BUDGET, MODEL_BUDGET = 20, 15


# The longest argument that we accept, in characters (for fields, of its JSON). A longer one gets an error and counts
# as a call; it would only slow down our side, e.g., a train name of thousands of spaces.
ARG_LIMITS = {"station": 200, "origin": 200, "destination": 200, "date": 40, "from_time": 40, "to_time": 40,
              "train": 100, "company": 200, "topic": 200, "doc_id": 300, "fields": 20000}
REJECTS = ("file_claim", "no_claim")  # the tools that answer a problem with {"status": "rejected", "errors": [...]}


def arg_length(v):
    """The length of an argument: of a string, or of the JSON of anything else, as the student wrote it (an accented
    letter counts once)."""
    return len(v) if isinstance(v, str) else len(json.dumps(v, ensure_ascii=False, default=str))


def arg_problem(args):
    """The error for the first argument that is longer than ARG_LIMITS allows, or None."""
    for k, v in args.items():
        limit = ARG_LIMITS.get(k)
        if limit is None or v is None:
            continue
        n = arg_length(v)
        if n > limit:
            return f"{k} is too long: {n:,} characters, and we accept at most {limit:,}"
    return None


def short_args(args):
    """The arguments for the log, with each one that is too long replaced by its length."""
    return {k: (f"<{arg_length(v):,} characters>"
                if k in ARG_LIMITS and v is not None and arg_problem({k: v}) else v) for k, v in args.items()}


TOOL_NAMES = ["ask_traveler", "list_forms", "get_form", "file_claim", "no_claim", "departures", "find_trains", "train_stops"]


class Tools:
    def __init__(self, req_id, data, rules, forms, traveler, gateway, logger, deadline, on_start=None):
        """on_start(event): called when a counted call starts, before it runs (chat.py shows calls in progress).
        The practice runner and the grader leave it None."""
        self._req_id, self._data, self._rules, self._forms = req_id, data, rules, forms
        self._traveler, self._gateway, self._log, self._deadline = traveler, gateway, logger, deadline
        self._on_start = on_start
        self.paused_s = 0.0  # seconds a person took to answer as the traveler (chat.py only); they don't count
        self._tool_calls = 0
        self._model_calls = 0
        self._events = []
        self._runs = {}  # train_id -> run (TrainData.run) of every train row that a train tool returned in this request
        self.topics = dict(TOPICS)

    # ---- for the runner (read only) ----------------------------------------------------------------------
    @property
    def tool_calls(self):
        return self._tool_calls

    @property
    def model_calls(self):
        return self._model_calls

    @property
    def events(self):
        return list(self._events)

    # ---- bookkeeping -------------------------------------------------------------------------------------
    def _check_time(self):
        if time.time() > self._deadline:
            raise TimeLimit("time limit for this request reached")

    def _tick(self, kind):
        self._check_time()
        if kind == "tool":
            if self._tool_calls >= TOOL_BUDGET:
                raise BudgetExceeded(f"tool call budget of {TOOL_BUDGET} used up")
            self._tool_calls += 1
        else:
            if self._model_calls >= MODEL_BUDGET:
                raise BudgetExceeded(f"model call budget of {MODEL_BUDGET} used up")
            self._model_calls += 1

    def _record(self, ev):
        ev = {"req": self._req_id, "t": round(time.time(), 3), "n": len(self._events) + 1, **ev}
        self._events.append(ev)
        self._log(ev)
        return ev

    def _run(self, name, fn, args):
        """Count the call, run it and log it. A tool never raises at the system: problems come back as
        {"error": "..."}."""
        self._tick("tool")
        problem = arg_problem(args)
        if problem:
            res = {"status": "rejected", "errors": [problem]} if name in REJECTS else {"error": problem}
            self._record({"kind": "tool", "name": name, "args": short_args(args), "result": res})
            return res
        if self._on_start is not None:
            self._on_start({"kind": "tool", "name": name, "args": args, "n": len(self._events) + 1})
        trace = None
        try:
            res = fn()
        except ToolError as e:
            res = {"error": str(e)}
        except Exception as e:  # a bug of ours, or an argument we didn't foresee
            res = {"error": f"the tool failed on these arguments: {type(e).__name__}: {e}"}
            trace = traceback.format_exc()
        self._record({"kind": "tool", "name": name, "args": args, "result": res, **({"trace": trace} if trace else {})})
        return res

    def refuse(self, name, reason):
        """A call that the system can't make here, e.g. a tool in setup(): logged, not counted."""
        self._check_time()
        self._record({"kind": "refused", "name": name, "result": {"error": reason}})
        return {"error": reason}

    def bad_arguments(self, name, reason):
        """A call whose arguments couldn't reach the runner (not JSON). It counts like the call it was meant to be."""
        if name == "llm":
            self._tick("model")
        elif name in TOOL_NAMES:
            self._tick("tool")
        else:
            self._check_time()
        self._record({"kind": "llm" if name == "llm" else "tool", "name": name, "args": None, "result": {"error": reason}})
        return {"error": reason}

    def budget(self):
        """Calls left for this request (free)."""
        return {"tool_calls_left": TOOL_BUDGET - self._tool_calls, "model_calls_left": MODEL_BUDGET - self._model_calls}

    # ---- rule documents (free) ---------------------------------------------------------------------------
    def list_rule_documents(self, company=None):
        """{"documents": [{doc_id, company, title, language, chars}, ...]} (free)."""
        self._check_time()
        problem = arg_problem({"company": company})
        if problem:
            self._record({"kind": "doc", "name": "list_rule_documents", "args": short_args({"company": company}),
                          "result": {"error": problem}})
            return {"error": problem}
        res = self._rules.list_rule_documents(company, self._forms.names() if self._forms else None)
        self._record({"kind": "doc", "name": "list_rule_documents", "args": {"company": company},
                      "result": {"documents": len(res.get("documents", []))} if "documents" in res else res})
        return res

    def read_rule_document(self, doc_id):
        """The full text of one document: {doc_id, company, title, language, text} (free)."""
        self._check_time()
        problem = arg_problem({"doc_id": doc_id})
        if problem:
            self._record({"kind": "doc", "name": "read_rule_document", "args": short_args({"doc_id": doc_id}),
                          "result": {"error": problem}})
            return {"error": problem}
        res = self._rules.read_rule_document(doc_id)
        self._record({"kind": "doc", "name": "read_rule_document", "args": {"doc_id": doc_id},
                      "result": {"chars": len(res["text"])} if "text" in res else res})
        return res

    # ---- tools (counted) ---------------------------------------------------------------------------------
    def ask_traveler(self, topic):
        if not getattr(self._traveler, "pauses_clock", False):
            return self._run("ask_traveler", lambda: self._traveler.ask(topic), {"topic": topic})
        # In chat.py a person answers. The time they take to type doesn't count against the request's time limit.
        t0 = time.time()
        try:
            return self._run("ask_traveler", lambda: self._traveler.ask(topic), {"topic": topic})
        finally:
            waited = time.time() - t0
            self._deadline += waited
            self.paused_s += waited

    def list_forms(self):
        return self._run("list_forms", self._forms.list_forms, {})

    def get_form(self, company):
        return self._run("get_form", lambda: self._forms.get_form(company), {"company": company})

    def file_claim(self, company, fields):
        """The form's train field must name a train run that a train tool returned in this request (forms.py)."""
        n = len(self._forms.claims)
        res = self._run("file_claim", lambda: self._forms.file_claim(company, fields, list(self._runs.values())),
                        {"company": company, "fields": fields})
        if res.get("status") == "accepted" and len(self._forms.claims) > n:
            self._events[-1]["accepted"] = self._forms.claims[-1]
        return res

    def no_claim(self, company):
        """The answer that nothing is owed to a company for this request."""
        n = len(self._forms.no_claims)
        res = self._run("no_claim", lambda: self._forms.no_claim(company), {"company": company})
        if res.get("status") == "recorded" and len(self._forms.no_claims) > n:
            self._events[-1]["declared"] = self._forms.no_claims[-1]
        return res

    def _train_tool(self, name, fn, args):
        """A train tool call, and the runs of its train rows, which the claim forms accept in the train field."""
        res = self._run(name, fn, args)
        if isinstance(res, dict):
            for row in res.get("departures") or res.get("trains") or []:
                run = self._data.run(row.get("train_id")) if isinstance(row, dict) else None
                if run:
                    self._runs[run["train_id"]] = run
        return res

    def departures(self, station, date, from_time, to_time):
        return self._train_tool("departures", lambda: self._data.departures(station, date, from_time, to_time),
                                {"station": station, "date": date, "from_time": from_time, "to_time": to_time})

    def find_trains(self, origin, destination, date, from_time, to_time):
        return self._train_tool("find_trains", lambda: self._data.find_trains(origin, destination, date, from_time, to_time),
                                {"origin": origin, "destination": destination, "date": date, "from_time": from_time,
                                 "to_time": to_time})

    def train_stops(self, train, date):
        return self._train_tool("train_stops", lambda: self._data.train_stops(train, date), {"train": train, "date": date})

    # ---- model calls (counted) ---------------------------------------------------------------------------
    def llm(self, messages, model="gpt-4o-mini", **options):
        """A chat completion through the course app (or, in grading, OpenAI directly), as a dict, or
        {"error": "..."}. Options as in OpenAI's chat completions: tools, tool_choice, parallel_tool_calls,
        response_format, max_tokens, temperature, top_p, seed, reasoning_effort ("low" only)."""
        self._tick("model")
        if self._on_start is not None:
            self._on_start({"kind": "llm", "name": "llm", "model": model, "n": len(self._events) + 1})
        res = self._gateway.call(messages, model=model, **options)
        rec = self._gateway.calls[-1] if self._gateway.calls else {}
        self._record({"kind": "llm", "model": model, "usage": rec, "messages": messages,
                      "options": {k: v for k, v in options.items() if k != "tools"},
                      "tools": [t.get("function", {}).get("name") for t in options.get("tools") or [] if isinstance(t, dict)],
                      "result": res})
        return res
