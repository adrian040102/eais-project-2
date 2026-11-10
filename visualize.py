"""Run explorer for the kit's runner: it shows what p2/runner.py recorded and what p2/score.py graded.

Usage, from the starter code folder:

  python -I visualize.py
  python -I visualize.py runs/<run-dir> [more run dirs]
  python -I visualize.py --latest 20 --out runs/explorer.html
  python -I visualize.py --all --open

Every number and every verdict on the page comes from the kit itself:

- points, penalties, "fully right" and the TOTAL lines are computed by p2/score.py: totals(), points(),
  no_claim_carry(), set_row_points() and carried_penalty();
- the failure categories are the strings that p2/score.py's issue_codes() returned, the two counters the kit
  charges for (row "extra" from grade["extra_filings"], row "over" from the traveler), and the recorded status.
  Nothing is renamed or re-invented;
- the per-request grader checks are the "checks" list of the log's grade event, copied verbatim;
- the expected side is the kit's practice answer key, shown as data and never re-judged.

The tool adds no grading of its own: if summary.json and p2/score.py disagree, the page says so in a warning
instead of picking one. It never calls a model and never changes a run. Local tools (search_rules, calculate_*)
execute inside the system process and therefore do not appear in the kit's event log.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
import webbrowser
from collections import Counter, defaultdict
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path


HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
# The starter code used to live in eais-p2-kit/ under a project folder; newer
# checkouts put p2/, practice/, forms/ and rules/ next to this script.
NESTED_KIT = (PROJECT / "eais-p2-kit" / "p2").is_dir()
KIT = PROJECT / "eais-p2-kit" if NESTED_KIT else HERE
RUNS_ROOT = PROJECT / "runs" if NESTED_KIT else KIT / "runs"
DEFAULT_OUT = RUNS_ROOT / "run-explorer.html"
for _entry in (str(HERE), str(KIT)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

try:  # the page reports the kit's grading, so it must run against the kit, not a copy of its rules
    from p2 import score as kit_score
    from p2.forms import FormDesk
    from p2.rules import fold
    from p2.traveler import norm_topic
except ImportError as _error:  # pragma: no cover
    raise SystemExit(f"visualize.py shows the kit's own grading, so it needs p2/ next to it or in "
                     f"eais-p2-kit/: {_error}")

# These run inside the system process, so the kit's log has no separate tool event.
LOCAL_TOOLS = {
    "search_rules", "list_rule_documents", "read_rule_document", "calculate_compensation",
    "calculate_delay", "resolve_connection", "budget", "finish",
}


def read_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def started_from_name(name):
    match = re.match(r"(\d{8})[T-](\d{6})", name)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1) + match.group(2), "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def started_key(path: Path):
    manifest = path / "run_manifest.json"
    if manifest.is_file():
        try:
            value = read_json(manifest).get("started_utc")
            if value:
                parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return parsed
        except (OSError, ValueError, TypeError):
            pass
    parsed = started_from_name(path.name)
    if parsed:
        return parsed
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return datetime.min.replace(tzinfo=timezone.utc)


def discover_runs(inputs):
    """Expand files, directories and a parent runs/ folder into run directories."""
    candidates = []
    for raw in inputs or [RUNS_ROOT]:
        path = Path(raw)
        if path.is_file():
            path = path.parent
        if (path / "summary.json").is_file():
            candidates.append(path)
        elif path.is_dir():
            candidates.extend(p for p in path.iterdir() if p.is_dir() and (p / "summary.json").is_file())
    seen, runs = set(), []
    for path in candidates:
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            runs.append(resolved)
    runs.sort(key=lambda p: (started_key(p), str(p)))
    return runs


def short_name(name):
    parsed = started_from_name(name)
    return parsed.strftime("%m-%d %H:%M") if parsed else name[:16]


def build_steps(events):
    """Order one request's raw log events into a renderable trace.

    Model turns keep the recorded response verbatim; kit tool and rule-document
    events are attached to the call that produced them, so the trace reads as
    model turn -> tool call -> result.
    """
    steps, turn = [], 0
    for event in events:
        kind = event.get("kind")
        if kind == "llm":
            turn += 1
            response = event.get("result") if isinstance(event.get("result"), dict) else {}
            calls = []
            for call in message_of(response).get("tool_calls") or []:
                function = call.get("function") or {}
                name = function.get("name")
                calls.append({"name": name, "args": parse_args(function.get("arguments")),
                              "local": name in LOCAL_TOOLS, "events": []})
            steps.append({
                "kind": "model", "t": event.get("t"), "n": event.get("n"), "turn": turn,
                "model": event.get("model"), "calls": calls,
                "usage": event.get("usage") or {}, "options": event.get("options") or {},
                "response": response,
            })
        elif kind in ("tool", "doc"):
            steps.append({"kind": kind, "t": event.get("t"), "n": event.get("n"),
                          "name": event.get("name"), "args": event.get("args") or {},
                          "result": event.get("result")})
        elif kind == "grade":
            steps.append({"kind": "grade", "grade": event.get("grade") or {},
                          "row": event.get("row") or {}})
        elif kind == "stdout":
            text = (event.get("text") or "").strip()
            if text:
                steps.append({"kind": "answer", "text": text})
    open_calls, ordered = [], []
    for step in steps:
        if step["kind"] == "model":
            open_calls = list(step["calls"])
            ordered.append(step)
        elif step["kind"] in ("tool", "doc"):
            # A kit event belongs to the model call that triggered it. A local
            # helper such as resolve_connection can trigger several kit calls,
            # so prefer a name match and otherwise attach to an unused call.
            target = next((call for call in open_calls
                           if call["name"] == step["name"] and not call["events"]), None)
            if target is None:
                target = next((call for call in open_calls if not call["events"]), None)
            if target is None and open_calls:
                target = open_calls[-1]
            if target is None:
                ordered.append(step)
            else:
                target["events"].append(step)
        else:
            ordered.append(step)
    return ordered


def parse_args(value):
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {"value": parsed}
        except json.JSONDecodeError:
            return {"value": value}
    return {}


def message_of(result):
    if not isinstance(result, dict):
        return {}
    choices = result.get("choices") or []
    if choices and isinstance(choices[0], dict):
        return choices[0].get("message") or {}
    return {}


def parse_log(path: Path):
    """Return ({request: [steps]}, setup info, statistics, {request: logged facts}).

    Malformed lines are skipped. The logged facts are the ask_traveler, file_claim and no_claim events exactly as
    the kit wrote them: data for the page, not a grade.
    """
    events_by_request = defaultdict(list)
    setup = {}
    stats = Counter()
    backends = Counter()
    log_path = Path(path)
    if not log_path.is_file():
        return {}, setup, {"steps": 0, "llm": 0, "tools": 0, "docs": 0,
                           "backends": [], "missing_log": True, "bad_lines": 0}, {}
    with log_path.open(encoding="utf-8-sig") as stream:
        for line in stream:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                stats["bad_lines"] += 1
                continue
            kind, req = event.get("kind"), event.get("req")
            if req == "setup":
                if kind == "doc":
                    stats["setup_docs"] += 1
                elif kind == "setup":
                    setup = {key: event.get(key) for key in
                             ("status", "cache_entries", "cache_bytes", "cache_problem", "stdout", "error")}
                continue
            if not req:
                continue
            if kind == "llm":
                stats["llm"] += 1
                usage = event.get("usage") or {}
                backends[usage.get("backend") or ("error" if usage.get("error") else "unknown")] += 1
            elif kind in ("tool", "doc"):
                stats[kind] += 1
            elif kind not in ("grade", "stdout"):
                continue
            events_by_request[req].append(event)
    traces = {req: build_steps(events) for req, events in events_by_request.items()}
    facts = {req: log_facts(events) for req, events in events_by_request.items()}
    info = {"steps": stats["llm"] + stats["tool"], "llm": stats["llm"], "tools": stats["tool"],
            "docs": stats["setup_docs"], "docs_in_requests": stats["doc"],
            "backends": sorted(backends), "missing_log": False, "bad_lines": stats["bad_lines"]}
    return traces, setup, info, facts


def load_answers(path):
    """Practice answer keys by request id, for the expected side of the page.

    Development diagnostic only: this reads the kit's practice key, which the
    submitted system must never see. Nothing here is written into team/. The
    page shows the key as data; it never re-judges it.
    """
    if path is None:
        return {}
    path = Path(path)
    if path.is_dir():
        path = path / "practice_requests.json"
    if not path.is_file():
        return {}
    try:
        data = read_json(path)
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    entries = data if isinstance(data, list) else (data.get("requests") or [])
    return {str(entry["id"]): entry.get("answer") or {}
            for entry in entries if isinstance(entry, dict) and entry.get("id")}


@lru_cache(maxsize=4)
def form_specs(folder):
    """Form specifications by folded company name, to map raw fields to policy slots."""
    specs = {}
    for file in sorted(Path(folder).glob("*.json")):
        try:
            spec = read_json(file)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        company = str(spec.get("company") or "").strip()
        if company:
            specs[company.casefold()] = spec
    return specs


def canonical_company(desk, company):
    """The form's own company name for a name the model filed, through the kit's FormDesk.names()."""
    try:
        return desk.names().get(fold(company)) or company
    except Exception:
        return company


def filing_view(desk, specs, company, fields):
    """The logged file_claim arguments, with the form's labels and slots where the kit's form defines them.

    This reads the kit's form file and the recorded arguments only; it makes no judgment about the filing. Whether
    a filing was accepted and how it scored is the "Grader checks" data from the log's grade event.
    """
    name = canonical_company(desk, company)
    spec = specs.get(str(name).casefold()) or {}
    fields = fields if isinstance(fields, dict) else {}
    labels, slots = {}, {}
    for field in spec.get("fields") or []:
        key = field.get("name")
        if key is None:
            continue
        labels[key] = field.get("label") or key
        if field.get("slot") and key in fields:
            slots[field["slot"]] = fields[key]
    return {"company": name, "filed_as": company, "known_form": bool(spec),
            "fields": fields, "labels": labels, "slots": slots}


def log_facts(events):
    """What the log records the model asking, filing and declaring, in recorded order.

    This is data, not a grade: it keeps the arguments and results the kit logged. "answered" follows the scorer's
    own rule in p2/score.py, which counts an ask_traveler answer unless it is one of NO_ANSWER.
    """
    asked, filings, declarations = [], [], []
    for event in events:
        if event.get("kind") != "tool":
            continue
        name, args, result = event.get("name"), event.get("args") or {}, event.get("result")
        if name == "ask_traveler" and isinstance(result, str):
            asked.append({"topic": norm_topic(args.get("topic")), "answer": result,
                          "answered": result not in kit_score.NO_ANSWER})
        elif name == "file_claim":
            filings.append({"company": args.get("company"), "fields": args.get("fields") or {},
                            "result": result})
        elif name == "no_claim":
            declarations.append({"company": args.get("company"), "result": result})
    return {"asked": asked, "filings": filings, "declarations": declarations}


def case_metadata(answer):
    """(case type, companies, expected decision) from a practice answer key entry."""
    answer = answer if isinstance(answer, dict) else {}
    claims = answer.get("claims") or []
    no_claim = answer.get("no_claim") or {}
    not_owed = answer.get("not_owed") or []
    companies = []

    def add(company):
        name = str(company).strip() if company is not None else ""
        if name and name.casefold() not in {x.casefold() for x in companies}:
            companies.append(name)

    for claim in claims:
        add(claim.get("company"))
    for company in no_claim.get("companies") or [no_claim.get("company")]:
        add(company)
    for ticket in not_owed:
        add(ticket.get("company"))
    decision = "claim" if claims else ("no_claim" if no_claim else "unknown")
    return answer.get("type") or decision, companies, decision


def official_failures(row):
    """Failure counts keyed only by names the kit itself uses.

    Each entry of row["issues"] is a string that p2/score.py's issue_codes() returned ("A", "AT", "P",
    "missing-claim", "topics", "no-claim-when-owed", ...). The two row counters that the kit charges for are added
    under the kit's own totals labels: "extra filings" (row "extra", from grade["extra_filings"]) and "questions over
    limit" (row "over"). A status other than "ok" is added under the status string that the runner recorded.
    """
    counts = Counter()
    for issue in row.get("issues") or []:
        counts[str(issue)] += 1
    extra = int(row.get("extra") or 0)
    if extra:
        counts["extra filings"] += extra
    over = int(row.get("over") or 0)
    if over:
        counts["questions over limit"] += over
    status = str(row.get("status") or "")
    if status and status != "ok":
        counts[status] += 1
    return dict(counts)


def _same_number(left, right):
    if isinstance(left, bool) or isinstance(right, bool):
        return left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return abs(float(left) - float(right)) <= 1e-9
    return left == right


def consistency_warnings(rows, grades):
    """Where summary.json differs from what p2/score.py computes from the log's grade event.

    We report the difference and leave both numbers as recorded; the page never decides which one is right.
    """
    problems = []
    for row in rows:
        request_id = str(row.get("id"))
        if row.get("status") == "not-run":
            continue
        grade = grades.get(request_id)
        if not isinstance(grade, dict) or not grade:
            problems.append(f"{request_id}: no grade event in log.jsonl, so this row cannot be checked against "
                            f"p2/score.py")
            continue
        over = int(row.get("over") or 0)
        computed = {
            "issues": kit_score.issue_codes(grade),
            "points": kit_score.points(grade, over),
            "penalty": kit_score.no_claim_carry(grade, over),
            "correct": grade.get("correct", 0),
            "max": grade.get("max", 1),
            "extra": grade.get("extra_filings", 0),
        }
        for key, value in computed.items():
            if not _same_number(row.get(key), value):
                problems.append(f"{request_id}: summary.json {key}={row.get(key)!r}, "
                                f"but p2/score.py gives {value!r}")
    return problems


def group_cases(cases, key_for):
    """Per-label counts and net points, as the explorer's group tables expect."""
    groups = {}
    for case in cases:
        for key in dict.fromkeys(key_for(case)):
            group = groups.setdefault(key, {"requests": 0, "attempted": 0, "net_points": 0.0,
                                            "max": 0, "percent": 0.0, "fully_right": 0, "cost_usd": 0.0})
            group["requests"] += 1
            group["attempted"] += int(bool(case.get("attempted")))
            group["net_points"] += float(case.get("net_points") or 0)
            group["max"] += int(case.get("max") or 0)
            group["fully_right"] += int(bool(case.get("fully_right")))
            group["cost_usd"] += float(case.get("usd") or 0)
    for group in groups.values():
        group["net_points"] = round(group["net_points"], 2)
        group["cost_usd"] = round(group["cost_usd"], 5)
        group["percent"] = round(100 * max(0.0, group["net_points"]) / group["max"], 2) if group["max"] else 0.0
    return groups


def report_from_summary(summary, split, answers=None, grades=None):
    """The explorer's case list, built only from summary.json rows and the log's grade events.

    Every score value goes through p2/score.py: set_row_points() for a case's net points, carried_penalty() for its
    penalty, totals() for the set totals and for "fully right", and issue_codes() when a row is checked.
    """
    answers, grades = answers or {}, grades or {}
    rows = [row for row in (summary.get("rows") or []) if isinstance(row, dict)]
    warnings = []
    if summary.get("stopped"):
        warnings.append(f"Run stopped: {summary['stopped']}")
    if summary.get("setup") not in (None, "ok", "none"):
        warnings.append(f"setup ended with {summary['setup']}")
    try:
        net_values = kit_score.set_row_points(rows)
    except KeyError as error:
        net_values = [float(row.get("points") or 0) - kit_score.carried_penalty(row) for row in rows]
        warnings.append(f"summary.json rows lack a field that p2/score.py set_row_points() needs ({error}); "
                        f"net points fall back to points - carried_penalty()")
    cases = []
    for row, net in zip(rows, net_values):
        request_id = str(row.get("id"))
        answer = answers.get(request_id) or {}
        grade = grades.get(request_id) or {}
        case_type, companies, decision = case_metadata(answer)
        checks = grade.get("checks") or []
        provenance = [str(problem) for check in checks if isinstance(check, dict)
                      for problem in (check.get("provenance_issues") or [])]
        cases.append({
            "id": row.get("id"), "case_type": case_type, "companies": companies, "decision": decision,
            "attempted": row.get("status") != "not-run",
            "points": row.get("points", 0.0), "penalty": kit_score.carried_penalty(row),
            "net_points": round(float(net), 5), "max": row.get("max", 1), "correct": row.get("correct", 0),
            "fully_right": bool(kit_score.totals([row], [], 0.0)[1]),
            "status": row.get("status", "missing"), "issues": row.get("issues") or [],
            "questions": row.get("questions", 0), "question_limit": row.get("limit", 0),
            "questions_over": row.get("over", 0), "extra": row.get("extra", 0),
            "tool_calls": row.get("tools", 0), "model_calls": row.get("model", 0),
            "usd": row.get("usd", 0), "seconds": row.get("seconds", 0), "checks": checks,
            "provenance_issues": provenance, "failures": official_failures(row),
        })
    model_calls = summary.get("model_calls")
    model_calls = model_calls if isinstance(model_calls, list) else []
    set_totals, fully_right, _tin, _tout, total_lines = kit_score.totals(
        rows, model_calls, float(summary.get("usd") or 0.0))
    seconds = [float(case.get("seconds") or 0) for case in cases]
    totals = {
        "points": set_totals["points"], "max": set_totals["max"], "correct": set_totals["correct"],
        "fully_right": fully_right, "penalty": set_totals["penalty"], "extra": set_totals["extra"],
        "questions": set_totals["questions"], "questions_over": set_totals["over"],
        "tool_calls": set_totals["tools"], "model_calls": set_totals["model"],
        "cost_usd": set_totals["usd"],
        "request_seconds_sum": round(sum(seconds), 2),
        "request_seconds_mean": round(sum(seconds) / len(seconds), 2) if seconds else 0.0,
    }
    recorded = summary.get("totals") if isinstance(summary.get("totals"), dict) else {}
    for key in ("points", "max", "correct", "penalty", "extra"):
        if key in recorded and not _same_number(recorded[key], totals[key]):
            warnings.append(f"summary.json totals {key}={recorded[key]!r}, but p2/score.py gives {totals[key]!r}")
    if grades:
        warnings.extend(consistency_warnings(rows, grades))
    failures = Counter()
    for case in cases:
        failures.update(case.get("failures") or {})
    not_run = sum(case["status"] == "not-run" for case in cases)
    official_points = recorded.get("points")
    return {
        "split": split, "cases": cases, "warnings": warnings,
        "score": {"points": totals["points"], "max": totals["max"],
                  "percent": round(100 * totals["points"] / totals["max"], 2) if totals["max"] else 0.0,
                  "official_points": official_points, "official_max": recorded.get("max"),
                  "official_totals_match": official_points is None
                  or _same_number(official_points, totals["points"])},
        "coverage": {"expected": len(cases), "attempted": sum(case["attempted"] for case in cases),
                     "not_run": not_run, "complete": not not_run},
        "totals": totals, "failures": dict(failures), "kit_total_lines": list(total_lines),
        "statuses": dict(Counter(case["status"] for case in cases)),
        "groups": {
            "case_type": group_cases(cases, lambda case: [case["case_type"]]),
            "company": group_cases(cases, lambda case: case["companies"] or ["\u2014"]),
            "decision": group_cases(cases, lambda case: [case["decision"]]),
        },
        "setup": summary.get("setup"),
    }


def build_report(summary, manifest, answers=None, grades=None):
    """The report, always rebuilt from summary.json and log.jsonl with the kit's p2/score.py.

    A stale evaluation.json is deliberately ignored: it could have been written with different rules, and this page
    must show what the current kit computes.
    """
    split = manifest.get("split") or summary.get("set") or "custom"
    return report_from_summary(summary, split, answers, grades)


def grades_of(traces):
    """The last grade event per request, the one the runner wrote for the request's final row."""
    grades = {}
    for request_id, trace in traces.items():
        for step in trace:
            if step.get("kind") == "grade" and step.get("grade"):
                grades[str(request_id)] = step["grade"]
    return grades


def desk_and_specs():
    """(the kit's FormDesk, its form specifications) for annotating logged form fields."""
    folder = KIT / "forms"
    if not folder.is_dir():
        return None, {}
    return FormDesk([folder]), form_specs(str(folder))


def load_run(path: Path, answers=None):
    path = Path(path).resolve()
    summary = read_json(path / "summary.json")
    manifest_path = path / "run_manifest.json"
    manifest = read_json(manifest_path) if manifest_path.is_file() else {}
    traces, setup, log_info, facts = parse_log(path / "log.jsonl")
    answers = answers or {}
    report = build_report(summary, manifest, answers, grades_of(traces))
    desk, specs = desk_and_specs()
    cases = []
    for case in report.get("cases") or []:
        item = dict(case)
        request_id = str(case.get("id"))
        item["trace"] = traces.get(case.get("id"), [])
        item["trace_note"] = (
            "log.jsonl is absent; no trace is available." if log_info.get("missing_log")
            else "No kit tool or model events were logged for this request."
        ) if not item["trace"] else ""
        item["key"] = answers.get(request_id)
        logged = dict(facts.get(case.get("id")) or {"asked": [], "filings": [], "declarations": []})
        logged["filings"] = [
            {**filing, **filing_view(desk, specs, filing.get("company"), filing.get("fields"))}
            for filing in logged.get("filings") or []
        ]
        item["log"] = logged
        cases.append(item)
    failures = report.get("failures") or {}
    if not failures:
        counts = Counter()
        for case in cases:
            counts.update(case.get("failures") or {})
        failures = dict(counts)
    statuses = report.get("statuses") or dict(Counter(case.get("status", "missing") for case in cases))
    score = report.get("score") or {}
    coverage = report.get("coverage") or {}
    totals = report.get("totals") or {}
    started = manifest.get("started_utc")
    if not started:
        parsed = started_from_name(path.name)
        started = parsed.isoformat() if parsed else None
    system_path = manifest.get("system") or summary.get("system")
    model_enabled = manifest.get("model_enabled")
    if model_enabled is None:
        model_enabled = bool(log_info.get("llm")) and any(
            backend not in ("off", "error", "unknown") for backend in log_info.get("backends") or [])
    return {
        "index": 0,
        "name": path.name,
        "short": short_name(path.name),
        "path": str(path),
        "split": report.get("split") or manifest.get("split") or summary.get("set") or "custom",
        "started": started,
        "system": system_path,
        "system_name": Path(system_path).name if system_path else "",
        "system_sha256": manifest.get("system_sha256"),
        "model_enabled": model_enabled,
        "sandbox": manifest.get("sandbox"),
        "python": manifest.get("runner_python"),
        "dataset_sha256": manifest.get("dataset_sha256"),
        "score": {
            "points": score.get("points", 0.0), "max": score.get("max", 0),
            "percent": score.get("percent", 0.0),
            "official_points": score.get("official_points"),
            "official_max": score.get("official_max"),
            "official_totals_match": score.get("official_totals_match"),
        },
        "coverage": coverage,
        "totals": totals,
        "statuses": statuses,
        "failures": {str(key): int(value) for key, value in failures.items() if int(value)},
        "groups": report.get("groups") or {},
        "warnings": report.get("warnings") or [],
        "kit_total_lines": report.get("kit_total_lines") or [],
        "setup": report.get("setup"),
        "summary": {key: summary.get(key) for key in
                    ("set", "usd", "tokens", "cache_bytes", "cache_entries", "stopped")},
        "manifest": {key: manifest.get(key) for key in
                     ("started_utc", "finished_utc", "runner_exit_code", "max_cost_usd", "timeout_s",
                      "command", "grading_python_matches", "split_version")},
        "log_setup": setup,
        "log_info": log_info,
        "cases": cases,
    }


def build_html(runs, title="Run Explorer", answer_source=None):
    for index, run in enumerate(runs):
        run["index"] = index
    generated = datetime.now(timezone.utc).isoformat(timespec="seconds")
    data = {"generated": generated, "title": title, "runs": runs,
            "answers": {"source": answer_source} if answer_source else None}
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    page = HTML.replace("__TITLE__", html.escape(title))
    page = page.replace("__GENERATED__", html.escape(generated))
    page = page.replace("__DATA__", payload)
    return page


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("runs", nargs="*", help="Run directories, summary.json files, or a folder of runs (default: runs/)")
    parser.add_argument("--latest", type=int, metavar="N", help="Use the N newest runs (default for no arguments: 12)")
    parser.add_argument("--all", action="store_true", help="Use every discovered run")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help=f"HTML output (default: {DEFAULT_OUT})")
    parser.add_argument("--title", default="Run Explorer")
    parser.add_argument("--answers", type=Path, default=KIT / "practice" / "practice_requests.json",
                        help="Practice answer key shown as the expected side (default: practice/practice_requests.json)")
    parser.add_argument("--no-answers", action="store_true",
                        help="Do not read practice answer keys")
    parser.add_argument("--open", action="store_true", help="Open the report in the default browser")
    args = parser.parse_args(argv)

    answers = {} if args.no_answers else load_answers(args.answers)
    answer_source = str(args.answers) if answers else None
    paths = discover_runs(args.runs)
    limit = None
    if not args.all:
        limit = args.latest if args.latest is not None else (12 if not args.runs else None)
    if limit is not None:
        paths = paths[-max(1, limit):]
    if not paths:
        print("No run directories with summary.json were found.", file=sys.stderr)
        return 2

    runs, failed = [], []
    for path in paths:
        try:
            runs.append(load_run(path, answers=answers))
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
            failed.append((path, str(error)))
    for path, error in failed:
        print(f"Skipped {path.name}: {error}", file=sys.stderr)
    if not runs:
        print("No run could be loaded.", file=sys.stderr)
        return 2

    output = args.out.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(build_html(runs, args.title, answer_source), encoding="utf-8")
    requests = sum(len(run.get("cases") or []) for run in runs)
    print(f"Wrote {output}")
    print(f"Runs: {len(runs)} · case rows: {requests}")
    if args.open:
        webbrowser.open(output.as_uri())
    return 0


HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
:root {
  --bg: #f5f7fa; --panel: #ffffff; --ink: #182230; --muted: #667085; --line: #e4e8ee;
  --accent: #2957c8; --accent-soft: #eaf0ff; --good: #147a4b; --bad: #b42318;
  --warn: #a15c00; --shadow: 0 1px 2px rgba(16,24,40,.06), 0 8px 24px rgba(16,24,40,.05);
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0f141b; --panel: #161d27; --ink: #e7edf6; --muted: #9aa7b8; --line: #273244;
    --accent: #8fb1ff; --accent-soft: #1b2a47; --good: #63d69b; --bad: #ff8d84;
    --warn: #f3b45f; --shadow: 0 1px 2px rgba(0,0,0,.3), 0 10px 28px rgba(0,0,0,.25);
  }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--ink);
  font: 14px/1.45 ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
header { padding: 28px clamp(16px, 4vw, 56px) 10px; }
h1 { margin: 0; font-size: clamp(22px, 3vw, 32px); letter-spacing: -.02em; }
.sub { color: var(--muted); margin-top: 4px; }
main { padding: 0 clamp(16px, 4vw, 56px) 56px; max-width: 1600px; margin: 0 auto; }
.panel { background: var(--panel); border: 1px solid var(--line); border-radius: 14px;
  box-shadow: var(--shadow); padding: 18px; margin-top: 18px; }
.panel h2 { font-size: 15px; text-transform: uppercase; letter-spacing: .06em; margin: 0 0 12px; color: var(--muted); }
.row { display: flex; gap: 14px; flex-wrap: wrap; align-items: center; min-width: 0; }
.spread { justify-content: space-between; }
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(150px, 100%), 1fr)); gap: 12px; }
.card { border: 1px solid var(--line); border-radius: 11px; padding: 12px 14px; background: var(--bg); min-width: 0; }
.card .k { color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: .06em; }
.card .v { font-size: 22px; font-weight: 650; margin-top: 2px; letter-spacing: -.02em; overflow-wrap: anywhere; }
.card .n { color: var(--muted); font-size: 12px; overflow-wrap: anywhere; }
.muted { color: var(--muted); }
.small { font-size: 12px; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
button, select, input { font: inherit; color: inherit; background: var(--panel);
  border: 1px solid var(--line); border-radius: 8px; padding: 7px 9px; }
select, input[type="search"] { min-width: 0; max-width: 100%; }
.row > select, .row > input { flex: 0 1 auto; }
button { cursor: pointer; }
button:hover, select:hover { border-color: var(--accent); }
button.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
label.check { display: inline-flex; gap: 6px; align-items: center; color: var(--muted); }
.tablewrap { overflow: auto; border: 1px solid var(--line); border-radius: 10px; }
table { border-collapse: separate; border-spacing: 0; width: 100%; font-size: 13px; }
th, td { padding: 8px 10px; border-bottom: 1px solid var(--line); text-align: left; white-space: nowrap; }
th { position: sticky; top: 0; z-index: 1; background: var(--panel); color: var(--muted);
  font-size: 11px; text-transform: uppercase; letter-spacing: .05em; }
tbody tr:last-child td { border-bottom: 0; }
tbody tr.click { cursor: pointer; }
tbody tr.click:hover, tbody tr.selected { background: var(--accent-soft); }
.num { text-align: right; font-variant-numeric: tabular-nums; }
.pos { color: var(--good); font-weight: 600; }
.neg { color: var(--bad); font-weight: 600; }
.badge { display: inline-block; border-radius: 999px; padding: 2px 8px; font-size: 11px; font-weight: 600;
  background: var(--accent-soft); color: var(--accent); }
.badge.ok, .badge.accepted, .badge.recorded { background: color-mix(in srgb, var(--good) 16%, transparent); color: var(--good); }
.badge.error, .badge.rejected, .badge.budget, .badge.time, .badge.no-model, .badge.cost-cap, .badge.not-run { background: color-mix(in srgb, var(--bad) 14%, transparent); color: var(--bad); }
.badge.truncated { background: color-mix(in srgb, var(--warn) 16%, transparent); color: var(--warn); }
.badge.local { background: var(--line); color: var(--muted); }
.late { color: var(--bad); }
.early { color: var(--good); }
#runMeta { overflow-wrap: anywhere; }
.chips { display: flex; gap: 6px; flex-wrap: wrap; }
.chip { border: 1px solid var(--line); border-radius: 999px; padding: 2px 8px; font-size: 11px; color: var(--muted); }
.chip.bad { color: var(--bad); border-color: color-mix(in srgb, var(--bad) 40%, var(--line)); }
.bar { height: 8px; border-radius: 999px; background: var(--accent); min-width: 2px; }
.barline { display: grid; grid-template-columns: minmax(90px, 190px) 1fr 42px; gap: 10px; align-items: center; padding: 3px 0; }
.barline .lab { color: var(--muted); font-size: 12px; overflow: hidden; text-overflow: ellipsis; }
.grid2 { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(320px, 100%), 1fr)); gap: 18px; }
.grid2 > div { min-width: 0; }
.meta { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(230px, 100%), 1fr)); gap: 10px 18px; }
.meta div { border-bottom: 1px dashed var(--line); padding: 4px 0; min-width: 0; }
.meta .k { color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: .05em; display: block; }
.meta .mono { display: block; overflow-wrap: anywhere; }
.warnbox { border-left: 3px solid var(--warn); background: color-mix(in srgb, var(--warn) 9%, transparent);
  padding: 9px 12px; border-radius: 6px; margin-top: 10px; }
.heat th.rot { writing-mode: vertical-rl; text-orientation: mixed; height: 88px; min-width: 34px; padding: 6px 2px; }
.heat th.case, .heat td.case { position: sticky; left: 0; z-index: 2; background: var(--panel); }
.heat td.cell { text-align: center; font-variant-numeric: tabular-nums; cursor: pointer; min-width: 44px; }
.heat td.cell:hover { outline: 2px solid var(--accent); outline-offset: -2px; }
.heat td.empty { background: repeating-linear-gradient(45deg, transparent, transparent 4px, var(--line) 4px, var(--line) 5px); }
.scrollx { overflow-x: auto; }
svg text { fill: var(--muted); font-size: 11px; }
dialog { border: 1px solid var(--line); border-radius: 14px; padding: 0; width: min(980px, 94vw);
  max-height: 88vh; background: var(--panel); color: var(--ink); box-shadow: var(--shadow); }
dialog::backdrop { background: rgba(6, 12, 22, .55); }
.dialoghead { display: flex; justify-content: space-between; align-items: center; gap: 12px;
  padding: 16px 20px; border-bottom: 1px solid var(--line); position: sticky; top: 0; background: var(--panel); z-index: 2; }
.dialogbody { padding: 18px 20px 26px; overflow: auto; max-height: calc(88vh - 68px); }
.trace { list-style: none; margin: 0; padding: 0; }
.step { display: grid; grid-template-columns: 58px minmax(0, 1fr); gap: 12px; }
.step > .when { color: var(--muted); font-size: 11px; text-align: right; padding-top: 3px;
  font-variant-numeric: tabular-nums; white-space: nowrap; }
.step > .body { position: relative; border-left: 2px solid var(--line); padding: 0 0 18px 16px; min-width: 0; }
.step:last-child > .body { border-left-color: transparent; }
.step > .body::before { content: ""; position: absolute; left: -6px; top: 3px; width: 10px; height: 10px;
  border-radius: 50%; background: var(--panel); border: 2px solid var(--line); box-sizing: border-box; }
.step.model > .body::before { border-color: var(--accent); }
.step.tool > .body::before { border-color: var(--good); }
.step.grade > .body::before { border-color: var(--warn); }
.step.answer > .body::before { border-color: var(--ink); }
.head { display: flex; gap: 8px; align-items: baseline; flex-wrap: wrap; min-width: 0; }
.step .name { font-weight: 650; }
.step.model .name { color: var(--accent); }
.metaline { color: var(--muted); font-size: 12px; margin-top: 2px; overflow-wrap: anywhere; }
.calls { list-style: none; margin: 8px 0 0; padding: 0; }
.call { border: 1px solid var(--line); border-radius: 9px; padding: 9px 11px; margin-top: 8px;
  background: var(--bg); min-width: 0; }
.callhead { display: flex; gap: 8px; align-items: baseline; flex-wrap: wrap; min-width: 0; }
.callname { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-weight: 650; font-size: 12.5px;
  min-width: 0; overflow-wrap: anywhere; }
.callargs { color: var(--muted); font-size: 12px; overflow-wrap: anywhere; min-width: 0; flex: 1 1 auto; }
.callbody { margin-top: 7px; min-width: 0; }
.event { min-width: 0; }
.event + .event { margin-top: 10px; padding-top: 9px; border-top: 1px dashed var(--line); }
.nestedhead { display: flex; gap: 8px; align-items: baseline; flex-wrap: wrap; min-width: 0; margin-bottom: 2px; }
.detail { white-space: pre-wrap; overflow-wrap: anywhere; margin-top: 5px; color: var(--ink); }
.quote { border-left: 3px solid var(--good); padding: 2px 0 2px 10px; white-space: pre-wrap;
  overflow-wrap: anywhere; }
.answerbox { border: 1px solid var(--line); border-radius: 8px; background: var(--bg); padding: 10px 12px;
  white-space: pre-wrap; overflow-wrap: anywhere; font-size: 13px; }
.tarow { display: flex; gap: 10px; align-items: baseline; flex-wrap: wrap; overflow-wrap: anywhere; }
.kv { width: auto; }
.kv th { width: 1%; white-space: nowrap; }
.kv td { white-space: normal; overflow-wrap: anywhere; }
table.mini { font-size: 12px; }
table.mini th, table.mini td { padding: 5px 8px; }
table.mini td.num { white-space: nowrap; }
.note { color: var(--muted); font-size: 11px; margin-top: 5px; overflow-wrap: anywhere; }
details { margin-top: 6px; }
summary { cursor: pointer; color: var(--muted); font-size: 12px; }
pre { margin: 6px 0 0; padding: 9px; overflow: auto; background: var(--bg); border: 1px solid var(--line);
  border-radius: 7px; font-size: 12px; max-height: 320px; white-space: pre-wrap; overflow-wrap: anywhere; }
.tracebar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin: 2px 0 12px; }
.tracebar .spacer { flex: 1 1 auto; }
.strip { margin: 6px 0 16px; }
.stripline { position: relative; height: 24px; background: var(--bg); border: 1px solid var(--line);
  border-radius: 7px; overflow: hidden; }
.stripblock { position: absolute; top: 4px; height: 14px; border-radius: 3px; opacity: .9; min-width: 3px; }
.stripblock.model { background: var(--accent); }
.stripblock.tool { background: var(--good); }
.stripblock.grade { background: var(--warn); }
.stripaxis { display: flex; justify-content: space-between; color: var(--muted); font-size: 11px; margin-top: 3px; }
.checks th, .checks td { font-size: 12px; }
.cmpclaim { border: 1px solid var(--line); border-radius: 9px; padding: 9px 11px; margin-top: 10px; background: var(--bg); min-width: 0; }
table.cmp th:first-child { width: 1%; white-space: nowrap; }
table.cmp td { vertical-align: top; }
table.cmp thead th { color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: .05em; }
.tiny { font-size: 11px; }
.hide { display: none !important; }
@media (max-width: 700px) {
  .barline { grid-template-columns: 120px 1fr 36px; }
  .dialogbody { padding: 14px; }
}
</style>
</head>
<body>
<header>
  <h1>__TITLE__</h1>
  <div class="sub">Static explorer for evaluation runs · every verdict from the kit's p2/score.py · generated __GENERATED__</div>
</header>
<main>
  <section class="panel">
    <div class="row spread">
      <div class="row">
        <label class="small muted" for="runSelect">Run</label>
        <select id="runSelect"></select>
        <span id="runSplit" class="badge"></span>
      </div>
      <div class="row small muted" id="runMeta"></div>
    </div>
    <div id="summaryCards" class="cards" style="margin-top:14px"></div>
    <div id="warnings"></div>
  </section>

  <section class="panel">
    <h2>p2/score.py output</h2>
    <div class="small muted">The kit's own TOTAL lines for this run, printed verbatim.</div>
    <pre id="kitTotals" class="quote" style="white-space:pre-wrap;margin-top:10px"></pre>
    <div class="note">Points, penalties, "fully right" and these lines come from p2/score.py totals(). Failure names are
      the strings that p2/score.py issue_codes() returned, the two counters the kit charges for, and the recorded status.
      The page adds no grading of its own.</div>
  </section>

  <section class="panel" id="trendPanel">
    <h2>Score over time</h2>
    <div class="scrollx"><svg id="trend" viewBox="0 0 900 170" role="img" aria-label="Score by run"></svg></div>
    <div class="small muted">Chronological runs. Click a point to select it.</div>
  </section>

  <section class="panel">
    <div class="row spread">
      <h2>Runs</h2>
      <div class="small muted" id="runCount"></div>
    </div>
    <div class="tablewrap">
      <table id="runTable">
        <thead><tr><th>Run</th><th>Split</th><th class="num">Score</th><th class="num">%</th>
          <th class="num">Fully right</th><th class="num">Penalty</th><th class="num">Cost</th>
          <th class="num">Tools</th><th class="num">Models</th><th class="num">Seconds</th><th>Status</th></tr></thead>
        <tbody></tbody>
      </table>
    </div>
  </section>

  <section class="panel">
    <h2>Case heatmap</h2>
    <div class="small muted" style="margin-bottom:10px">Colour is net points / maximum. Click a cell for the request trace. Columns are chronological.</div>
    <div class="scrollx"><table class="heat" id="heatmap"></table></div>
  </section>

  <section class="panel" id="comparePanel">
    <div class="row spread">
      <h2>Compare runs</h2>
      <div class="row">
        <label class="small muted" for="baseSelect">Baseline</label>
        <select id="baseSelect"></select>
        <label class="small muted" for="curSelect">Current</label>
        <select id="curSelect"></select>
        <label class="check small"><input id="changedOnly" type="checkbox"> changed only</label>
      </div>
    </div>
    <div class="scrollx"><table id="compareTable">
      <thead><tr><th>Case</th><th class="num">Baseline</th><th class="num">Current</th><th class="num">Δ net</th>
        <th>Baseline issues</th><th>Current issues</th><th>Current status</th></tr></thead>
      <tbody></tbody></table></div>
  </section>

  <section class="panel">
    <div class="row spread">
      <h2>Requests</h2>
      <div class="row">
        <input id="search" type="search" placeholder="Filter case, type, company, issue…" size="30">
        <select id="statusFilter"><option value="">All statuses</option></select>
        <select id="decisionFilter"><option value="">All decisions</option></select>
        <label class="check small"><input id="failingOnly" type="checkbox"> failing only</label>
      </div>
    </div>
    <div class="tablewrap">
      <table id="caseTable">
        <thead><tr><th>ID</th><th>Type</th><th>Company</th><th>Decision</th><th class="num">Net / max</th>
          <th>Status</th><th>Issues</th><th class="num">Q / limit</th><th class="num">Tools</th>
          <th class="num">Models</th><th class="num">USD</th><th class="num">Seconds</th></tr></thead>
        <tbody></tbody>
      </table>
    </div>
  </section>

  <section class="panel">
    <h2>Diagnostics for selected run</h2>
    <div class="grid2">
      <div>
        <div class="small muted" style="margin-bottom:8px">Failure categories (counts can overlap)</div>
        <div id="failures"></div>
      </div>
      <div>
        <div class="small muted" style="margin-bottom:8px">Groups</div>
        <div id="groups"></div>
      </div>
    </div>
  </section>

  <section class="panel">
    <h2>Run metadata</h2>
    <div class="meta" id="meta"></div>
    <div class="small muted" style="margin-top:12px">Local tools such as <span class="mono">search_rules</span> and
      <span class="mono">calculate_*</span> run inside the system process, so the kit log does not contain them.
      This explorer never embeds model message histories.</div>
  </section>
</main>

<dialog id="caseDialog">
  <div class="dialoghead">
    <div><strong id="caseTitle">Request</strong><div class="small muted" id="caseSub"></div></div>
    <button onclick="document.getElementById('caseDialog').close()">Close</button>
  </div>
  <div class="dialogbody" id="caseBody"></div>
</dialog>

<script id="run-data" type="application/json">__DATA__</script>
<script>
const DATA = JSON.parse(document.getElementById('run-data').textContent);
const state = { run: Math.max(0, DATA.runs.length - 1), search: '', status: '', decision: '', failing: false,
  base: Math.max(0, DATA.runs.length - 2), current: Math.max(0, DATA.runs.length - 1), compareInit: false };
const $ = (sel) => document.querySelector(sel);
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt = (value, digits = 2) => (value === null || value === undefined || Number.isNaN(Number(value))) ? '–' : Number(value).toFixed(digits);
const pct = (value) => fmt(value, 1) + '%';
const run = () => DATA.runs[state.run];
const caseOf = (r, id) => (r.cases || []).find((c) => c.id === id);
const statusClass = (status) => String(status || '').split(':')[0].replace(/[^a-z0-9-]/gi, '') || 'unknown';
const badge = (text, cls) => `<span class="badge ${esc(cls || statusClass(text))}">${esc(text)}</span>`;
const short = (value, n = 90) => { const s = String(value ?? ''); return s.length > n ? s.slice(0, n - 1) + '…' : s; };
const money = (value) => '$' + fmt(value, 5);
const issueText = (issues) => (issues && issues.length) ? issues.join(', ') : '—';

function ratio(c) {
  if (!c || !c.max) return null;
  return Number(c.net_points ?? (c.points - c.penalty)) / Number(c.max);
}
function heatStyle(r) {
  if (r === null) return '';
  const t = Math.max(0, Math.min(1, r));
  const hue = r < 0 ? 0 : 4 + t * 132;
  return `background:hsl(${hue} 62% 86%);color:#17202c;`;
}

function renderRunSelect() {
  const select = $('#runSelect');
  select.innerHTML = DATA.runs.slice().reverse().map((r) =>
    `<option value="${r.index}">${esc(r.name)}</option>`).join('');
  select.value = String(state.run);
  select.onchange = () => { state.run = Number(select.value); renderAll(); };
}

function renderSummary() {
  const r = run();
  $('#runSplit').textContent = r.split || 'custom';
  $('#runMeta').innerHTML = [
    r.system_name ? `system ${esc(r.system_name)}` : '',
    r.model_enabled === true ? 'model enabled' : r.model_enabled === false ? 'model disabled' : '',
    r.started ? `started ${esc(r.started)}` : '',
  ].filter(Boolean).map((x) => `<span>${x}</span>`).join(' · ');
  const totals = r.totals || {};
  const coverage = r.coverage || {};
  const cards = [
    ['Score', `${fmt(r.score.points)} / ${r.score.max}`, pct(r.score.percent)],
    ['Fully right', `${fmt(totals.fully_right ?? 0, 0)} / ${coverage.expected ?? (r.cases || []).length}`, 'strict success'],
    ['Coverage', `${coverage.attempted ?? 0} / ${coverage.expected ?? 0}`, coverage.complete ? 'complete' : 'incomplete'],
    ['Penalty', fmt(totals.penalty ?? 0), 'set-level deductions'],
    ['Cost', money(totals.cost_usd ?? r.summary.usd ?? 0), `${fmt(totals.model_calls ?? 0, 0)} model calls`],
    ['Calls', `${fmt(totals.tool_calls ?? 0, 0)} tools`, `${fmt(totals.model_calls ?? 0, 0)} models`],
    ['Runtime', `${fmt(totals.request_seconds_sum ?? 0, 1)}s`, `mean ${fmt(totals.request_seconds_mean ?? 0, 1)}s`],
    ['Questions', fmt(totals.questions ?? 0, 0), `over ${fmt(totals.questions_over ?? 0, 0)}`],
  ];
  $('#summaryCards').innerHTML = cards.map(([k, v, n]) =>
    `<div class="card"><div class="k">${esc(k)}</div><div class="v">${esc(v)}</div><div class="n">${esc(n)}</div></div>`).join('');
  const warnings = (r.warnings || []).map((w) => `<div class="warnbox">${esc(w)}</div>`).join('');
  $('#warnings').innerHTML = warnings;
}

function renderTrend() {
  const panel = $('#trendPanel');
  if (DATA.runs.length < 2) { panel.classList.add('hide'); return; }
  panel.classList.remove('hide');
  const width = 900, height = 170, left = 38, right = 14, top = 12, bottom = 28;
  const xs = (i) => DATA.runs.length === 1 ? left : left + i * (width - left - right) / (DATA.runs.length - 1);
  const ys = (p) => top + (100 - Math.max(0, Math.min(100, p))) * (height - top - bottom) / 100;
  const pts = DATA.runs.map((r, i) => [xs(i), ys(Number(r.score.percent || 0)), r]);
  const line = pts.map((p) => `${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(' ');
  const grid = [0, 25, 50, 75, 100].map((v) =>
    `<line x1="${left}" y1="${ys(v)}" x2="${width - right}" y2="${ys(v)}" stroke="var(--line)" stroke-dasharray="3 4"/>` +
    `<text x="${left - 7}" y="${ys(v) + 4}" text-anchor="end">${v}</text>`).join('');
  const dots = pts.map(([x, y, r], i) =>
    `<circle cx="${x}" cy="${y}" r="${i === state.run ? 6 : 4}" fill="${i === state.run ? 'var(--accent)' : 'var(--good)'}"` +
    ` data-i="${i}" style="cursor:pointer"><title>${esc(r.name)} — ${pct(r.score.percent)}</title></circle>`).join('');
  $('#trend').innerHTML = grid + `<polyline points="${line}" fill="none" stroke="var(--good)" stroke-width="2"/>` + dots;
  $('#trend').querySelectorAll('circle').forEach((node) => node.onclick = () => {
    state.run = Number(node.dataset.i); renderAll();
  });
}

function renderRunTable() {
  $('#runCount').textContent = `${DATA.runs.length} run${DATA.runs.length === 1 ? '' : 's'}`;
  const rows = DATA.runs.slice().reverse().map((r) => {
    const t = r.totals || {}, c = r.coverage || {};
    return `<tr class="click ${r.index === state.run ? 'selected' : ''}" data-i="${r.index}">
      <td class="mono">${esc(r.name)}</td><td>${esc(r.split)}</td>
      <td class="num">${fmt(r.score.points)} / ${r.score.max}</td><td class="num">${pct(r.score.percent)}</td>
      <td class="num">${fmt(t.fully_right ?? 0, 0)} / ${c.expected ?? 0}</td>
      <td class="num">${fmt(t.penalty ?? 0)}</td><td class="num">${money(t.cost_usd ?? 0)}</td>
      <td class="num">${fmt(t.tool_calls ?? 0, 0)}</td><td class="num">${fmt(t.model_calls ?? 0, 0)}</td>
      <td class="num">${fmt(t.request_seconds_sum ?? 0, 1)}</td>
      <td>${c.complete ? badge('complete', 'ok') : badge('incomplete', 'error')}</td></tr>`;
  }).join('');
  $('#runTable tbody').innerHTML = rows;
  $('#runTable').querySelectorAll('tr[data-i]').forEach((tr) => tr.onclick = () => {
    state.run = Number(tr.dataset.i); renderAll();
  });
}

function renderHeatmap() {
  const ids = [...new Set(DATA.runs.flatMap((r) => (r.cases || []).map((c) => c.id)))]
    .sort((a, b) => String(a).localeCompare(String(b), undefined, {numeric: true}));
  const head = `<thead><tr><th class="case">Case</th>${DATA.runs.map((r) =>
    `<th class="rot" title="${esc(r.name)}">${esc(r.short)}<br><span class="tiny">${esc(r.split)}</span></th>`).join('')}</tr></thead>`;
  const body = ids.map((id) => `<tr><td class="case mono">${esc(id)}</td>${DATA.runs.map((r) => {
    const c = caseOf(r, id);
    if (!c) return '<td class="empty" title="not in this run"></td>';
    const rr = ratio(c);
    return `<td class="cell" style="${heatStyle(rr)}" data-r="${r.index}" data-id="${esc(id)}"
      title="${esc(id)} · ${fmt(c.net_points)}/${c.max} · ${esc(c.status)} · ${esc(issueText(c.issues))}">${fmt(c.net_points)}/${c.max}</td>`;
  }).join('')}</tr>`).join('');
  $('#heatmap').innerHTML = head + `<tbody>${body}</tbody>`;
  $('#heatmap').querySelectorAll('td.cell').forEach((td) => td.onclick = () => {
    state.run = Number(td.dataset.r); renderAll(); openCase(td.dataset.id);
  });
}

function fillFilters() {
  const r = run();
  const statuses = [...new Set((r.cases || []).map((c) => c.status))].sort();
  const decisions = [...new Set((r.cases || []).map((c) => c.decision))].sort();
  const s = $('#statusFilter'), d = $('#decisionFilter');
  s.innerHTML = '<option value="">All statuses</option>' + statuses.map((x) => `<option>${esc(x)}</option>`).join('');
  d.innerHTML = '<option value="">All decisions</option>' + decisions.map((x) => `<option>${esc(x)}</option>`).join('');
  if (!statuses.includes(state.status)) state.status = '';
  if (!decisions.includes(state.decision)) state.decision = '';
  s.value = state.status; d.value = state.decision;
  $('#search').value = state.search;
  $('#failingOnly').checked = state.failing;
  s.onchange = () => { state.status = s.value; renderCases(); };
  d.onchange = () => { state.decision = d.value; renderCases(); };
  $('#search').oninput = (event) => { state.search = event.target.value.toLowerCase(); renderCases(); };
  $('#failingOnly').onchange = (event) => { state.failing = event.target.checked; renderCases(); };
}

function renderCases() {
  const r = run();
  const term = state.search;
  const cases = (r.cases || []).filter((c) => {
    if (state.status && c.status !== state.status) return false;
    if (state.decision && c.decision !== state.decision) return false;
    const failing = !c.fully_right || (c.issues || []).length > 0;
    if (state.failing && !failing) return false;
    if (!term) return true;
    const hay = [c.id, c.case_type, c.status, c.decision, (c.companies || []).join(' '), (c.issues || []).join(' '),
      (c.provenance_issues || []).join(' ')].join(' ').toLowerCase();
    return hay.includes(term);
  });
  $('#caseTable tbody').innerHTML = cases.map((c) => `<tr class="click" data-id="${esc(c.id)}">
    <td class="mono">${esc(c.id)}</td><td>${esc(c.case_type)}</td><td>${esc((c.companies || []).join(', ') || '—')}</td>
    <td>${esc(c.decision)}</td><td class="num">${fmt(c.net_points)} / ${c.max}</td>
    <td>${badge(c.status)}</td><td>${esc(issueText(c.issues))}</td>
    <td class="num">${fmt(c.questions, 0)} / ${fmt(c.question_limit, 0)}</td>
    <td class="num">${fmt(c.tool_calls, 0)}</td><td class="num">${fmt(c.model_calls, 0)}</td>
    <td class="num">${money(c.usd)}</td><td class="num">${fmt(c.seconds, 1)}</td></tr>`).join('');
  $('#caseTable').querySelectorAll('tr[data-id]').forEach((tr) => tr.onclick = () => openCase(tr.dataset.id));
}

function renderFailures() {
  const r = run();
  const entries = Object.entries(r.failures || {}).filter(([, count]) => Number(count) > 0)
    .sort((a, b) => b[1] - a[1] || String(a[0]).localeCompare(String(b[0])));
  if (!entries.length) { $('#failures').innerHTML = '<div class="muted small">No failure categories recorded.</div>'; return; }
  const max = Math.max(...entries.map(([, count]) => count));
  $('#failures').innerHTML = entries.map(([key, count]) =>
    `<div class="barline"><div class="lab mono" title="${esc(key)}">${esc(key)}</div>
      <div><div class="bar" style="width:${Math.max(2, 100 * count / max)}%"></div></div>
      <div class="num small">${count}</div></div>`).join('') +
    `<div class="note">Names are the kit's own: each string from p2/score.py issue_codes() ("A" amount, "T" train,
     "P" provenance, combined like "ATP" when several checks fail, plus the named issues), the two counters the kit
     charges for ("extra filings", "questions over limit", summed like the kit's TOTAL line), and the recorded run
     status.</div>`;
}

function renderKitTotals() {
  const lines = run().kit_total_lines || [];
  $('#kitTotals').textContent = lines.length ? lines.join('\n')
    : 'No p2/score.py TOTAL lines: summary.json has no rows.';
}

function renderGroups() {
  const groups = run().groups || {};
  const wanted = [['case_type', 'Case type'], ['company', 'Company'], ['decision', 'Decision']];
  const html = wanted.filter(([key]) => groups[key]).map(([key, title]) => {
    const rows = Object.entries(groups[key]).map(([label, g]) =>
      `<tr><td>${esc(label)}</td><td class="num">${fmt(g.attempted, 0)} / ${fmt(g.requests, 0)}</td>
       <td class="num">${fmt(g.net_points)} / ${g.max}</td><td class="num">${pct(g.percent)}</td>
       <td class="num">${fmt(g.fully_right, 0)}</td><td class="num">${money(g.cost_usd)}</td></tr>`).join('');
    return `<div class="small" style="margin-bottom:14px"><strong>${esc(title)}</strong>
      <div class="tablewrap"><table class="checks"><thead><tr><th>Group</th><th class="num">Attempted</th><th class="num">Net / max</th>
      <th class="num">%</th><th class="num">Fully right</th><th class="num">Cost</th></tr></thead><tbody>${rows}</tbody></table></div></div>`;
  }).join('');
  $('#groups').innerHTML = html || '<div class="muted small">No group data.</div>';
}

function renderMeta() {
  const r = run(), m = r.manifest || {}, s = r.summary || {}, ls = r.log_setup || {}, li = r.log_info || {};
  const items = [
    ['Run', r.name], ['Path', r.path], ['Split', r.split], ['Started', r.started],
    ['System', r.system], ['System SHA-256', r.system_sha256], ['Runner Python', r.python],
    ['Grading Python 3.10', m.grading_python_matches], ['Model enabled', r.model_enabled],
    ['Model backends', (li.backends || []).join(', ')], ['Sandbox', r.sandbox],
    ['Max cost USD', m.max_cost_usd], ['Timeout s', m.timeout_s], ['Exit code', m.runner_exit_code],
    ['Split version', m.split_version], ['Command', Array.isArray(m.command) ? m.command.join(' ') : m.command],
    ['Setup', ls.status ?? r.setup], ['Cache entries', ls.cache_entries ?? s.cache_entries],
    ['Cache bytes', ls.cache_bytes ?? s.cache_bytes], ['Rule documents read', li.docs],
    ['Log steps', li.steps], ['Official totals match', r.score.official_totals_match],
    ['Dataset SHA-256', r.dataset_sha256],
  ];
  $('#meta').innerHTML = items.filter(([, v]) => v !== null && v !== undefined && v !== '')
    .map(([k, v]) => `<div><span class="k">${esc(k)}</span><span class="mono small">${esc(short(v, 180))}</span></div>`).join('');
}

function checkTable(checks) {
  if (!checks || !checks.length) return '<div class="muted small">The log has no grade event for this request, so there are no p2/score.py checks to show.</div>';
  const keys = [...new Set(checks.flatMap((check) => Object.keys(check)))];
  const head = keys.map((key) => `<th>${esc(key)}</th>`).join('');
  const rows = checks.map((check) => `<tr>${keys.map((key) => {
    const value = check[key];
    if (typeof value === 'boolean') return `<td>${value ? badge('yes', 'ok') : badge('no', 'error')}</td>`;
    if (Array.isArray(value)) return `<td>${esc(value.join(', ') || '—')}</td>`;
    return `<td>${esc(value ?? '—')}</td>`;
  }).join('')}</tr>`).join('');
  return `<div class="tablewrap"><table class="checks"><thead><tr>${head}</tr></thead><tbody>${rows}</tbody></table></div>`;
}

/* ---------- request trace ---------- */
const messageOf = (response) => {
  const choices = (response && response.choices) || [];
  return (choices[0] && choices[0].message) || {};
};
const clock = (value) => {
  const m = String(value ?? '').match(/(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2})/);
  return m ? { day: m[1], time: m[2] } : { day: '', time: String(value ?? '') };
};
function clockCell(value, baseDay) {
  if (value === null || value === undefined || value === '') return '<span class="muted">—</span>';
  const { day, time } = clock(value);
  if (!day || !baseDay || day === baseDay) return esc(time);
  const days = Math.round((Date.parse(day) - Date.parse(baseDay)) / 86400000);
  return `${esc(time)} <span class="tiny muted">${days > 0 ? '+' + days + 'd' : days + 'd'}</span>`;
}
const delayCell = (value) => {
  if (value === null || value === undefined || value === '') return '<span class="muted">—</span>';
  const n = Number(value);
  if (Number.isNaN(n)) return esc(value);
  return `<span class="${n > 0 ? 'late' : n < 0 ? 'early' : 'muted'}">${n > 0 ? '+' : ''}${n}</span>`;
};
const serviceOf = (row) => {
  const day = row && (row.service_day || clock(row.dep_planned).day);
  return day || '';
};
function trainCell(row) {
  const flags = [];
  if (row.dep_cancelled || row.arr_cancelled) flags.push('<span class="chip bad">cancelled</span>');
  return `<strong>${esc(row.train || row.train_id || '?')}</strong>` +
    (row.train_id ? `<div class="tiny muted">${esc(row.train_id)}</div>` : '') +
    (flags.length ? ' ' + flags.join(' ') : '');
}
function dataTable(headers, rows, options) {
  const opts = options || {};
  const numeric = opts.numeric || [];
  const limit = opts.limit || 0;
  const shown = limit && rows.length > limit ? rows.slice(0, limit) : rows;
  const head = headers.map((head, i) => `<th${numeric.includes(i) ? ' class="num"' : ''}>${esc(head)}</th>`).join('');
  const body = shown.map((row) => `<tr>${row.map((cell, i) =>
    `<td${numeric.includes(i) ? ' class="num"' : ''}>${cell}</td>`).join('')}</tr>`).join('');
  const notes = [opts.note, limit && rows.length > limit ? `${rows.length - limit} more row(s) in raw JSON` : '']
    .filter(Boolean).map((text) => `<div class="note">${esc(text)}</div>`).join('');
  return `<div class="tablewrap"><table class="mini"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>${notes}`;
}
function kvTable(entries) {
  const rows = entries.filter(([, value]) => value !== null && value !== undefined && value !== '')
    .map(([key, value]) => `<tr><th>${esc(key)}</th><td>${value}</td></tr>`).join('');
  return rows ? `<div class="tablewrap"><table class="mini kv">${rows}</table></div>` : '';
}
function rawBlock(label, value) {
  if (value === undefined) return '';
  return `<details class="raw"><summary>${esc(label)}</summary><pre>${esc(JSON.stringify(value, null, 2))}</pre></details>`;
}
function toolStatus(name, result) {
  if (result && typeof result === 'object') {
    if (result.error) return 'error';
    if (result.status) return String(result.status);
    if (Array.isArray(result.errors) && result.errors.length) return 'rejected';
  }
  return 'ok';
}
function argSummary(name, args) {
  const a = args || {};
  const v = (x) => (x === null || x === undefined || x === '') ? '?' : String(x);
  switch (name) {
    case 'ask_traveler': return `topic: ${v(a.topic)}`;
    case 'find_trains': return `${v(a.origin)} → ${v(a.destination)} · ${v(a.date)} · ${v(a.from_time)}–${v(a.to_time)}`;
    case 'departures': return `${v(a.station)} · ${v(a.date)} · ${v(a.from_time)}–${v(a.to_time)}`;
    case 'train_stops': return `${v(a.train)} · ${v(a.date)}`;
    case 'get_form': case 'file_claim': case 'no_claim': case 'list_rule_documents':
      return a.company ? `company: ${v(a.company)}` : 'all companies';
    case 'search_rules':
      return `“${v(a.query)}”${a.company ? ` · ${v(a.company)}` : ''}`;
    case 'read_rule_document': return `doc: ${v(a.doc_id)}`;
    case 'calculate_compensation':
      return `${v(a.price)} × ${v(a.percentage)}%${a.minimum ? ` · min ${v(a.minimum)}` : ''}`;
    case 'calculate_delay': return `${v(a.planned)} → ${v(a.recorded)}`;
    case 'resolve_connection':
      return `${v(a.origin)} → ${v(a.via)} → ${v(a.destination)} · ${v(a.date)} · dep ${v(a.departure)} · change ${v(a.connection)}`;
    case 'budget': case 'finish': return '';
    default: {
      const keys = Object.keys(a);
      if (!keys.length) return '';
      return keys.map((key) => `${key}: ${typeof a[key] === 'object' ? JSON.stringify(a[key]) : a[key]}`).join(' · ');
    }
  }
}
function renderResult(name, result) {
  if (result === null || result === undefined) return '';
  if (typeof result === 'string') return `<div class="quote">${esc(result)}</div>`;
  if (typeof result !== 'object') return `<div class="detail">${esc(String(result))}</div>`;
  if (result.error) return `<div class="detail"><span class="badge error">error</span> ${esc(result.error)}</div>`;
  if (name === 'find_trains') {
    const rows = result.trains || [];
    const base = (result.from || '').slice(0, 10);
    return dataTable(['Train', 'Dep', 'Arr', 'Δ arr'], rows.map((row) => [
      trainCell(row),
      `<div>${clockCell(row.dep_planned, base)}</div>` +
        (row.dep_recorded ? `<div class="tiny muted">rec ${clockCell(row.dep_recorded, base)}</div>` : ''),
      `<div>${clockCell(row.arr_planned, base)}</div>` +
        (row.arr_recorded ? `<div class="tiny muted">rec ${clockCell(row.arr_recorded, base)}</div>` : ''),
      delayCell(row.arr_delay_min),
    ]), { numeric: [3], limit: 12, note: `${rows.length} direct train(s) · ${result.origin} → ${result.destination} · ${result.from}–${result.to}` });
  }
  if (name === 'departures') {
    const rows = result.departures || [];
    const base = (result.from || '').slice(0, 10);
    return dataTable(['Train', 'To', 'Dep', 'Δ dep'], rows.map((row) => [
      trainCell(row),
      esc(row.to || '—'),
      `<div>${clockCell(row.dep_planned, base)}</div>` +
        (row.dep_recorded ? `<div class="tiny muted">rec ${clockCell(row.dep_recorded, base)}</div>` : ''),
      delayCell(row.dep_delay_min),
    ]), { numeric: [3], limit: 12, note: `${rows.length} departure(s) · ${result.station} · ${result.from}–${result.to}` });
  }
  if (name === 'train_stops') {
    const trains = result.trains || [];
    return trains.map((train) => {
      const stops = train.stops || [];
      const base = serviceOf(train);
      return dataTable(['Station', 'Arr', 'Δ arr'], stops.map((stop) => [
        esc(stop.station || '—'),
        `<div>${clockCell(stop.arr_planned, base)}</div>` +
          (stop.arr_recorded ? `<div class="tiny muted">rec ${clockCell(stop.arr_recorded, base)}</div>` : ''),
        delayCell(stop.arr_delay_min),
      ]), { numeric: [2], limit: 14, note: `${train.train} (${train.train_id}) · ${stops.length} stops` });
    }).join('');
  }
  if (name === 'list_forms') {
    const forms = result.forms || [];
    return `<div class="chips">${forms.map((form) =>
      `<span class="chip" title="${esc(form.title || '')}">${esc(form.company)} · ${esc(form.language || '?')}</span>`).join('')}</div>`;
  }
  if (name === 'get_form') {
    const fields = result.fields || [];
    return (result.instructions ? `<div class="detail small">${esc(result.instructions)}</div>` : '') +
      dataTable(['Field', 'Label', 'Type', 'Req', 'Format / help'], fields.map((field) => [
        `<span class="mono">${esc(field.name)}</span>`,
        esc(field.label || ''),
        esc(field.type || ''),
        field.required ? badge('yes', 'ok') : '<span class="muted">no</span>',
        esc([field.format, field.help].filter(Boolean).join(' · ') || '—'),
      ]), { limit: 24, note: `${result.company} · ${fields.length} fields` });
  }
  if (name === 'file_claim' || name === 'no_claim') {
    const entries = [
      ['Company', esc(result.company || '—')],
      ['Status', badge(result.status || 'ok')],
      ['Claim ID', esc(result.claim_id || '')],
      ['Message', esc(result.message || '')],
    ];
    const train = result.train || {};
    if (train.train || train.train_id) {
      entries.push(['Train', `${esc(train.train || '')} <span class="mono tiny">${esc(train.train_id || '')}</span>`]);
    }
    if (train.from || train.to) entries.push(['Route', esc([train.from, train.to].filter(Boolean).join(' → '))]);
    const errors = (result.errors || []).map((error) => `<li>${esc(error)}</li>`).join('');
    return kvTable(entries) + (errors ? `<ul class="detail small">${errors}</ul>` : '');
  }
  if (result.chars !== undefined) return `<div class="detail">${esc(result.chars)} characters returned</div>`;
  if (result.documents !== undefined) return `<div class="detail">${esc(result.documents)} documents listed</div>`;
  const entries = Object.entries(result).map(([key, value]) => [
    key, `<span class="mono">${esc(typeof value === 'object' ? JSON.stringify(value) : value)}</span>`]);
  return entries.length ? kvTable(entries) : '<div class="note">empty result</div>';
}
function eventBlock(call, event) {
  const primary = event.name === call.name;
  const head = primary ? '' : `<div class="nestedhead">
    <span class="callname">${esc(event.name)}</span>
    <span class="callargs">${esc(argSummary(event.name, event.args))}</span>
    <span class="chip">nested</span>${badge(toolStatus(event.name, event.result))}</div>`;
  return `<div class="event${primary ? '' : ' nested'}">${head}${renderResult(event.name, event.result)}
    ${rawBlock('Raw JSON', { name: event.name, args: event.args, result: event.result })}</div>`;
}
function callRaw(call) {
  const events = call.events || [];
  if (events.length === 1 && events[0].name === call.name) {
    return { name: call.name, args: call.args, result: events[0].result };
  }
  return { name: call.name, args: call.args,
           events: events.map((event) => ({ name: event.name, args: event.args, result: event.result })) };
}
function callHtml(call) {
  const events = call.events || [];
  const summary = argSummary(call.name, call.args);
  const head = `<div class="callhead"><span class="callname">${esc(call.name || '?')}</span>
      ${summary ? `<span class="callargs">${esc(summary)}</span>` : '<span class="spacer"></span>'}
      ${call.local ? '<span class="chip">local</span>' : ''}
      ${events.length === 1 ? badge(toolStatus(events[0].name, events[0].result)) : ''}</div>`;
  const body = events.length ? events.map((event) => eventBlock(call, event)).join('')
    : '<div class="note">Executed inside the system process; the kit log records no result.</div>';
  // A single event already carries its own complete raw block.
  const raw = events.length === 1 ? '' : rawBlock('Raw JSON', callRaw(call));
  return `<li class="call">${head}<div class="callbody">${body}</div>${raw}</li>`;
}
function usageInfo(step) {
  const usage = step.usage || {};
  const responseUsage = (step.response && step.response.usage) || {};
  return {
    backend: usage.backend || usage.endpoint || '',
    model: usage.model || step.model || '',
    seconds: usage.seconds,
    usd: usage.usd ?? usage.est_usd ?? (step.response && step.response.cost_usd),
    input: usage.input_tokens ?? responseUsage.prompt_tokens,
    output: usage.output_tokens ?? responseUsage.completion_tokens,
    reasoning: usage.reasoning_tokens ?? ((responseUsage.completion_tokens_details || {}).reasoning_tokens),
    error: usage.error || (step.response && step.response.error) || '',
  };
}
function modelStep(step, when) {
  const info = usageInfo(step);
  const text = messageOf(step.response).content;
  const meta = [
    info.model,
    info.backend,
    info.seconds !== undefined && info.seconds !== null ? fmt(info.seconds, 2) + ' s' : '',
    info.usd !== undefined && info.usd !== null ? money(info.usd) : '',
    info.input !== undefined ? `${info.input}→${info.output ?? '?'} tok` : '',
    info.reasoning ? `${info.reasoning} reasoning tok` : '',
  ].filter(Boolean).join(' · ');
  const calls = (step.calls || []).map(callHtml).join('');
  return `<li class="step model">
    <div class="when">${when}</div>
    <div class="body">
      <div class="head"><span class="name">Model turn ${esc(step.turn)}</span>
        ${info.error ? badge('error') : ''}</div>
      <div class="metaline">${esc(meta)}</div>
      ${text ? `<div class="detail">${esc(text)}</div>` : ''}
      ${calls ? `<ul class="calls">${calls}</ul>` : '<div class="note">No tool call in this turn.</div>'}
      ${rawBlock('Raw response JSON', { model: step.model, options: step.options, usage: step.usage, response: step.response })}
    </div></li>`;
}
function toolStep(step, when) {
  return `<li class="step tool">
    <div class="when">${when}</div>
    <div class="body">
      <div class="head"><span class="name">${esc(step.name || step.kind)}</span>
        ${step.kind === 'doc' ? '<span class="chip">rule document</span>' : ''}
        ${badge(toolStatus(step.name, step.result))}</div>
      <div class="metaline">${esc(argSummary(step.name, step.args) || 'no arguments')}</div>
      <div class="callbody">${renderResult(step.name, step.result)}</div>
      ${rawBlock('Raw JSON', { name: step.name, args: step.args, result: step.result })}
    </div></li>`;
}
function gradeStep(step, when) {
  const grade = step.grade || {};
  const row = step.row || {};
  const score = Number(grade.correct || 0);
  const issues = (row.issues || []).map((issue) => `<span class="chip bad">${esc(issue)}</span>`).join('');
  return `<li class="step grade">
    <div class="when">${when}</div>
    <div class="body">
      <div class="head"><span class="name">Grader</span>
        ${badge(`${grade.correct ?? '?'} / ${grade.max ?? '?'}`, score > 0 ? 'ok' : 'error')}
        ${row.penalty ? badge(`penalty ${fmt(row.penalty)}`, 'error') : ''}</div>
      ${issues ? `<div class="chips" style="margin-top:6px">${issues}</div>` : ''}
      ${checkTable(grade.checks)}
      ${rawBlock('Raw JSON', step.grade)}
    </div></li>`;
}
function traceStats(trace) {
  const stats = { start: Infinity, end: -Infinity, wall: 0, modelSeconds: 0, turns: 0, calls: 0, events: 0 };
  const track = (t, seconds) => {
    if (typeof t !== 'number') return;
    stats.start = Math.min(stats.start, t - seconds);
    stats.end = Math.max(stats.end, t);
  };
  trace.forEach((step) => {
    if (step.kind === 'model') {
      stats.turns += 1;
      stats.modelSeconds += Number((step.usage || {}).seconds) || 0;
    }
    stats.calls += (step.calls || []).length;
    track(step.t, Number((step.usage || {}).seconds) || 0);
    (step.calls || []).forEach((call) => (call.events || []).forEach((event) => {
      stats.events += 1;
      track(event.t, 0);
    }));
  });
  stats.wall = Number.isFinite(stats.start) && Number.isFinite(stats.end) ? stats.end - stats.start : 0;
  return stats;
}
function timelineEntries(trace) {
  const entries = [];
  trace.forEach((step) => {
    if (typeof step.t === 'number') {
      entries.push({ kind: step.kind === 'model' ? 'model' : step.kind === 'grade' ? 'grade' : 'tool',
        label: step.kind === 'model' ? `model turn ${step.turn}` : (step.name || step.kind),
        t: step.t, seconds: Number((step.usage || {}).seconds) || 0 });
    }
    (step.calls || []).forEach((call) => (call.events || []).forEach((event) => {
      if (typeof event.t === 'number') {
        entries.push({ kind: 'tool', label: event.name || call.name, t: event.t, seconds: 0 });
      }
    }));
  });
  return entries.sort((a, b) => a.t - b.t);
}
function timelineStrip(trace) {
  const entries = timelineEntries(trace);
  if (entries.length < 2) return '';
  const stats = traceStats(trace);
  const span = Math.max(stats.wall, 0.1);
  const blocks = entries.map((entry) => {
    // The log records completion time, so a model block ends at its timestamp.
    const width = entry.seconds || Math.max(span * 0.004, 0.05);
    const left = Math.max(0, ((entry.t - width) - stats.start) / span * 100);
    const clipped = Math.min((width / span) * 100, 100 - left);
    return `<div class="stripblock ${entry.kind}" style="left:${left.toFixed(3)}%;width:${clipped.toFixed(3)}%"
      title="${esc(entry.label)} · ${(entry.t - width - stats.start).toFixed(1)}–${(entry.t - stats.start).toFixed(1)} s"></div>`;
  }).join('');
  return `<div class="strip"><div class="stripline">${blocks}</div>
    <div class="stripaxis"><span>0 s</span><span>${span.toFixed(1)} s wall clock</span></div></div>`;
}
function traceHtml(caseData) {
  const trace = caseData.trace || [];
  if (!trace.length) return `<div class="muted small">${esc(caseData.trace_note || 'No trace recorded.')}</div>`;
  const stats = traceStats(trace);
  const when = (t) => typeof t === 'number' && Number.isFinite(stats.start)
    ? `+${(t - stats.start).toFixed(1)}s` : '';
  const body = trace.map((step) => {
    if (step.kind === 'model') return modelStep(step, when(step.t));
    if (step.kind === 'grade') return gradeStep(step, when(step.t));
    if (step.kind === 'answer') {
      return `<li class="step answer"><div class="when">${when(step.t)}</div><div class="body">
        <div class="head"><span class="name">Printed output</span></div>
        <div class="answerbox">${esc(step.text)}</div></div></li>`;
    }
    return toolStep(step, when(step.t));
  }).join('');
  return timelineStrip(trace) + `<ol class="trace">${body}</ol>`;
}

const showValue = (value) => (value === null || value === undefined || value === '')
  ? '<span class="muted">—</span>' : esc(String(value));
const topicGroups = (groups) => (!groups || !groups.length) ? '<span class="muted">none</span>'
  : groups.map((group) => `<div class="mono">${esc(group.join(' | '))}</div>`).join('');
function kvRows(rows) {
  return `<div class="tablewrap"><table class="mini kv"><tbody>${rows.map(([k, v]) =>
    `<tr><th>${esc(k)}</th><td>${v}</td></tr>`).join('')}</tbody></table></div>`;
}
function keyHtml(c) {
  const key = c.key;
  const title = '<h3 class="small" style="margin-top:18px">Answer key (kit data, not a verdict)</h3>';
  if (!key) {
    return title + `<div class="note">No practice key loaded for ${esc(c.id)}. Run without --no-answers to show it.</div>`;
  }
  const chips = [`<span class="chip">key type: ${esc(key.type || '—')}</span>`];
  if (c.question_limit !== null && c.question_limit !== undefined) {
    chips.push(`<span class="chip">question limit: ${esc(c.question_limit)}</span>`);
  }
  const claims = (key.claims || []).map((claim, i) => `<div class="cmpclaim">
      <div class="callhead"><span class="callname">claim ${i + 1} · ${esc(claim.company || '—')}</span></div>
      ${kvRows([
        ['Amount', showValue(claim.amount) + (claim.currency ? ' ' + esc(claim.currency) : '')],
        ['Ticket price', showValue(claim.price)],
        ['Payment', showValue(claim.payment)],
        ['Train', `${esc(claim.train || '—')} <span class="tiny muted mono">${esc((claim.runs || []).join(', '))}</span>`],
        ['Required topic groups', topicGroups(claim.required_topics)],
        ['Delay (min)', showValue(claim.delay_min)],
      ])}</div>`).join('');
  const nc = key.no_claim;
  const noClaim = nc ? `<div class="cmpclaim">
      <div class="callhead"><span class="callname">no claim expected · ${esc((nc.companies || [nc.company]).join(' / '))}</span></div>
      ${kvRows([
        ['Reason', showValue(nc.reason)],
        ['Evidence runs', `<span class="mono">${esc((nc.required_runs || []).join(', ') || '—')}</span>`],
        ['Required topic groups', topicGroups(key.required_topics)],
      ])}</div>` : '';
  const notOwed = (key.not_owed || []).map((ticket) => `<div class="cmpclaim">
      <div class="callhead"><span class="callname">nothing owed · ${esc(ticket.company || '—')}</span></div>
      ${kvRows([['Runs', `<span class="mono">${esc((ticket.runs || []).join(', ') || '—')}</span>`]])}</div>`).join('');
  return `${title}
    <div class="row small" style="gap:6px">${chips.join('')}</div>
    ${claims}${noClaim}${notOwed}
    ${key.notes ? `<details class="raw"><summary>Why this is the expected answer</summary>
      <div class="quote">${esc(key.notes)}</div></details>` : ''}`;
}
function filingsHtml(c) {
  const log = c.log || {};
  const filings = log.filings || [];
  const declarations = log.declarations || [];
  const title = '<h3 class="small" style="margin-top:18px">File and declare calls, as logged</h3>';
  if (!filings.length && !declarations.length) {
    return title + '<div class="note">The log has no file_claim or no_claim call for this request.</div>';
  }
  const filed = filings.map((filing) => {
    const res = filing.result || {};
    const status = res.status || (res.error ? 'error' : '—');
    const slots = Object.entries(filing.slots || {})
      .map(([slot, value]) => `<span class="chip">${esc(slot)}: ${esc(value)}</span>`).join(' ');
    const fields = Object.entries(filing.fields || {}).map(([name, value]) => {
      const label = (filing.labels || {})[name];
      return `<tr><th class="mono">${esc(name)}</th><td>${esc(typeof value === 'object' ? JSON.stringify(value) : value)}` +
        (label && label !== name ? ` <span class="tiny muted">${esc(label)}</span>` : '') + '</td></tr>';
    }).join('');
    const errors = (res.errors || []).length
      ? `<div class="warnbox small">${esc(res.errors.join(' · '))}</div>` : '';
    return `<div class="cmpclaim"><div class="callhead"><span class="callname">file_claim · ${esc(filing.company || '?')}</span>
        ${badge(status)}${filing.known_form ? '' : ' ' + badge('unknown form', 'error')}</div>
      ${slots ? `<div class="row small" style="gap:6px">${slots}</div>` : ''}
      ${fields ? `<details class="raw" open><summary>Arguments the model sent</summary>
        <div class="tablewrap"><table class="mini kv">${fields}</table></div></details>` : ''}
      ${errors}</div>`;
  }).join('');
  const declared = declarations.map((item) => {
    const res = item.result || {};
    return `<div class="cmpclaim"><div class="callhead"><span class="callname">no_claim · ${esc(item.company || '?')}</span>
      ${badge(res.status || (res.error ? 'error' : '—'))}</div></div>`;
  }).join('');
  return `${title}
    <div class="note">These are the logged arguments and results. Whether the kit accepted them, and how each claim
    scored, is the "Grader checks" table below, copied from the log's grade event.</div>${filed}${declared}`;
}
function askedHtml(c) {
  const asked = ((c.log || {}).asked) || [];
  if (!asked.length) return '';
  const rows = asked.map((item) => `<tr><th class="mono">${esc(item.topic)}</th><td>${esc(item.answer)}</td>
    <td>${item.answered ? badge('counts', 'ok') : badge('no answer', 'error')}</td></tr>`).join('');
  return `<h3 class="small" style="margin-top:18px">Topics asked (ask_traveler events)</h3>
    <div class="note">"counts" follows p2/score.py: an answer counts unless it is one of the two NO_ANSWER strings.</div>
    <div class="tablewrap"><table class="mini kv"><tbody>${rows}</tbody></table></div>`;
}

function openCase(id) {
  const r = run();
  const c = caseOf(r, id);
  if (!c) return;
  $('#caseTitle').textContent = `${c.id} · ${c.case_type || 'request'}`;
  $('#caseSub').innerHTML = `${esc((c.companies || []).join(', ') || 'no company')} · ${esc(c.decision)} ·
    ${fmt(c.net_points)} / ${c.max} net · ${badge(c.status)}`;
  const failureChips = Object.entries(c.failures || {}).filter(([, v]) => Number(v) > 0)
    .map(([k, v]) => `<span class="chip bad">${esc(k)}: ${v}</span>`).join('');
  const provenance = (c.provenance_issues || []).map((x) => `<li>${esc(x)}</li>`).join('');
  const counts = [
    ['Questions', `${fmt(c.questions, 0)} / ${fmt(c.question_limit, 0)} (${fmt(c.questions_over, 0)} over)`],
    ['Calls', `${fmt(c.tool_calls, 0)} tools · ${fmt(c.model_calls, 0)} models`],
    ['Cost', money(c.usd)], ['Runtime', fmt(c.seconds, 2) + 's'],
    ['Points', `${fmt(c.points)} − ${fmt(c.penalty)} penalty = ${fmt(c.net_points)}`],
    ['Fully right', c.fully_right ? 'yes' : 'no'],
  ];
  const cards = counts.map(([k, v]) => `<div class="card"><div class="k">${esc(k)}</div><div class="v small">${esc(v)}</div></div>`).join('');
  const issues = (c.issues || []).length ? `<div class="chips">${c.issues.map((x) => `<span class="chip bad">${esc(x)}</span>`).join('')}</div>` : '';
  const trace = c.trace || [];
  const stats = traceStats(trace);
  const summary = trace.length
    ? `${stats.turns} model turn${stats.turns === 1 ? '' : 's'} · ${stats.calls} tool call${stats.calls === 1 ? '' : 's'} · ` +
      `${stats.wall.toFixed(1)} s wall clock · ${stats.modelSeconds.toFixed(1)} s in model calls`
    : 'no recorded events';
  $('#caseBody').innerHTML = `
    <div class="cards">${cards}</div>
    <div style="margin-top:14px">${issues}</div>
    ${provenance ? `<h3 class="small">Provenance issues reported by p2/score.py</h3><ul class="small">${provenance}</ul>` : ''}
    ${failureChips ? `<h3 class="small">Failure categories (kit names)</h3><div class="chips">${failureChips}</div>` : ''}
    ${keyHtml(c)}
    ${filingsHtml(c)}
    ${askedHtml(c)}
    <h3 class="small" style="margin-top:18px">Grader checks (p2/score.py, copied from the log's grade event)</h3>${checkTable(c.checks)}
    <h3 class="small" style="margin-top:18px">Trace</h3>
    <div class="tracebar">
      <button type="button" id="expandRaw">Expand raw JSON</button>
      <button type="button" id="collapseRaw">Collapse</button>
      <span class="spacer"></span>
      <span class="muted small">${esc(summary)}</span>
    </div>
    ${traceHtml(c)}
    <div class="note">Raw blocks show the recorded model response, tool arguments and tool result.
      The model request (system prompt, tool schemas, message history) is not embedded.</div>`;
  $('#expandRaw').onclick = () => document.querySelectorAll('#caseBody details.raw').forEach((node) => { node.open = true; });
  $('#collapseRaw').onclick = () => document.querySelectorAll('#caseBody details.raw').forEach((node) => { node.open = false; });
  $('#caseDialog').showModal();
}

function initCompare() {
  if (DATA.runs.length < 2) return false;
  const options = DATA.runs.map((r) => `<option value="${r.index}">${esc(r.name)}</option>`).join('');
  const base = $('#baseSelect'), current = $('#curSelect');
  base.innerHTML = options; current.innerHTML = options;
  state.base = Math.max(0, DATA.runs.length - 2);
  state.current = DATA.runs.length - 1;
  base.value = String(state.base); current.value = String(state.current);
  base.onchange = () => { state.base = Number(base.value); renderCompare(); };
  current.onchange = () => { state.current = Number(current.value); renderCompare(); };
  $('#changedOnly').checked = false;
  $('#changedOnly').onchange = (event) => { state.changed = event.target.checked; renderCompare(); };
  state.compareInit = true;
  return true;
}

function renderCompare() {
  const panel = $('#comparePanel');
  if (DATA.runs.length < 2) { panel.classList.add('hide'); return; }
  panel.classList.remove('hide');
  if (!state.compareInit && !initCompare()) return;
  const base = DATA.runs[state.base], current = DATA.runs[state.current];
  const ids = [...new Set([...(base.cases || []), ...(current.cases || [])].map((c) => c.id))]
    .sort((a, b) => String(a).localeCompare(String(b), undefined, {numeric: true}));
  const rows = ids.map((id) => {
    const a = caseOf(base, id), b = caseOf(current, id);
    const an = a ? Number(a.net_points) : null, bn = b ? Number(b.net_points) : null;
    const delta = (an === null || bn === null) ? null : bn - an;
    const changed = !a || !b || delta !== 0 || a.status !== b.status ||
      (a.issues || []).join('|') !== (b.issues || []).join('|');
    return { id, a, b, an, bn, delta, changed };
  }).filter((row) => !state.changed || row.changed);
  $('#compareTable tbody').innerHTML = rows.map((row) => {
    const deltaClass = row.delta === null || row.delta === 0 ? '' : row.delta > 0 ? 'pos' : 'neg';
    const deltaText = row.delta === null ? '–' : (row.delta > 0 ? '+' : '') + fmt(row.delta);
    return `<tr class="click" data-id="${esc(row.id)}" data-r="${row.b ? current.index : base.index}">
      <td class="mono">${esc(row.id)}</td>
      <td class="num">${row.a ? fmt(row.an) + ' / ' + row.a.max : '–'}</td>
      <td class="num">${row.b ? fmt(row.bn) + ' / ' + row.b.max : '–'}</td>
      <td class="num ${deltaClass}">${deltaText}</td>
      <td>${esc(row.a ? issueText(row.a.issues) : 'not in baseline')}</td>
      <td>${esc(row.b ? issueText(row.b.issues) : 'not in current')}</td>
      <td>${row.b ? badge(row.b.status) : '–'}</td></tr>`;
  }).join('') || '<tr><td colspan="7" class="muted">No changed cases.</td></tr>';
  $('#compareTable').querySelectorAll('tr[data-id]').forEach((tr) => tr.onclick = () => {
    state.run = Number(tr.dataset.r); renderAll(); openCase(tr.dataset.id);
  });
}

function renderAll() {
  renderRunSelect(); renderSummary(); renderKitTotals(); renderTrend(); renderRunTable(); renderHeatmap();
  fillFilters(); renderCases(); renderFailures(); renderGroups(); renderMeta(); renderCompare();
}

document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') { const dialog = $('#caseDialog'); if (dialog.open) dialog.close(); }
});
renderAll();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    raise SystemExit(main())
