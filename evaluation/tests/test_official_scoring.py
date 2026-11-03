"""Integration checks using the kit's real scorer and synthetic tool events.

These fixtures do not execute an agent, perform live train lookups, or constitute
a benchmark run. They validate that reports preserve the official scorer's
decisions, evidence requirements, and penalty accounting. Train IDs come from
the public answer key solely to construct controlled scoring fixtures.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path


EVALUATION = Path(__file__).resolve().parents[1]
KIT = EVALUATION.parent / "eais-p2-kit"
sys.path.insert(0, str(KIT))
sys.path.insert(0, str(EVALUATION))

from p2kit.forms import FormDesk
from p2kit.rules import RuleLibrary
from p2kit.score import grade_request, issue_codes, no_claim_carry, points, totals
from p2kit.traveler import Traveler
from reporting import make_report


class OfficialScoringIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cases = json.loads((KIT / "practice" / "practice_requests.json").read_text(encoding="utf-8"))
        cls.labels = {case["id"]: case for case in cases}
        # The scorer accepts a RuleLibrary but does not read its documents.
        # An empty library avoids loading 45 unrelated texts in each test run.
        cls.rules = RuleLibrary([])

    def setUp(self):
        self.forms = FormDesk([KIT / "forms"])
        self.temp = tempfile.TemporaryDirectory(prefix="scorer-test-", dir=EVALUATION / "tests")
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)

    def ouigo_fixture(self, questions=1, ask_after=False):
        """Create an accepted P001 claim using the real form validator."""
        case = self.labels["P001"]
        expected = case["answer"]["claims"][0]
        run = {
            "train": "OUIGO 7853", "number": "7853", "lines": [],
            "train_id": expected["runs"][0], "service_day": "2026-10-20",
            "from": "Toulon", "to": "Nice-Ville",
        }
        traveler = Traveler(case["traveler"]["answers"], case["traveler"]["limit"])
        answers = []
        for _ in range(questions):
            answers.append({"kind": "tool", "name": "ask_traveler",
                            "args": {"topic": "ticket_price"},
                            "result": traveler.ask("ticket_price")})
        events = [] if ask_after else list(answers)
        events.append({"kind": "tool", "name": "find_trains", "args": {},
                       "result": {"trains": [run]}})
        fields = {
            "date_voyage": "20/10/2026", "numero_train": run["train_id"],
            "gare_depart": "Toulon", "gare_arrivee": "Nice-Ville",
            "heure_arrivee_prevue": "22:13", "heure_arrivee_reelle": "00:23",
            "retard_minutes": "130", "prix_trajet_eur": "25.00",
            "montant_demande_eur": "12.50", "mode_indemnisation": "bon d'achat",
        }
        result = self.forms.file_claim("OUIGO", fields, [run])
        self.assertEqual(result["status"], "accepted", result)
        events.append({"kind": "tool", "name": "file_claim",
                       "args": {"company": "OUIGO", "fields": fields},
                       "result": result, "accepted": self.forms.claims[-1]})
        if ask_after:
            events.extend(answers)
        return events, traveler.over_limit

    def declaration(self, company):
        result = self.forms.no_claim(company)
        self.assertEqual(result["status"], "recorded")
        return {"kind": "tool", "name": "no_claim", "args": {"company": company},
                "result": result, "declared": self.forms.no_claims[-1]}

    def grade_row(self, rid, events, over=0):
        case = self.labels[rid]
        grade = grade_request(case["text"], case["answer"], events, self.forms, self.rules)
        row = {
            "id": rid, "points": points(grade, over), "penalty": no_claim_carry(grade, over),
            "max": grade["max"], "correct": grade["correct"], "extra": grade["extra_filings"],
            "questions": sum(event.get("name") == "ask_traveler" for event in events),
            "limit": case["traveler"]["limit"], "over": over, "tools": len(events),
            "model": 0, "usd": 0.0, "status": "ok", "issues": issue_codes(grade),
            "seconds": 0.0,
        }
        if grade.get("wrong_no_claims") or grade.get("wrong_filings"):
            row["wrong_no_claims"] = grade.get("wrong_no_claims", 0)
        return row, grade

    def report(self, fixtures):
        rows, log_events = [], []
        for rid, events, over in fixtures:
            row, grade = self.grade_row(rid, events, over)
            rows.append(row)
            log_events.extend(dict(event, req=rid) for event in events)
            log_events.append({"req": rid, "kind": "grade", "grade": grade, "row": row})
        official_totals, full, _, _, _ = totals(rows, [], 0.0)
        summary = {"rows": rows, "totals": official_totals, "fully_right": full,
                   "model_calls": [], "usd": 0.0, "setup": "none", "stopped": None}
        path = self.folder / "summary.json"
        path.write_text(json.dumps(summary), encoding="utf-8")
        (self.folder / "log.jsonl").write_text(
            "\n".join(json.dumps(event) for event in log_events) + "\n", encoding="utf-8")
        report = make_report(path, self.labels, [rid for rid, _, _ in fixtures], "synthetic-scorer-fixtures")
        self.assertTrue(report["score"]["official_totals_match"])
        self.assertEqual(report["score"]["points"], official_totals["points"])
        self.assertEqual(report["score"]["max"], official_totals["max"])
        return report

    def test_p001_valid_claim_matches_real_scorer(self):
        events, over = self.ouigo_fixture()
        report = self.report([("P001", events, over)])
        self.assertEqual(report["score"]["points"], 1)
        self.assertEqual(report["totals"]["fully_right"], 1)
        self.assertEqual(report["cases"][0]["issues"], [])

    def test_p001_missing_price_question_fails_provenance(self):
        events, over = self.ouigo_fixture(questions=0)
        report = self.report([("P001", events, over)])
        self.assertEqual(report["score"]["points"], 0)
        self.assertEqual(report["cases"][0]["issues"], ["P"])
        self.assertEqual(report["failures"]["missing_facts"], 1)
        self.assertEqual(report["cases"][0]["provenance_issues"], ["missing topic ticket_price"])

    def test_question_after_filing_does_not_repair_provenance(self):
        events, over = self.ouigo_fixture(ask_after=True)
        report = self.report([("P001", events, over)])
        self.assertEqual(report["score"]["points"], 0)
        self.assertEqual(report["failures"]["provenance"], 1)

    def test_p003_correct_no_claim_requires_eligible_train_evidence(self):
        train_id = self.labels["P003"]["answer"]["no_claim"]["required_runs"][0]
        lookup = {"kind": "tool", "name": "find_trains", "args": {},
                  "result": {"trains": [{"train_id": train_id}]}}
        declaration = self.declaration("DB")
        report = self.report([("P003", [lookup, declaration], 0)])
        self.assertEqual(report["score"]["points"], 1)
        lookup["name"] = "departures"
        lookup["result"] = {"departures": [{"train_id": train_id}]}
        report = self.report([("P003", [lookup, declaration], 0)])
        self.assertEqual(report["score"]["points"], 0)
        self.assertEqual(report["failures"]["no_claim_train_evidence"], 1)

    def test_wrong_declarations_reduce_other_rows_before_set_floor(self):
        events, over = self.ouigo_fixture()
        wrong = [self.declaration("OUIGO") for _ in range(3)]
        report = self.report([("P001", events, over), ("P003", wrong, 0)])
        self.assertEqual(report["score"]["points"], 0)
        self.assertEqual(report["cases"][0]["net_points"], 1)
        self.assertEqual(report["cases"][1]["net_points"], -1.5)
        self.assertEqual(report["failures"]["wrong_no_claim"], 3)
        self.assertEqual(report["failures"]["extra_or_duplicate_claims"], 0)

    def test_no_claim_when_compensation_owed_is_penalized_once(self):
        events, over = self.ouigo_fixture()
        events.append(self.declaration("OUIGO"))
        report = self.report([("P001", events, over)])
        self.assertEqual(report["score"]["points"], 0.5)
        self.assertEqual(report["cases"][0]["points"], 1)
        self.assertEqual(report["cases"][0]["penalty"], 0.5)
        self.assertEqual(report["failures"]["wrong_no_claim"], 1)
        self.assertEqual(report["failures"]["extra_or_duplicate_claims"], 0)

    def test_official_traveler_question_limit_reduces_correct_claim(self):
        events, over = self.ouigo_fixture(questions=5)
        self.assertEqual(over, 2)
        report = self.report([("P001", events, over)])
        self.assertEqual(report["score"]["points"], 0.5)
        self.assertEqual(report["totals"]["correct"], 1)
        self.assertEqual(report["totals"]["fully_right"], 0)
        self.assertEqual(report["failures"]["questions_over"], 2)

    def test_all_36_empty_event_fixtures_score_zero_without_atp_noise(self):
        report = self.report([(rid, [], 0) for rid in self.labels])
        self.assertEqual(report["score"]["points"], 0)
        self.assertEqual(report["score"]["max"], 36)
        self.assertTrue(report["coverage"]["complete"])
        self.assertEqual(report["failures"]["missing_claim"], 26)
        self.assertEqual(report["failures"]["missing_no_claim"], 10)
        self.assertEqual(sum(report["failures"][key] for key in ("amount", "train", "provenance")), 0)
        self.assertEqual(report["totals"]["model_calls"], 0)
        self.assertEqual(report["totals"]["cost_usd"], 0)


if __name__ == "__main__":
    unittest.main()
