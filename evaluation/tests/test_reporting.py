"""Regression tests for penalty accounting and incomplete-run diagnostics."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from reporting import make_report, render_markdown


def label(rid, company="DB", kind="single_full", no_claim=False):
    answer = {"type": kind, "claims": [] if no_claim else [{"company": company}]}
    if no_claim:
        answer["no_claim"] = {"company": company}
    return {"id": rid, "answer": answer, "traveler": {"limit": 3}}


def row(rid, **values):
    return {"id": rid, "points": 1.0, "penalty": 0.0, "max": 1, "correct": 1,
            "extra": 0, "questions": 1, "limit": 3, "over": 0, "tools": 5,
            "model": 2, "usd": 0.001, "seconds": 2.0, "status": "ok", "issues": [], **values}


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="report-test-", dir=Path(__file__).resolve().parent)
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)

    def report(self, rows, labels, expected=None, events=None, **extra):
        summary = {"rows": rows, "totals": {"points": max(0, sum(r["points"] - r.get("penalty", 0) for r in rows)),
                                              "max": sum(r["max"] for r in rows)},
                   "usd": 0.002, "model_calls": [], **extra}
        path = self.folder / "summary.json"
        path.write_text(json.dumps(summary), encoding="utf-8")
        if events is not None:
            (self.folder / "log.jsonl").write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")
        return make_report(path, {c["id"]: c for c in labels}, expected or [c["id"] for c in labels], "dev")

    def test_penalties_are_applied_before_global_floor(self):
        report = self.report([row("P1"), row("P2", points=0, correct=0, penalty=1.5)], [label("P1"), label("P2")])
        self.assertEqual(report["score"]["points"], 0)
        self.assertEqual(report["cases"][1]["net_points"], -1.5)
        self.assertEqual(report["totals"]["penalty"], 1.5)
        self.assertTrue(report["score"]["official_totals_match"])

    def test_combined_codes_and_missing_facts(self):
        checks = [{"company": "DB", "filed": True, "amount": False, "train": False,
                   "provenance": False, "ok": False,
                   "provenance_issues": ["missing topic ticket_price", "price is not the ticket price"]}]
        report = self.report([row("P1", points=0, correct=0, issues=["ATP"])], [label("P1")],
                             events=[{"kind": "grade", "req": "P1", "grade": {"checks": checks}}])
        for key in ("amount", "train", "provenance", "missing_facts", "wrong_ticket_price"):
            self.assertEqual(report["failures"][key], 1)
        self.assertEqual(report["failures"]["missing_claim"], 0)
        self.assertIn("missing topic ticket_price", render_markdown(report))

    def test_missing_and_not_run_keep_full_denominator(self):
        labels = [label("P1"), label("P2"), label("P3")]
        report = self.report([row("P1"), row("P2", points=0, correct=0, status="not-run", seconds=0)], labels)
        self.assertEqual(report["score"]["max"], 3)
        self.assertEqual(report["score"]["points"], 1)
        self.assertEqual(report["coverage"]["attempted"], 1)
        self.assertEqual(report["coverage"]["not_run"], 1)
        self.assertEqual(report["coverage"]["missing_ids"], ["P3"])
        self.assertFalse(report["coverage"]["complete"])
        self.assertEqual(report["totals"]["request_seconds_mean"], 2)

    def test_missing_log_is_supported(self):
        report = self.report([row("P1", points=0, correct=0, issues=["missing-claim"])], [label("P1")])
        self.assertEqual(report["failures"]["missing_claim"], 1)
        self.assertEqual(report["failures"]["amount"], 0)
        self.assertEqual(report["cases"][0]["checks"], [])
        self.assertTrue(any("log.jsonl is absent" in x for x in report["warnings"]))

    def test_groups_no_claim_evidence_and_cost_include_setup(self):
        labels = [label("P1", "DB"), label("P2", "SBB", "nothing_threshold", True)]
        report = self.report([row("P1"), row("P2", points=0, correct=0, issues=["no-claim-P", "topics"])], labels,
                             usd=0.123, model_calls=[{"error": "redacted"}, {"cost_usd": 0.001}])
        self.assertEqual(report["groups"]["company"]["DB"]["fully_right"], 1)
        self.assertEqual(report["groups"]["decision"]["no_claim"]["max"], 1)
        self.assertEqual(report["failures"]["no_claim_train_evidence"], 1)
        self.assertEqual(report["failures"]["missing_facts"], 1)
        self.assertEqual(report["totals"]["cost_usd"], 0.123)
        self.assertEqual(report["totals"]["model_errors"], 1)
        self.assertNotIn("redacted", json.dumps(report))

    def test_wrong_declarations_are_not_duplicate_filings(self):
        report = self.report([row("P1", points=0, correct=0, penalty=0.5, extra=2,
                                  wrong_no_claims=1, issues=["missing-claim", "no-claim-when-owed"])], [label("P1")])
        self.assertEqual(report["failures"]["extra_or_duplicate_claims"], 1)
        self.assertEqual(report["failures"]["wrong_no_claim"], 1)

    def test_multiple_claim_denominator_and_over_questions(self):
        case = label("P1")
        case["answer"]["claims"].append({"company": "SNCF"})
        report = self.report([row("P1", max=2, points=0.75, correct=1, over=1, issues=["missing-claim"])], [case])
        self.assertEqual(report["score"]["max"], 2)
        self.assertEqual(report["failures"]["questions_over"], 1)
        self.assertEqual(report["groups"]["company"]["DB"]["max"], 2)
        self.assertEqual(report["groups"]["company"]["SNCF"]["max"], 2)

    def test_duplicate_rows_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "Duplicate summary row"):
            self.report([row("P1"), row("P1")], [label("P1")])

    def test_invalid_log_does_not_silently_drop_evidence(self):
        self.report([row("P1")], [label("P1")])
        (self.folder / "log.jsonl").write_text("{broken", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Invalid JSON"):
            make_report(self.folder / "summary.json", {"P1": label("P1")}, ["P1"], "dev")


if __name__ == "__main__":
    unittest.main()
