"""Diagnostics for the course runner's summary and event log.

The official runner remains the scoring authority. This module applies its
penalty convention to the selected requests and adds coverage and failure
diagnostics; it does not regrade claims or inspect model message contents.
"""
from __future__ import annotations

import json
import math
import re
import statistics
from collections import Counter
from pathlib import Path


FAILURE_KEYS = (
    "amount", "train", "provenance", "missing_claim", "missing_no_claim",
    "wrong_no_claim", "filed_when_nothing_owed", "extra_or_duplicate_claims",
    "no_claim_train_evidence", "missing_facts", "wrong_ticket_price",
    "questions_over", "runtime_error", "call_budget", "timeout",
    "model_access", "cost_cap", "not_run", "missing_output",
)


def _penalty(row):
    return float(row.get("penalty", row.get("set_penalty", row.get("no_claim_penalty", 0))))


def _expected(case):
    answer = case["answer"]
    claims = answer.get("claims", [])
    if claims:
        companies = sorted({c["company"] for c in claims})
        return len(claims), companies, "claim"
    nc = answer.get("no_claim", {})
    companies = sorted(set(nc.get("companies") or [nc.get("company", "unknown")]))
    return 1, companies, "no_claim"


def _load_grades(log_path):
    grades = {}
    if log_path.is_file():
        with log_path.open(encoding="utf-8-sig") as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"Invalid JSON at {log_path}:{line_number}: {exc.msg}") from exc
                if event.get("kind") == "grade" and event.get("req"):
                    grades[event["req"]] = event.get("grade") or {}
    return grades


def _failure_counts(row, grade, checks, decision, present):
    failures = Counter({key: 0 for key in FAILURE_KEYS})
    issues = row.get("issues", [])
    for code in issues:
        if re.fullmatch(r"[ATP]+", code):
            for char, name in (("A", "amount"), ("T", "train"), ("P", "provenance")):
                failures[name] += int(char in code)
    for code, name in (
        ("missing-claim", "missing_claim"), ("missing-no-claim", "missing_no_claim"),
        ("no-claim-P", "no_claim_train_evidence"), ("topics", "missing_facts"),
    ):
        failures[name] += issues.count(code)
    wrong_no_claim = grade.get("wrong_no_claims", row.get("wrong_no_claims"))
    if wrong_no_claim is None:
        wrong_no_claim = int(any(c in issues for c in ("no-claim-when-owed", "no-claim-wrong-company")))
    failures["wrong_no_claim"] = int(wrong_no_claim)
    failures["filed_when_nothing_owed"] = int(grade.get(
        "wrong_filings", int("filed-when-nothing-owed" in issues)))
    # The runner's extra field includes no_claim calls when compensation is owed.
    declarations = grade.get("no_claims", wrong_no_claim) if decision == "claim" else 0
    failures["extra_or_duplicate_claims"] = max(0, int(row.get("extra", 0)) - int(declarations))
    for check in checks:
        for problem in check.get("provenance_issues", []):
            failures["missing_facts"] += int(problem.startswith("missing topic "))
            failures["wrong_ticket_price"] += int(problem in ("price is not the ticket price", "price unreadable"))
    failures["questions_over"] = int(row.get("over", 0))
    status = row.get("status", "missing")
    failures["runtime_error"] = int(status.startswith("error:"))
    for status_value, name in (
        ("budget", "call_budget"), ("time", "timeout"), ("no-model", "model_access"),
        ("cost-cap", "cost_cap"), ("not-run", "not_run"),
    ):
        failures[name] = int(status == status_value)
    failures["missing_output"] = int(not present)
    return dict(failures)


def _group(cases, key):
    grouped = {}
    for case in cases:
        labels = case[key] if key == "companies" else [case[key]]
        for label in labels:
            grouped.setdefault(label, []).append(case)
    result = {}
    for label, members in sorted(grouped.items()):
        net = round(sum(c["net_points"] for c in members), 6)
        maximum = sum(c["max"] for c in members)
        result[label] = {
            "requests": len(members),
            "attempted": sum(c["attempted"] for c in members),
            "net_points": net,
            "points": max(0.0, net),
            "max": maximum,
            "percent": round(100 * max(0.0, net) / maximum, 2) if maximum else 0.0,
            "fully_right": sum(c["fully_right"] for c in members),
            "cost_usd": round(sum(c["usd"] for c in members), 6),
        }
    return result


def make_report(summary_path: Path, cases_by_id: dict, expected_ids: list[str], split_name: str) -> dict:
    """Read one official run; retain the full selected denominator after early stops.

    Missing rows receive zero, and not-run rows are distinguished from attempts.
    Per-case net contributions can be negative: the official set floor applies
    only after all contributions have been summed. Group net_points retain that
    convention, and company groups can overlap for multi-company requests.
    """
    summary_path = Path(summary_path).resolve()
    summary = json.loads(summary_path.read_text(encoding="utf-8-sig"))
    if len(expected_ids) != len(set(expected_ids)):
        raise ValueError("Expected request IDs contain duplicates")
    unknown = sorted(set(expected_ids) - set(cases_by_id))
    if unknown:
        raise ValueError(f"Unknown expected request IDs: {', '.join(unknown)}")
    rows = summary.get("rows", [])
    by_id = {}
    for row in rows:
        if row["id"] in by_id:
            raise ValueError(f"Duplicate summary row for {row['id']}")
        by_id[row["id"]] = row
    grades = _load_grades(summary_path.with_name("log.jsonl"))
    warnings = []
    if not summary_path.with_name("log.jsonl").is_file():
        warnings.append("log.jsonl is absent; detailed grading checks and missing-topic explanations are unavailable.")
    expected_set = set(expected_ids)
    missing = [rid for rid in expected_ids if rid not in by_id]
    unexpected = sorted(set(by_id) - expected_set)
    if missing:
        warnings.append("Missing summary rows count as zero against the full selected denominator.")
    if unexpected:
        warnings.append("Requests outside the selected split are excluded from score and per-case diagnostics; run-wide costs and model errors still cover the whole summary.")
    cases = []
    for rid in expected_ids:
        label = cases_by_id[rid]
        maximum, companies, decision = _expected(label)
        present = rid in by_id
        row = by_id.get(rid, {})
        grade = grades.get(rid, {})
        checks = grade.get("checks", [])
        penalty = _penalty(row)
        points = float(row.get("points", 0))
        status = row.get("status", "missing")
        attempted = present and status != "not-run"
        correct = int(row.get("correct", 0))
        extra = int(row.get("extra", 0))
        over = int(row.get("over", 0))
        if present and int(row.get("max", maximum)) != maximum:
            warnings.append(f"{rid}: runner maximum differs from the current practice labels.")
        cases.append({
            "id": rid, "case_type": label["answer"].get("type", "unspecified"),
            "companies": companies, "decision": decision, "present": present,
            "attempted": attempted, "points": points, "penalty": penalty,
            "net_points": round(points - penalty, 6), "max": maximum,
            "correct": correct, "fully_right": bool(attempted and correct == maximum and extra == 0 and over == 0 and penalty == 0),
            "status": status, "issues": row.get("issues", []),
            "questions": int(row.get("questions", 0)), "question_limit": int(row.get("limit", label.get("traveler", {}).get("limit", 0))),
            "questions_over": over, "extra": extra, "tool_calls": int(row.get("tools", 0)),
            "model_calls": int(row.get("model", 0)), "usd": float(row.get("usd", 0)),
            "seconds": float(row.get("seconds", 0)), "checks": checks,
            "provenance_issues": [problem for check in checks for problem in check.get("provenance_issues", [])],
            "failures": _failure_counts(row, grade, checks, decision, present),
        })
    maximum = sum(c["max"] for c in cases)
    computed_points = max(0.0, round(sum(c["net_points"] for c in cases), 6))
    row_points = max(0.0, round(sum(float(r.get("points", 0)) - _penalty(r) for r in rows), 6))
    official = summary.get("totals", {})
    official_points = official.get("points")
    official_max = official.get("max")
    match = None
    if official_points is not None and official_max is not None:
        match = math.isclose(row_points, float(official_points), abs_tol=1e-6) and sum(int(r.get("max", 0)) for r in rows) == int(official_max)
        if not match:
            warnings.append("Recomputed row totals differ from official totals; inspect the original summary before interpreting the score.")
    times = sorted(c["seconds"] for c in cases if c["attempted"])
    failures = {key: sum(c["failures"][key] for c in cases) for key in FAILURE_KEYS}
    calls = summary.get("model_calls", [])
    cost = float(summary.get("usd", sum(c["usd"] for c in cases)))
    if summary.get("stopped"):
        warnings.append("The course runner stopped early; consult summary.json's stopped field.")
    return {
        "schema_version": 1, "split": split_name, "source_summary": str(summary_path),
        "note": "Diagnostic report. The course runner is the scoring authority. Amount/train/provenance counts describe failed claim checks; missing claims are counted separately. Company groups may overlap. Cost includes setup when recorded in the summary.",
        "score": {"points": computed_points, "max": maximum,
                  "percent": round(100 * computed_points / maximum, 2) if maximum else 0.0,
                  "official_points": official_points, "official_max": official_max,
                  "official_totals_match": match},
        "coverage": {"expected": len(expected_ids), "rows_present": sum(c["present"] for c in cases),
                     "attempted": sum(c["attempted"] for c in cases),
                     "not_run": sum(c["status"] == "not-run" for c in cases),
                     "missing_ids": missing, "unexpected_ids": unexpected,
                     "complete": not missing and not unexpected and all(c["attempted"] for c in cases)},
        "totals": {"correct": sum(c["correct"] for c in cases),
                   "fully_right": sum(c["fully_right"] for c in cases),
                   "extra_filings": failures["extra_or_duplicate_claims"],
                   "penalty": round(sum(c["penalty"] for c in cases), 6),
                   "questions": sum(c["questions"] for c in cases),
                   "questions_over": sum(c["questions_over"] for c in cases),
                   "tool_calls": sum(c["tool_calls"] for c in cases),
                   "model_calls": sum(c["model_calls"] for c in cases),
                   "model_errors": sum(bool(call.get("error")) for call in calls),
                   "cost_usd": cost,
                   "request_seconds_sum": round(sum(times), 3),
                   "request_seconds_mean": round(statistics.mean(times), 3) if times else None,
                   "request_seconds_p95": times[math.ceil(len(times) * 0.95) - 1] if times else None},
        "statuses": dict(sorted(Counter(c["status"] for c in cases).items())),
        "failures": failures,
        "groups": {"case_type": _group(cases, "case_type"), "company": _group(cases, "companies"), "decision": _group(cases, "decision")},
        "setup": summary.get("setup"), "warnings": warnings, "cases": cases,
    }


def _cell(value):
    return str(value).replace("|", "\\|").replace("\n", " ")


def render_markdown(report: dict) -> str:
    """Render concise, shareable diagnostics without model requests or responses."""
    score, coverage, totals = report["score"], report["coverage"], report["totals"]
    lines = [
        f"# Evaluation: {_cell(report['split'])}", "",
        f"Score: **{score['points']:.2f} / {score['max']} ({score['percent']:.2f}%)**. "
        f"Fully right: **{totals['fully_right']} / {coverage['expected']} requests**.", "",
        f"Coverage: {coverage['attempted']} attempted, {coverage['not_run']} not run, "
        f"{len(coverage['missing_ids'])} missing. Statuses: "
        + ", ".join(f"{_cell(k)}={v}" for k, v in report["statuses"].items()) + ".", "",
        f"Cost: **${totals['cost_usd']:.5f}**; tools: {totals['tool_calls']}; "
        f"model calls: {totals['model_calls']}; model errors: {totals['model_errors']}.", "",
        f"Request runtime sum: {totals['request_seconds_sum']:.3f}s; "
        f"mean: {totals['request_seconds_mean']}s; p95: {totals['request_seconds_p95']}s. "
        "Runtime excludes setup; p95 uses the nearest-rank method over attempted requests.", "",
        report["note"], "",
    ]
    if report["warnings"]:
        lines += ["## Warnings", ""] + [f"- {w}" for w in report["warnings"]] + [""]
    lines += ["## Failure diagnostics", "", "| Category | Count |", "|---|---:|"]
    for key, value in report["failures"].items():
        lines.append(f"| {key.replace('_', ' ')} | {value} |")
    lines += ["", "Counts can overlap. A combined ATP issue increments amount, train, and provenance. "
              "Missing-fact counts use detailed checks where available; missing claims do not also count as ATP failures.", ""]
    for key, title in (("case_type", "Case type"), ("company", "Company"), ("decision", "Decision")):
        lines += [f"## {title}", "", "| Group | Attempted / expected | Net points before floor | Maximum | Fully right |", "|---|---:|---:|---:|---:|"]
        for label, group in report["groups"][key].items():
            lines.append(f"| {_cell(label)} | {group['attempted']} / {group['requests']} | {group['net_points']:.2f} | {group['max']} | {group['fully_right']} |")
        lines.append("")
    lines += ["## Requests", "", "| ID | Type | Net / max | Status | Issues | Questions / limit | Tools | Models | USD | Seconds |", "|---|---|---:|---|---|---:|---:|---:|---:|---:|"]
    for case in report["cases"]:
        lines.append(f"| {case['id']} | {_cell(case['case_type'])} | {case['net_points']:.2f} / {case['max']} | {_cell(case['status'])} | {_cell(', '.join(case['issues']))} | {case['questions']} / {case['question_limit']} | {case['tool_calls']} | {case['model_calls']} | {case['usd']:.5f} | {case['seconds']:.2f} |")
    details = [c for c in report["cases"] if c["provenance_issues"]]
    if details:
        lines += ["", "## Missing facts and ticket-price details", ""]
        for case in details:
            lines.append(f"- {case['id']}: " + "; ".join(_cell(p) for p in case["provenance_issues"]))
    return "\n".join(lines) + "\n"
