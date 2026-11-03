"""Validate split boundaries and reporting through the command-line entry point."""
import contextlib
import csv
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

EVALUATION = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EVALUATION))
import evaluate


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cli-test-", dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)

    def invoke(self, *args):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return evaluate.main(list(args))

    def test_fixed_split_covers_companies_and_preserves_singleton(self):
        cases, splits = evaluate.load_benchmark()
        self.assertEqual((len(splits["dev"]), len(splits["holdout"])), (26, 10))
        self.assertIn("P024", splits["dev"])
        for name in ("dev", "holdout"):
            composition = evaluate.describe(cases, splits[name])
            self.assertEqual(len(composition["companies"]), 6)
        self.assertEqual(len(evaluate.describe(cases, splits["holdout"])["case_types"]), 10)

    def test_changed_labels_are_rejected(self):
        kit = self.folder / "kit"
        (kit / "practice").mkdir(parents=True)
        (kit / "practice/practice_requests.json").write_text("[]", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Practice data changed"):
            evaluate.load_benchmark(kit)

    def test_split_overlap_is_rejected(self):
        splits = evaluate.read_json(EVALUATION / "splits.json")
        splits["holdout"].append(splits["dev"][0])
        path = self.folder / "splits.json"
        path.write_text(json.dumps(splits), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "partition"):
            evaluate.load_benchmark(split_file=path)

    def test_report_keeps_absent_cases_and_exports_csv(self):
        cases, splits = evaluate.load_benchmark()
        rid = splits["dev"][0]
        row = {"id": rid, "points": 1, "penalty": 0, "max": 1, "correct": 1,
               "status": "ok", "issues": [], "usd": 0.001, "seconds": 2}
        (self.folder / "summary.json").write_text(json.dumps({"rows": [row], "usd": 0.001}), encoding="utf-8")
        self.assertEqual(self.invoke("report", "--run", str(self.folder), "--split", "dev"), 2)
        report = evaluate.read_json(self.folder / "evaluation.json")
        self.assertEqual(report["score"]["max"], 26)
        self.assertEqual(report["coverage"]["attempted"], 1)
        with (self.folder / "cases.csv").open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 26)
        self.assertEqual(rows[0]["case_type"], cases[rid]["answer"]["type"])
        self.assertEqual(rows[0]["usd"], "0.001")
        self.assertTrue((self.folder / "evaluation.md").is_file())

    def test_report_requires_explicit_split_for_external_run(self):
        with self.assertRaises(SystemExit) as caught:
            self.invoke("report", "--run", str(self.folder))
        self.assertEqual(caught.exception.code, 2)

    def test_manifest_prevents_relabelling_holdout_as_development(self):
        (self.folder / "run_manifest.json").write_text(json.dumps({"split": "holdout"}), encoding="utf-8")
        with self.assertRaises(SystemExit) as caught:
            self.invoke("report", "--run", str(self.folder), "--split", "dev")
        self.assertEqual(caught.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
