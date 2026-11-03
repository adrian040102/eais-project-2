"""Run fixed practice splits with the course evaluator and summarize its results."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone


HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
KIT = PROJECT / "eais-p2-kit"
sys.path.insert(0, str(HERE))

from reporting import make_report, render_markdown


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_benchmark(kit=KIT, split_file=HERE / "splits.json"):
    """Reject dataset drift and splits that omit, repeat, or invent a case."""
    dataset = Path(kit) / "practice" / "practice_requests.json"
    splits = read_json(split_file)
    if sha256(dataset) != splits["dataset_sha256"]:
        raise ValueError("Practice data changed. Review and version the split before evaluating it.")
    cases = read_json(dataset)
    by_id = {case["id"]: case for case in cases}
    if len(by_id) != len(cases):
        raise ValueError("Practice data contains duplicate request IDs.")
    dev, holdout = splits["dev"], splits["holdout"]
    combined = dev + holdout
    if len(combined) != len(set(combined)) or set(combined) != set(by_id):
        raise ValueError("Development and holdout splits must partition all practice requests exactly once.")
    if not dev or not holdout:
        raise ValueError("Both development and holdout splits must be nonempty.")
    for case in cases:
        answer = case["answer"]
        if not answer.get("claims") and not answer.get("no_claim"):
            raise ValueError(f"{case['id']} has no labelled outcome.")
    return by_id, splits


def split_ids(cases, splits, name):
    return list(cases) if name == "all" else list(splits[name])


def describe(cases, ids):
    from collections import Counter

    companies, types = Counter(), Counter()
    claims = no_claims = maximum = 0
    for rid in ids:
        answer = cases[rid]["answer"]
        expected = answer.get("claims", [])
        names = {claim["company"] for claim in expected}
        if expected:
            claims += 1
        else:
            no_claims += 1
            names.add(answer["no_claim"]["company"])
        companies.update(names)
        types.update([answer.get("type", "unspecified")])
        maximum += len(expected) or 1
    return {"requests": len(ids), "maximum_points": maximum, "claim_requests": claims,
            "no_claim_requests": no_claims, "companies": dict(sorted(companies.items())),
            "case_types": dict(sorted(types.items())), "ids": ids}


def write_reports(run_dir, cases, ids, split):
    run_dir = Path(run_dir).resolve()
    report = make_report(run_dir / "summary.json", cases, ids, split)
    (run_dir / "evaluation.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (run_dir / "evaluation.md").write_text(render_markdown(report), encoding="utf-8")
    fields = ["id", "case_type", "companies", "decision", "points", "penalty", "net_points", "max",
              "status", "issues", "tool_calls", "model_calls", "questions", "question_limit", "questions_over", "usd", "seconds"]
    with (run_dir / "cases.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in report["cases"]:
            writer.writerow({key: " | ".join(str(v) for v in value) if isinstance(value, list) else value
                             for key, value in row.items() if key in fields})
    print(f"Report: {run_dir / 'evaluation.md'}")
    print(f"Score: {report['score']['points']:.2f}/{report['score']['max']} "
          f"({report['score']['percent']:.1f}%)")
    print(f"Coverage: {report['coverage']['attempted']}/{report['coverage']['expected']} attempted")
    return report


def run_evaluation(args, cases, splits):
    if os.name != "posix":
        raise ValueError("The supplied runner requires Unix process APIs (resource and os.statvfs). "
                         "On Windows, use .\\run.ps1 to run it in Docker with Python 3.10. "
                         "Inventory and report also work directly on Windows.")
    system = Path(args.system).resolve()
    if not system.is_file():
        raise ValueError(f"System file not found: {system}")
    runner = Path(args.kit).resolve() / "run_practice.py"
    trains = Path(args.trains).resolve() if args.trains else Path(args.kit).resolve() / "data" / "trains.sqlite"
    if not trains.is_file():
        raise ValueError(f"Train store not found: {trains}. Build it with python -I make_store.py in the kit folder.")
    if args.model and not all(os.environ.get(key) for key in ("P2_TOKEN", "P2_BASE_URL")):
        raise ValueError("--model requires P2_TOKEN and P2_BASE_URL in the environment.")
    if not math.isfinite(args.max_cost_usd) or args.max_cost_usd <= 0:
        raise ValueError("--max-cost-usd must be a finite positive number.")
    if not math.isfinite(args.timeout_s) or args.timeout_s <= 0:
        raise ValueError("--timeout-s must be a finite positive number.")
    ids = split_ids(cases, splits, args.split)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = Path(args.out).resolve() if args.out else PROJECT / "runs" / f"{stamp}-{os.getpid()}-{args.split}-{system.stem}"
    # The official runner overwrites logs in an existing output directory.
    out.mkdir(parents=True, exist_ok=False)
    command = [args.python, "-I", str(runner), "--system", str(system), "--only", ",".join(ids),
               "--trains", str(trains), "--max-cost-usd", str(args.max_cost_usd),
               "--timeout-s", str(args.timeout_s), "--out", str(out)]
    if not args.model:
        command.append("--no-model")
    if args.sandbox:
        command.append("--sandbox")
    runner_version = subprocess.run([args.python, "--version"], capture_output=True, text=True, check=True)
    version = (runner_version.stdout or runner_version.stderr).strip()
    manifest = {"schema_version": 1, "started_utc": stamp, "split": args.split, "ids": ids,
                "split_version": splits["version"], "dataset_sha256": splits["dataset_sha256"],
                "splits_sha256": sha256(HERE / "splits.json"),
                "kit_manifest_sha256": sha256(Path(args.kit) / "MANIFEST.json"),
                "system": str(system), "system_sha256": sha256(system), "runner_python": version,
                "grading_python_matches": version.startswith("Python 3.10."),
                "model_enabled": args.model, "max_cost_usd": args.max_cost_usd,
                "timeout_s": args.timeout_s, "sandbox": args.sandbox, "command": command}
    (out / "run_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Running {len(ids)} {args.split} cases; model calls {'enabled' if args.model else 'disabled'}.", flush=True)
    completed = subprocess.run(command, cwd=str(Path(args.kit).resolve()), check=False)
    manifest["runner_exit_code"] = completed.returncode
    manifest["finished_utc"] = datetime.now(timezone.utc).isoformat()
    (out / "run_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    if not (out / "summary.json").is_file():
        raise ValueError(f"Runner exited with code {completed.returncode} without summary.json. See {out}.")
    report = write_reports(out, cases, ids, args.split)
    if completed.returncode:
        return completed.returncode
    return 0 if report["coverage"]["complete"] else 2


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kit", type=Path, default=KIT, help="Unchanged extracted course kit")
    subs = parser.add_subparsers(dest="command", required=True)
    inventory = subs.add_parser("inventory", help="Validate labels and show fixed split composition")
    inventory.add_argument("--out", type=Path, help="Also save the inventory as JSON")
    run = subs.add_parser("run", help="Launch the official runner on one split (Unix or the Docker runtime)")
    run.add_argument("--system", required=True, help="Agent file with solve(request_text, tools)")
    run.add_argument("--split", choices=("dev", "holdout", "all"), default="dev")
    run.add_argument("--python", default=sys.executable, help="Runner interpreter, ideally Python 3.10")
    run.add_argument("--trains", help="Override the kit's train store")
    run.add_argument("--out", help="New directory for raw output and reports; must not exist")
    run.add_argument("--max-cost-usd", type=float, default=0.25)
    run.add_argument("--timeout-s", type=float, default=600)
    run.add_argument("--model", action="store_true", help="Enable paid model calls; default is --no-model")
    run.add_argument("--sandbox", action="store_true", help="Use the official bubblewrap sandbox")
    report = subs.add_parser("report", help="Summarize an existing official summary.json and log.jsonl")
    report.add_argument("--run", required=True, type=Path, help="Run directory, or its summary.json")
    report.add_argument("--split", choices=("dev", "holdout", "all"), help="Required for runs without run_manifest.json")
    args = parser.parse_args(argv)
    try:
        cases, splits = load_benchmark(args.kit)
        if args.command == "inventory":
            result = {"split_version": splits["version"], "dataset_sha256": splits["dataset_sha256"],
                      "interpretation": splits["interpretation"],
                      "splits": {name: describe(cases, split_ids(cases, splits, name)) for name in ("dev", "holdout", "all")}}
            output = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
            if args.out:
                args.out.write_text(output, encoding="utf-8")
            print(output, end="")
            return 0
        if args.command == "run":
            return run_evaluation(args, cases, splits)
        folder = args.run.resolve()
        if folder.is_file():
            folder = folder.parent
        manifest_path = folder / "run_manifest.json"
        manifest = read_json(manifest_path) if manifest_path.is_file() else {}
        split = args.split or manifest.get("split")
        if split not in ("dev", "holdout", "all"):
            raise ValueError("Specify --split dev, holdout, or all so incomplete runs retain the full denominator.")
        if args.split and manifest.get("split", args.split) != args.split:
            raise ValueError("--split disagrees with this run's manifest.")
        if manifest.get("dataset_sha256", splits["dataset_sha256"]) != splits["dataset_sha256"]:
            raise ValueError("Run manifest belongs to different practice data.")
        ids = split_ids(cases, splits, split)
        if manifest.get("ids", ids) != ids:
            raise ValueError("Run manifest IDs disagree with this split version.")
        result = write_reports(folder, cases, ids, split)
        return 0 if result["coverage"]["complete"] else 2
    except (ValueError, OSError, KeyError, subprocess.SubprocessError) as exc:
        parser.exit(2, f"Evaluation error: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
