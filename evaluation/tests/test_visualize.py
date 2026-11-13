"""Tests for the static run explorer; no model calls and no network."""
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import visualize


def llm(n, t, calls, **extra):
    """One recorded model response with the given (name, arguments) tool calls."""
    return {
        "req": "P001", "kind": "llm", "n": n, "t": t, "model": "gpt-6-luna",
        "usage": {"backend": "course", "usd": 0.001, "seconds": 1.2,
                  "input_tokens": 10, "output_tokens": 2},
        "result": {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
            {"id": f"c{n}-{index}", "type": "function",
             "function": {"name": name, "arguments": json.dumps(args)}}
            for index, (name, args) in enumerate(calls)]}}]},
        **extra,
    }


class VisualizeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="visualize-test-", dir=Path(__file__).resolve().parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.run_dir = self.root / "20261110T120000Z-test-dev"
        self.run_dir.mkdir()
        self.write_run()

    def write_run(self):
        summary = {
            "set": "practice", "usd": 0.001, "cache_entries": 1, "cache_bytes": 100,
            "rows": [{"id": "P001", "points": 1.0, "penalty": 0.0, "max": 1, "correct": 1, "extra": 0,
                      "questions": 1, "limit": 3, "over": 0, "tools": 4, "model": 1,
                      "usd": 0.001, "status": "ok", "issues": [], "seconds": 2.5}],
            "totals": {"points": 1.0, "max": 1},
        }
        case = {
            "id": "P001", "case_type": "ouigo", "companies": ["OUIGO"], "decision": "claim",
            "present": True, "attempted": True, "points": 1.0, "penalty": 0.0, "net_points": 1.0,
            "max": 1, "correct": 1, "fully_right": True, "status": "ok", "issues": [],
            "questions": 1, "question_limit": 3, "questions_over": 0, "extra": 0,
            "tool_calls": 4, "model_calls": 1, "usd": 0.001, "seconds": 2.5,
            "checks": [{"company": "OUIGO", "filed": True, "amount": True, "train": True,
                        "provenance": True, "provenance_issues": [], "payment_ok": True, "ok": True}],
            "provenance_issues": [], "failures": {key: 0 for key in visualize.FAILURE_KEYS},
        }
        report = {
            "split": "dev", "cases": [case], "warnings": [],
            "score": {"points": 1.0, "max": 1, "percent": 100.0,
                      "official_points": 1.0, "official_max": 1, "official_totals_match": True},
            "coverage": {"expected": 1, "attempted": 1, "not_run": 0, "complete": True},
            "totals": {"correct": 1, "fully_right": 1, "penalty": 0, "questions": 1, "questions_over": 0,
                       "tool_calls": 4, "model_calls": 1, "cost_usd": 0.001},
            "failures": {key: 0 for key in visualize.FAILURE_KEYS},
            "statuses": {"ok": 1}, "groups": {}, "setup": "ok",
        }
        manifest = {
            "split": "dev", "ids": ["P001"], "started_utc": "2026-11-10T12:00:00+00:00",
            "system": "/tmp/team/system.py", "system_sha256": "abc", "model_enabled": True,
        }
        events = [
            {"req": "setup", "kind": "doc", "name": "list_rule_documents", "args": {}, "result": {"documents": 45}},
            {"req": "setup", "kind": "setup", "status": "ok", "cache_entries": 1, "cache_bytes": 100},
            llm(1, 1.0, [("search_rules", {"query": "delay OUIGO", "company": "OUIGO"})]),
            llm(2, 2.0, [("ask_traveler", {"topic": "ticket_price"})]),
            {"req": "P001", "kind": "tool", "n": 3, "t": 3.0, "name": "ask_traveler",
             "args": {"topic": "ticket_price"}, "result": "25 euros."},
            llm(4, 4.0, [("find_trains", {"origin": "Toulon", "destination": "Nice"}),
                         ("read_rule_document", {"doc_id": "fr-ouigo"})]),
            {"req": "P001", "kind": "doc", "n": 5, "t": 5.0, "name": "read_rule_document",
             "args": {"doc_id": "fr-ouigo"}, "result": {"chars": 2229}},
            {"req": "P001", "kind": "tool", "n": 6, "t": 6.0, "name": "find_trains",
             "args": {"origin": "Toulon", "destination": "Nice"},
             "result": {"trains": [{"train": "OUIGO 7853", "train_id": "tr-x",
                                    "dep_planned": "2026-10-20 20:15", "arr_planned": "2026-10-20 22:13",
                                    "arr_recorded": "2026-10-21 00:23:00", "arr_delay_min": 130}]}},
            llm(7, 7.0, [("file_claim", {"company": "OUIGO", "fields": {"montant_demande_eur": "12.50", "prix_trajet_eur": "25.00", "mode_indemnisation": "bon d'achat"}})]),
            {"req": "P001", "kind": "tool", "n": 8, "t": 8.0, "name": "file_claim",
             "args": {"company": "OUIGO", "fields": {"montant_demande_eur": "12.50", "prix_trajet_eur": "25.00", "mode_indemnisation": "bon d'achat"}},
             "result": {"status": "accepted", "claim_id": "OUI-0001",
                        "train": {"train": "OUIGO 7853", "train_id": "tr-x"}}},
            {"req": "P001", "kind": "grade",
             "grade": {"correct": 1, "max": 1, "extra_filings": 0,
                       "checks": [{"company": "OUIGO", "ok": True}]},
             "row": {"id": "P001", "points": 1.0, "penalty": 0.0, "issues": []}},
        ]
        (self.run_dir / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
        (self.run_dir / "evaluation.json").write_text(json.dumps(report), encoding="utf-8")
        (self.run_dir / "run_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        (self.run_dir / "log.jsonl").write_text(
            "\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")

    def test_load_run_builds_trace_and_metadata(self):
        run = visualize.load_run(self.run_dir)
        self.assertEqual(run["score"]["points"], 1.0)
        self.assertTrue(run["model_enabled"])
        self.assertEqual(run["log_info"]["docs"], 1)
        self.assertEqual(run["log_info"]["docs_in_requests"], 1)
        case = run["cases"][0]
        # Kit events are attached to the call that produced them; only the four
        # model turns and the grade remain as top-level rows.
        self.assertEqual([step["kind"] for step in case["trace"]],
                         ["model", "model", "model", "model", "grade"])
        self.assertEqual([step["turn"] for step in case["trace"][:4]], [1, 2, 3, 4])

    def write_answers(self, amount="12.50"):
        answer = {
            "claims": [{
                "company": "OUIGO", "amount": amount, "currency": "EUR", "train": "7853",
                "runs": ["tr-x"], "price": "25.00", "payment": "bon d'achat",
                "required_topics": [["ticket_price"]], "delay_min": 130,
                "planned_arrival": "2026-10-20 22:13", "recorded_arrival": "2026-10-21 00:23:00",
            }],
            "required_topics": [], "facts_needed": 1, "type": "ouigo",
            "notes": "25.00 EUR x 50% = 12.50 EUR.",
        }
        path = self.root / "practice_requests.json"
        path.write_text(json.dumps([{"id": "P001", "text": "request", "answer": answer}]), encoding="utf-8")
        return visualize.load_answers(path)

    def test_expected_answer_is_compared_with_filed_fields(self):
        answers = self.write_answers()
        self.assertEqual(list(answers), ["P001"])
        case = visualize.load_run(self.run_dir, answers=answers)["cases"][0]
        comparison = case["comparison"]
        claim = comparison["claims"][0]
        self.assertEqual(claim["company"], "OUIGO")
        self.assertTrue(claim["filed"])
        self.assertEqual(claim["actual"]["amount"], "12.50")
        self.assertEqual(claim["expected"]["amount"], "12.50")
        self.assertEqual(claim["checks"],
                         {"amount": True, "currency": None, "price": True, "payment": True, "train": True})
        self.assertEqual([topic["ok"] for topic in claim["topics"]], [True])
        self.assertEqual(comparison["type"], "ouigo")
        self.assertTrue(comparison["notes"].startswith("25.00 EUR"))

    def test_comparison_reports_an_amount_mismatch(self):
        case = visualize.load_run(self.run_dir, answers=self.write_answers(amount="13.00"))["cases"][0]
        claim = case["comparison"]["claims"][0]
        self.assertFalse(claim["checks"]["amount"])
        self.assertEqual(claim["expected"]["amount"], "13.00")
        self.assertEqual(claim["actual"]["amount"], "12.50")
        # The amount defect is the only mismatch: price, payment and train still agree.
        self.assertTrue(claim["checks"]["price"])
        self.assertTrue(claim["checks"]["payment"])
        self.assertTrue(claim["checks"]["train"])

    def test_runs_without_answers_have_no_comparison(self):
        case = visualize.load_run(self.run_dir)["cases"][0]
        self.assertIsNone(case["comparison"])
        self.assertIsNone(case["expected"])

    def test_missing_topic_is_reported_from_the_filing_snapshot(self):
        answers = self.write_answers()
        answers["P001"]["claims"][0]["required_topics"] = [["delay_reason"]]
        case = visualize.load_run(self.run_dir, answers=answers)["cases"][0]
        topic = case["comparison"]["claims"][0]["topics"][0]
        self.assertEqual(topic["group"], ["delay_reason"])
        self.assertEqual(topic["asked"], [])
        self.assertFalse(topic["ok"])

    def test_calls_carry_args_and_matching_events(self):
        trace = visualize.load_run(self.run_dir)["cases"][0]["trace"]
        search = trace[0]["calls"][0]
        self.assertEqual(search["name"], "search_rules")
        self.assertTrue(search["local"])
        self.assertEqual(search["events"], [])
        self.assertEqual(trace[1]["calls"][0]["events"][0]["result"], "25 euros.")
        names = [call["name"] for call in trace[2]["calls"]]
        self.assertEqual(names, ["find_trains", "read_rule_document"])
        find, read = trace[2]["calls"]
        self.assertEqual(find["events"][0]["result"]["trains"][0]["arr_delay_min"], 130)
        self.assertEqual(read["events"][0]["kind"], "doc")
        self.assertEqual(read["events"][0]["result"]["chars"], 2229)
        self.assertEqual(trace[3]["calls"][0]["events"][0]["result"]["status"], "accepted")

    def test_raw_response_is_kept_verbatim(self):
        trace = visualize.load_run(self.run_dir)["cases"][0]["trace"]
        response = trace[1]["response"]
        call = response["choices"][0]["message"]["tool_calls"][0]
        self.assertEqual(call["function"]["name"], "ask_traveler")
        self.assertEqual(json.loads(call["function"]["arguments"]), {"topic": "ticket_price"})
        self.assertEqual(trace[1]["usage"]["backend"], "course")
        self.assertEqual(trace[3]["calls"][0]["args"], {"company": "OUIGO", "fields": {"montant_demande_eur": "12.50", "prix_trajet_eur": "25.00", "mode_indemnisation": "bon d'achat"}})

    def test_helper_calls_keep_nested_kit_events(self):
        turn = {"kind": "llm", "t": 1.0, "n": 1, "usage": {}, "result": {"choices": [{"message": {"tool_calls": [
            {"function": {"name": "resolve_connection", "arguments": "{\"origin\":\"A\"}"}}]}}]}}
        steps = visualize.build_steps([
            turn,
            {"kind": "tool", "t": 2.0, "name": "ask_traveler", "args": {"topic": "q"}, "result": "x"},
            {"kind": "tool", "t": 3.0, "name": "find_trains", "args": {"origin": "A"}, "result": {}},
            {"kind": "tool", "t": 4.0, "name": "find_trains", "args": {"origin": "A"}, "result": {}},
        ])
        self.assertEqual(len(steps), 1)
        call = steps[0]["calls"][0]
        self.assertTrue(call["local"])
        self.assertEqual([event["name"] for event in call["events"]],
                         ["ask_traveler", "find_trains", "find_trains"])

    def test_unpaired_events_stay_visible(self):
        steps = visualize.build_steps([
            {"kind": "tool", "t": 1.0, "name": "departures", "args": {}, "result": {}},
            {"kind": "stdout", "text": "printed answer"},
        ])
        self.assertEqual([step["kind"] for step in steps], ["tool", "answer"])
        self.assertEqual(steps[1]["text"], "printed answer")

    def test_local_tools_are_known(self):
        self.assertIn("search_rules", visualize.LOCAL_TOOLS)
        self.assertIn("calculate_compensation", visualize.LOCAL_TOOLS)
        self.assertNotIn("ask_traveler", visualize.LOCAL_TOOLS)

    def test_missing_evaluation_json_is_rebuilt_from_summary(self):
        (self.run_dir / "evaluation.json").unlink()
        run = visualize.load_run(self.run_dir)
        self.assertEqual(run["score"]["points"], 1.0)
        self.assertEqual(run["cases"][0]["id"], "P001")

    def test_html_embeds_parseable_payload(self):
        run = visualize.load_run(self.run_dir)
        page = visualize.build_html([run], "Test Explorer")
        self.assertIn("<!doctype html>", page)
        match = re.search(r'<script id="run-data" type="application/json">(.*?)</script>', page, re.S)
        self.assertIsNotNone(match)
        payload = json.loads(match.group(1).replace("<\\/", "</"))
        self.assertEqual(payload["runs"][0]["cases"][0]["id"], "P001")
        self.assertEqual(len(payload["runs"][0]["cases"][0]["trace"]), 5)
        self.assertNotIn("</script>", match.group(1))

    def test_discover_runs_accepts_parent_folder(self):
        runs = visualize.discover_runs([self.root])
        self.assertEqual([run.name for run in runs], [self.run_dir.name])


if __name__ == "__main__":
    unittest.main()
