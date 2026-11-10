"""Model calls for Project 2: two models, two ways to reach them, token and cost logging, and a cost cap per run.

Every model call of a system goes through `tools.llm(...)`, which calls `ModelGateway.call`. The gateway has two
backends with the same request and answer:

- "course": the course app's endpoint `POST <P2_BASE_URL>/chat/completions` with the team's token
  (`Authorization: Bearer <P2_TOKEN>`). The course app holds the OpenAI key and charges the team's budget.
- "openai": OpenAI directly, with the course key in OPENAI_API_KEY, for the staff's grading runs.

Both take the chat completions request shape and return a chat completion dict. gpt-6-luna always reasons at the
effort "low". OpenAI's chat completions endpoint refuses function tools while gpt-6-luna reasons, so the "openai"
backend sends gpt-6-luna calls with tools to the Responses API and turns the answer back into a chat completion, as
the course app does. The assistant message of such an answer carries `encrypted_reasoning`; a system sends that
message back unchanged, and the next call hands the model its own reasoning.

The request rules, the prices and the reasoning encoding follow the course app (docs/P2-MODEL-ACCESS.md in the app's
repository), so that a system gets the same answers and refusals from both backends.
"""
import base64
import json
import os
import re
import ssl
import time
import urllib.error
import urllib.request

from .errors import CostLimit, ModelAccessError

MODELS = {  # name -> (model we send to OpenAI, reasoning effort we always set)
    "gpt-4o-mini": ("gpt-4o-mini-2024-07-18", None),
    "gpt-6-luna": ("gpt-6-luna", "low"),
}
ALIASES = {"gpt-4o-mini": "gpt-4o-mini", "gpt-6-luna": "gpt-6-luna"}  # exact names only, as the course app takes them

# USD per million tokens, as in the course app's src/modelPrices.ts (OpenAI's standard prices on 23 October 2026).
# A cache write is input that OpenAI writes to its prompt cache; gpt-4o-mini charges no extra for it.
PRICES = {
    "gpt-4o-mini": {"input": 0.15, "cached": 0.075, "cache_write": 0.15, "output": 0.60},
    "gpt-6-luna": {"input": 0.10, "cached": 0.01, "cache_write": 0.125, "output": 0.50,
                   "long_input": (272_000, 2.0, 1.5)},  # from this many input tokens: input x2, output x1.5
}
MAX_OUT_DEFAULT, MAX_OUT_CAP = 1500, 4000
MAX_CHARS = 200_000          # messages with their encrypted reasoning, tools and response format together
RESPONSES_MIN_OUT = 16       # the Responses API refuses fewer output tokens
OPTIONS = {"tools", "tool_choice", "parallel_tool_calls", "response_format", "max_tokens", "max_completion_tokens",
           "temperature", "top_p", "seed", "reasoning_effort", "stream"}
OPENAI_URL = "https://api.openai.com/v1"
REASONING_PREFIX = "r1."
COURSE_WAITS = (2, 4, 8, 16, 30)  # seconds between attempts at the course app, about a minute in all
OPENAI_WAITS = (1, 4)             # seconds between attempts at OpenAI, three attempts in all
OVERHEAD_TOKENS = 1000            # input tokens OpenAI may add beyond the call's bytes (the course app's margin)


# ---- the request ---------------------------------------------------------------------------------------------

def _tool_problem(tools):
    if not isinstance(tools, list):
        return "tools must be a list of function tools"
    for i, t in enumerate(tools):
        fn = t.get("function") if isinstance(t, dict) else None
        if not isinstance(t, dict) or t.get("type") != "function" or not isinstance(fn, dict) \
                or not isinstance(fn.get("name"), str) or not fn["name"]:
            return (f'tools[{i}] must be {{"type": "function", '
                    '"function": {"name": ..., "description": ..., "parameters": ...}}')
    return None


def check_request(model, options):
    """(model name, None), or (None, error text) for a request the course app would refuse. Same rules for both
    backends."""
    name = ALIASES.get(str(model))
    if name is None:
        return None, f"model must be gpt-4o-mini or gpt-6-luna, not {model!r}"
    unknown = sorted(set(options) - OPTIONS)
    if unknown:
        return None, f"we don't accept the option(s) {', '.join(unknown)}; allowed: {', '.join(sorted(OPTIONS))}"
    if options.get("reasoning_effort") not in (None, "low"):
        return None, 'reasoning_effort must be "low" or left out; gpt-6-luna always reasons at "low"'
    if options.get("max_tokens") is not None and options.get("max_completion_tokens") is not None:
        return None, "send max_tokens or max_completion_tokens, not both"
    out = options.get("max_completion_tokens", options.get("max_tokens"))
    if out is not None and (isinstance(out, bool) or not isinstance(out, int) or not 1 <= out <= MAX_OUT_CAP):
        return None, f"max_tokens must be a whole number from 1 to {MAX_OUT_CAP}"
    if options.get("stream") not in (None, False):
        return None, "stream must be false or left out"
    if options.get("tools") is not None:
        problem = _tool_problem(options["tools"])
        if problem:
            return None, problem
    tc = options.get("tool_choice")
    if tc is not None and tc not in ("none", "auto", "required") and not (
            isinstance(tc, dict) and tc.get("type") == "function" and isinstance(tc.get("function"), dict)
            and isinstance(tc["function"].get("name"), str)):
        return None, 'tool_choice must be "none", "auto", "required" or {"type": "function", "function": {"name": ...}}'
    rf = options.get("response_format")
    if rf is not None and not (isinstance(rf, dict) and rf.get("type") in ("text", "json_object", "json_schema")
                               and (rf["type"] != "json_schema" or isinstance(rf.get("json_schema"), dict))):
        return None, ('response_format must be {"type": "text"}, {"type": "json_object"} or '
                      '{"type": "json_schema", "json_schema": {...}}')
    return name, None


def max_tokens_of(options):
    return options.get("max_completion_tokens") or options.get("max_tokens") or MAX_OUT_DEFAULT


def _compact(v):
    return json.dumps(v, ensure_ascii=False, separators=(",", ":"))


_CONTROL = re.compile("[\x00-\x07\x0b\x0e-\x1f]")  # control characters that JSON writes as \u00XX
_SURROGATE = re.compile("[\ud800-\udfff]")


def _js_number(x):
    """A JSON number as JavaScript's JSON.stringify writes it after JSON.parse made it a double."""
    try:
        x = float(x)
    except OverflowError:
        return "null"
    if x != x or x in (float("inf"), float("-inf")):
        return "null"
    if x == 0:
        return "0"
    mant, _, exp = repr(abs(x)).partition("e")  # the shortest digits that round-trip, as in JavaScript
    whole, _, frac = mant.partition(".")
    all_digits = whole + frac
    digits = all_digits.strip("0")
    k, n = len(digits), len(whole) + int(exp or 0) - (len(all_digits) - len(all_digits.lstrip("0")))
    if k <= n <= 21:
        text = digits + "0" * (n - k)
    elif 0 < n <= 21:
        text = digits[:n] + "." + digits[n:]
    elif -6 < n <= 0:
        text = "0." + "0" * -n + digits
    else:
        text = digits[0] + ("." + digits[1:] if k > 1 else "") + "e" + ("+" if n > 0 else "-") + str(abs(n - 1))
    return ("-" if x < 0 else "") + text


def _js_len(v):
    """Length of JSON.stringify(v) in JavaScript, for a value as json.loads gives it."""
    if v is None or v is True:
        return 4
    if v is False:
        return 5
    if isinstance(v, str):
        # quotes, UTF-16 code units, one more for each two-character escape, five more for each \u escape
        return 2 + len(v.encode("utf-16-le", "surrogatepass")) // 2 + sum(map(v.count, '"\\\b\f\n\r\t')) + \
            5 * (len(_CONTROL.findall(v)) + len(_SURROGATE.findall(v)))
    if isinstance(v, (int, float)):
        return len(_js_number(v))
    if isinstance(v, list):
        return 1 + len(v) + sum(map(_js_len, v)) if v else 2
    return 1 + len(v) + sum(_js_len(key) + 1 + _js_len(value) for key, value in v.items()) if v else 2


def _join_pairs(v):
    """v with each high and low surrogate pair in its text joined into one character, as JSON.parse reads it."""
    if isinstance(v, str):
        return v.encode("utf-16-le", "surrogatepass").decode("utf-16-le", "surrogatepass")
    if isinstance(v, dict):
        return {_join_pairs(k): _join_pairs(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_join_pairs(x) for x in v]
    return v


def _chars(messages, options):
    """Characters of the call as the course app counts them: JSON.stringify of the messages with their encrypted
    reasoning, the tools and the response format, in UTF-16 code units, of the call as the app parses it from our
    request. Raises TypeError or ValueError for values that aren't JSON."""
    sent = json.loads(json.dumps([messages, options.get("tools") or [], options.get("response_format")]))
    return sum(map(_js_len, sent))


def estimate_usd(name, messages, options):
    """A safe upper bound of what a call can cost, which we reserve before the call.

    Every token of OpenAI's tokenizers stands for at least one byte of text, so we count each byte of the call's JSON
    (messages, tools and response format, in UTF-8) as one input token, plus OVERHEAD_TOKENS, at the dearest input
    price. On top come all the output tokens the call allows, and OpenAI's higher rates for long prompts whenever
    this count is over their threshold. The course app reserves the same way."""
    p = PRICES[name]
    raw = _compact(messages) + _compact(options.get("tools") or []) + _compact(options.get("response_format"))
    tokens_in = len(raw.encode("utf-8")) + OVERHEAD_TOKENS
    tokens_out = max(RESPONSES_MIN_OUT, max_tokens_of(options))
    long = p.get("long_input")
    fin, fout = (long[1], long[2]) if long and tokens_in > long[0] else (1.0, 1.0)
    return (tokens_in * max(p["input"], p["cache_write"]) * fin + tokens_out * p["output"] * fout) / 1e6


def cost_usd(name, usage):
    """What a call cost, from its token counts in chat completion form; reasoning tokens are output tokens."""
    p = PRICES[name]
    tin = usage.get("prompt_tokens", 0) or 0
    tout = usage.get("completion_tokens", 0) or 0
    details = usage.get("prompt_tokens_details") or {}
    cached = min(details.get("cached_tokens", 0) or 0, tin)
    written = min(details.get("cache_write_tokens", 0) or 0, tin - cached)
    long = p.get("long_input")
    fin, fout = (long[1], long[2]) if long and tin > long[0] else (1.0, 1.0)
    cost_in = (tin - cached - written) * p["input"] + cached * p["cached"] + written * p["cache_write"]
    return (cost_in * fin + tout * p["output"] * fout) / 1e6


# ---- encrypted reasoning, as the course app carries it -------------------------------------------------------

def encode_reasoning(items):
    raw = json.dumps(items, ensure_ascii=False, separators=(",", ":")).encode()
    return REASONING_PREFIX + base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_reasoning(value):
    """The reasoning items, or None if the value isn't one we sent."""
    if not isinstance(value, str) or not value.startswith(REASONING_PREFIX):
        return None
    body = value[len(REASONING_PREFIX):]
    try:
        items = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except (ValueError, TypeError):
        return None
    ok = isinstance(items, list) and items and all(
        isinstance(i, dict) and set(i) == {"id", "summary", "encrypted_content"} and isinstance(i["id"], str)
        and isinstance(i["summary"], list) and isinstance(i["encrypted_content"], str) and i["encrypted_content"]
        for i in items)
    return items if ok else None


# ---- HTTP ----------------------------------------------------------------------------------------------------

def _tls_context():
    """Certificates from certifi when it is installed: Python from python.org on a Mac has none of its own until
    you run its "Install Certificates" script. Without certifi, Python's own certificates."""
    try:
        import certifi
    except ImportError:
        return None
    return ssl.create_default_context(cafile=certifi.where())


class HTTPFailure(Exception):
    """An HTTP error answer (status and body), or no answer at all (status None)."""

    def __init__(self, status, body, refused=False):
        super().__init__(f"HTTP {status}")
        self.status, self.body, self.refused = status, body, refused


def http_post(url, headers, body, timeout):
    """POST JSON and return the decoded JSON answer, or raise HTTPFailure."""
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"content-type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_tls_context()) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise HTTPFailure(e.code, e.read().decode(errors="replace")[:4000]) from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        refused = isinstance(getattr(e, "reason", None), ConnectionRefusedError)
        raise HTTPFailure(None, f"could not reach {url}: {e}", refused=refused) from None


def _error_of(body):
    """(code, message) from an error answer such as {"error": {"code", "message"}}."""
    try:
        err = json.loads(body).get("error")
        if isinstance(err, dict):
            return err.get("code") or err.get("type"), str(err.get("message") or "")
        if isinstance(err, str):
            return None, err
    except (ValueError, AttributeError, TypeError):
        pass
    return None, str(body or "")[:600]


def _redact(text, *secrets):
    for s in secrets:
        if s:
            text = text.replace(s, "[key]")
    return text


# ---- the answer, as a chat completion ------------------------------------------------------------------------

def _usage(tin, tout, cached, written, reasoning):
    tout = tout if tout >= reasoning else tout + reasoning  # reasoning counts as output
    return {"prompt_tokens": tin, "completion_tokens": tout, "total_tokens": tin + tout,
            "prompt_tokens_details": {"cached_tokens": cached, "cache_write_tokens": written},
            "completion_tokens_details": {"reasoning_tokens": reasoning}}


def from_chat(resp):
    """An OpenAI chat completion with only the fields the course app passes on."""
    u = resp.get("usage") or {}
    pd, cd = u.get("prompt_tokens_details") or {}, u.get("completion_tokens_details") or {}
    choice = (resp.get("choices") or [{}])[0]
    m = choice.get("message") or {}
    calls = [{"id": c.get("id", ""), "type": "function",
              "function": {"name": (c.get("function") or {}).get("name", ""),
                           "arguments": (c.get("function") or {}).get("arguments", "")}}
             for c in (m.get("tool_calls") or [])]
    message = {"role": "assistant", "content": m.get("content")}
    if m.get("refusal"):
        message["refusal"] = m["refusal"]
    if calls:
        message["tool_calls"] = calls
    return {"id": resp.get("id", ""), "object": "chat.completion", "created": resp.get("created") or int(time.time()),
            "model": resp.get("model", ""),
            "choices": [{"index": 0, "finish_reason": choice.get("finish_reason") or "stop", "logprobs": None,
                         "message": message}],
            "usage": _usage(u.get("prompt_tokens", 0) or 0, u.get("completion_tokens", 0) or 0,
                            pd.get("cached_tokens", 0) or 0, pd.get("cache_write_tokens", 0) or 0,
                            cd.get("reasoning_tokens", 0) or 0)}


def from_responses(resp):
    """A Responses API answer as a chat completion, with the turn's reasoning in `encrypted_reasoning`."""
    u = resp.get("usage") or {}
    idt, odt = u.get("input_tokens_details") or {}, u.get("output_tokens_details") or {}
    texts, refusals, calls, reasoning = [], [], [], []
    for item in resp.get("output") or []:
        kind = item.get("type")
        if kind == "message":
            for part in item.get("content") or []:
                if part.get("type") == "output_text":
                    texts.append(part.get("text", ""))
                elif part.get("type") == "refusal":
                    refusals.append(part.get("refusal", ""))
        elif kind == "function_call":
            calls.append({"id": item.get("call_id", ""), "type": "function",
                          "function": {"name": item.get("name", ""), "arguments": item.get("arguments", "")}})
        elif kind == "reasoning" and item.get("encrypted_content"):
            reasoning.append({"id": item.get("id", ""), "summary": item.get("summary") or [],
                              "encrypted_content": item["encrypted_content"]})
    incomplete = (resp.get("incomplete_details") or {}).get("reason")
    if calls:
        finish = "tool_calls"
    elif resp.get("status") == "incomplete":
        finish = "content_filter" if incomplete == "content_filter" else "length"
    else:
        finish = "stop"
    message = {"role": "assistant", "content": "".join(texts) or None}
    if refusals:
        message["refusal"] = "".join(refusals)
    if calls:
        message["tool_calls"] = calls
        if reasoning:  # only with tool calls, as the pilot kept it and the course app keeps it
            message["encrypted_reasoning"] = encode_reasoning(reasoning)
    return {"id": resp.get("id", ""), "object": "chat.completion", "created": resp.get("created_at") or int(time.time()),
            "model": resp.get("model", ""),
            "choices": [{"index": 0, "finish_reason": finish, "logprobs": None, "message": message}],
            "usage": _usage(u.get("input_tokens", 0) or 0, u.get("output_tokens", 0) or 0,
                            idt.get("cached_tokens", 0) or 0, idt.get("cache_write_tokens", 0) or 0,
                            odt.get("reasoning_tokens", 0) or 0)}


def _failed(res):
    """True for our own error answer {"error": "..."}. OpenAI's Responses API answers always have an "error" field,
    null when the call worked, so the field alone doesn't mean a failure."""
    return isinstance(res, dict) and isinstance(res.get("error"), str)


def _without_nulls(d):
    return {k: v for k, v in (d or {}).items() if v is not None}


def _joined(content):
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return content or ""


# ---- the gateway ---------------------------------------------------------------------------------------------

NO_MODEL = "no model calls in this run, so we made no call and charged nothing"


class ModelGateway:
    """Makes the model calls of one run and keeps the run's spending under `max_cost_usd`.

    backend: "course" (the course app, with base_url and token), "openai" (OpenAI, with api_key), or "off" (no model
    calls: every call returns {"error": NO_MODEL} at no cost, for run_practice.py --no-model and check_upload.py).
    post and sleep are for tests: post(url, headers, body, timeout) returns the decoded answer or raises HTTPFailure.
    """

    def __init__(self, max_cost_usd, backend="course", *, base_url=None, token=None, api_key=None,
                 openai_url=OPENAI_URL, post=None, sleep=time.sleep):
        if backend not in ("course", "openai", "off"):
            raise ValueError(f"P2_BACKEND must be course or openai, not {backend!r}")
        self.max_cost = float(max_cost_usd)
        self.backend = backend
        self.base_url = (base_url or "").strip().rstrip("/")
        self._token = (token or "").strip()
        self._key = (api_key or "").strip()
        self.openai_url = openai_url.rstrip("/")
        self._post = post or http_post
        self._sleep = sleep
        self.spent = 0.0
        self.calls = []  # one record per call: model, backend, endpoint, tokens, cost, seconds, error

    @classmethod
    def from_env(cls, max_cost_usd, default_backend="course", environ=None, post=None, sleep=time.sleep):
        """The gateway for the settings in the environment. Removes the token and the key from the environment,
        so that a system can't call a model around the gateway. post and sleep as in __init__; chat.py passes
        versions that the Stop button can cancel, and the runners leave the defaults.

        P2_BACKEND      "course" or "openai" (default: default_backend)
        P2_BASE_URL     the course app's Project 2 address, e.g. https://<course app>/api/p2
        P2_TOKEN        the team's token from the course app
        OPENAI_API_KEY  the course key, for the "openai" backend (staff only)
        """
        env = os.environ if environ is None else environ
        backend = (env.get("P2_BACKEND") or default_backend).strip().lower()
        return cls(max_cost_usd, backend, base_url=env.get("P2_BASE_URL"), token=env.pop("P2_TOKEN", None),
                   api_key=env.pop("OPENAI_API_KEY", None), post=post, sleep=sleep)

    def settings_problem(self):
        """None when the backend has what it needs, else what is missing."""
        if self.backend == "off":
            return None
        if self.backend == "course":
            missing = [n for n, v in (("P2_BASE_URL", self.base_url), ("P2_TOKEN", self._token)) if not v]
            return f"set {' and '.join(missing)} to make model calls through the course app" if missing else None
        return None if self._key else "set OPENAI_API_KEY to make model calls with P2_BACKEND=openai"

    def describe(self):
        if self.backend == "off":
            return "none"
        return f"course app {self.base_url or '(P2_BASE_URL not set)'}" if self.backend == "course" else "OpenAI directly"

    # ---- one call --------------------------------------------------------------------------------------------
    def call(self, messages, model="gpt-4o-mini", **options):
        """A chat completion dict, or {"error": "..."} for a refused or failed call. Raises CostLimit when the
        call could pass the cost cap, and ModelAccessError when model calls can't work at all."""
        t0 = time.time()
        rec = {"model": str(model), "backend": self.backend}
        name, problem = check_request(model, options)
        if problem is None and (not isinstance(messages, list) or not messages):
            problem = "messages must be a non-empty list of messages"
        if problem is None:
            try:
                if _SURROGATE.search(_compact([messages, options])):
                    messages, options = _join_pairs(messages), _join_pairs(options)
                text_chars = _chars(messages, options)
            except (TypeError, ValueError) as e:
                problem = f"messages and options must be JSON values (dicts, lists, text, numbers): {e}"
            else:
                if text_chars > MAX_CHARS:
                    problem = f"the call has {text_chars:,} characters; the limit is {MAX_CHARS:,}"
                elif _SURROGATE.search(_compact([messages, options])):
                    problem = "messages and options must be valid text, without lone surrogates such as \\ud800"
        if problem is None and self.backend == "off":
            problem = NO_MODEL
        if problem:
            rec.update(error=problem, usd=0.0, seconds=0.0)
            self.calls.append(rec)
            return {"error": problem}
        est = estimate_usd(name, messages, options)  # reserved before the call
        rec["est_usd"] = round(est, 6)
        if self.spent + est > self.max_cost:
            raise CostLimit(f"cost cap reached: spent ${self.spent:.4f}, this call could cost up to ${est:.4f}, "
                            f"cap ${self.max_cost:.4f}")
        problem = self.settings_problem()
        if problem:
            raise ModelAccessError(problem)
        res = self._course(name, messages, options, rec) if self.backend == "course" else \
            self._openai(name, messages, options, rec)
        rec["seconds"] = round(time.time() - t0, 2)
        if _failed(res):
            rec.update(error=res["error"], usd=0.0)
        else:
            u = res["usage"]
            usd = res.get("cost_usd")
            usd = float(usd) if isinstance(usd, (int, float)) and not isinstance(usd, bool) else cost_usd(name, u)
            res["cost_usd"] = round(usd, 6)
            self.spent += usd
            pd, cd = u.get("prompt_tokens_details") or {}, u.get("completion_tokens_details") or {}
            rec.update(input_tokens=u.get("prompt_tokens", 0), cached_tokens=pd.get("cached_tokens", 0),
                       cache_write_tokens=pd.get("cache_write_tokens", 0), output_tokens=u.get("completion_tokens", 0),
                       reasoning_tokens=cd.get("reasoning_tokens", 0), usd=round(usd, 6))
            if "budget_left_usd" in res:
                rec["budget_left_usd"] = res["budget_left_usd"]
        self.calls.append(rec)
        if self.spent > self.max_cost:  # the actual cost passed the cap, e.g. a course app price above our bound
            raise CostLimit(f"cost cap passed: spent ${self.spent:.4f} of ${self.max_cost:.4f}")
        return res

    # ---- the course app --------------------------------------------------------------------------------------
    def _course(self, name, messages, options, rec):
        url = self.base_url if self.base_url.endswith("/chat/completions") else self.base_url + "/chat/completions"
        body = {"model": name, "messages": messages, **{k: v for k, v in options.items() if v is not None}}
        rec["endpoint"] = "course"
        for attempt in range(len(COURSE_WAITS) + 1):
            try:
                res = self._post(url, {"authorization": f"Bearer {self._token}"}, body, 120)
                break
            except HTTPFailure as e:
                code, msg = _error_of(e.body)
                msg = _redact(msg, self._token)
                if e.status == 401:
                    raise ModelAccessError(f"the course app refused the token: {msg}") from None
                if e.status == 402 or code == "course_budget_reached":
                    raise CostLimit(f"the course app refused the call: {msg}") from None
                if code == "closed":
                    raise ModelAccessError(f"the course app takes no model calls now: {msg}") from None
                # Busy, too many calls at once, rate limited, restarting or the model failed: nothing was charged.
                if (e.status in (429, 502, 503, 504) or e.refused) and attempt < len(COURSE_WAITS):
                    self._sleep(COURSE_WAITS[attempt])
                    continue
                return {"error": f"course app, HTTP {e.status or 'no answer'}: {msg}"}
        if not isinstance(res, dict) or not res.get("choices"):
            return {"error": "the course app sent an answer without choices"}
        res.setdefault("usage", {})
        return res

    # ---- OpenAI directly -------------------------------------------------------------------------------------
    def _openai(self, name, messages, options, rec):
        snapshot, effort = MODELS[name]
        tools = options.get("tools") or []
        if effort and tools:
            rec["endpoint"] = "responses"
            body = self._responses_body(snapshot, effort, messages, options)
            if isinstance(body, str):
                return {"error": body}
            res = self._openai_post("/responses", body)
            return res if _failed(res) else from_responses(res)
        rec["endpoint"] = "chat/completions"
        body = {"model": snapshot, "messages": [self._chat_message(m) for m in messages],
                "max_completion_tokens": max_tokens_of(options)}
        if effort:
            body["reasoning_effort"] = effort  # a reasoning model takes neither temperature nor top_p
        else:
            body.update({k: options[k] for k in ("temperature", "top_p") if options.get(k) is not None})
        if options.get("seed") is not None:
            body["seed"] = options["seed"]
        if tools:
            body["tools"] = [{"type": "function", "function": _without_nulls(t["function"])} for t in tools]
            for k in ("tool_choice", "parallel_tool_calls"):
                if options.get(k) is not None:
                    body[k] = options[k]
        rf = options.get("response_format")
        if rf:
            body["response_format"] = ({"type": "json_schema", "json_schema": _without_nulls(rf["json_schema"])}
                                       if rf["type"] == "json_schema" else rf)
        res = self._openai_post("/chat/completions", body)
        return res if _failed(res) else from_chat(res)

    @staticmethod
    def _chat_message(m):
        """An assistant message without our encrypted reasoning and without the OpenAI library's empty fields."""
        if not isinstance(m, dict) or m.get("role") != "assistant":
            return m
        out = {"role": "assistant"}
        if "content" in m:
            out["content"] = m["content"]
        for k in ("name", "tool_calls", "refusal"):
            if m.get(k):
                out[k] = m[k]
        return out

    @staticmethod
    def _responses_body(snapshot, effort, messages, options):
        """The Responses API request, as the pilot's wrapper and the course app build it, or an error text."""
        items = []
        for i, m in enumerate(messages):
            if not isinstance(m, dict):
                return f"messages[{i}] must be a message object"
            role = m.get("role")
            if role == "tool":
                items.append({"type": "function_call_output", "call_id": m.get("tool_call_id"),
                              "output": _joined(m.get("content"))})
                continue
            if role != "assistant":
                items.append({"role": role, "content": _joined(m.get("content"))})
                continue
            calls = m.get("tool_calls") or []
            if m.get("encrypted_reasoning"):
                reasoning = decode_reasoning(m["encrypted_reasoning"])
                if reasoning is None:
                    return f"messages[{i}].encrypted_reasoning must be the encrypted_reasoning we sent, unchanged"
                if calls:  # reasoning goes back only with the tool calls it led to
                    items += [{"type": "reasoning", **r} for r in reasoning]
            content = _joined(m.get("content"))
            if content:
                items.append({"role": "assistant", "content": content})
            for c in calls:
                fn = c.get("function") or {}
                items.append({"type": "function_call", "call_id": c.get("id"), "name": fn.get("name"),
                              "arguments": fn.get("arguments") or "{}"})
        body = {"model": snapshot, "input": items, "store": False, "include": ["reasoning.encrypted_content"],
                "reasoning": {"effort": effort}, "max_output_tokens": max(RESPONSES_MIN_OUT, max_tokens_of(options)),
                "tools": [{"type": "function", "name": t["function"]["name"],
                           "description": t["function"].get("description") or "",
                           "parameters": t["function"].get("parameters") or {"type": "object", "properties": {}},
                           "strict": bool(t["function"].get("strict"))} for t in options["tools"]]}
        tc = options.get("tool_choice")
        if tc is not None:
            body["tool_choice"] = tc if isinstance(tc, str) else {"type": "function", "name": tc["function"]["name"]}
        if options.get("parallel_tool_calls") is not None:
            body["parallel_tool_calls"] = options["parallel_tool_calls"]
        rf = options.get("response_format") or {}
        if rf.get("type") == "json_schema":
            body["text"] = {"format": {"type": "json_schema", **_without_nulls(rf["json_schema"])}}
        elif rf.get("type") == "json_object":
            body["text"] = {"format": {"type": "json_object"}}
        return body

    def _openai_post(self, path, body):
        for attempt in range(len(OPENAI_WAITS) + 1):
            try:
                return self._post(self.openai_url + path, {"authorization": f"Bearer {self._key}"}, body, 60)
            except HTTPFailure as e:
                _, msg = _error_of(e.body)
                msg = _redact(msg, self._key)
                if e.status == 401:
                    raise ModelAccessError(f"OpenAI refused the key: {msg}") from None
                if (e.status is None or e.status == 429 or e.status >= 500) and attempt < len(OPENAI_WAITS):
                    self._sleep(OPENAI_WAITS[attempt])
                    continue
                return {"error": f"OpenAI, HTTP {e.status or 'no answer'}: {msg[:600]}"}
