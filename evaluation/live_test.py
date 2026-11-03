"""Run a small, capped live test; read the course token without saving it."""

import argparse
from datetime import datetime, timezone
import getpass
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
sys.path.insert(0, str(HERE))
from reporting import make_report, render_markdown


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--probe", action="store_true")
    mode.add_argument("--cases", help="Explicit development IDs, separated by commas")
    parser.add_argument("--cap", type=float, required=True, help="Run cap, at most $0.10")
    args = parser.parse_args()
    if not math.isfinite(args.cap) or not 0 < args.cap <= 0.10:
        parser.error("The live-test cap must be between 0 and 0.10 USD.")
    splits = json.loads((HERE / "splits.json").read_text(encoding="utf-8"))
    ids = ["P001"] if args.probe else [item.strip() for item in args.cases.split(",") if item.strip()]
    if not ids or len(ids) != len(set(ids)) or not set(ids) <= set(splits["dev"]):
        parser.error("Choose distinct IDs from the development split.")
    token = getpass.getpass("Temporary course token (hidden): ").strip()
    if not token:
        parser.error("No token supplied.")
    environment = os.environ.copy()
    environment["P2_TOKEN"] = token
    environment["P2_BASE_URL"] = "https://eais-discussion-production.up.railway.app/api/p2"
    del token
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    label = "access-probe" if args.probe else "live-smoke"
    out = PROJECT / "runs" / f"{stamp}-{os.getpid()}-{label}"
    out.mkdir(parents=True, exist_ok=False)
    system = "/workspace/eais-p2-kit/examples/probe_llm.py"
    if not args.probe:
        # Run the snapshot, so a later edit cannot change a running experiment.
        shutil.copytree(PROJECT / "team", out / "source", ignore=shutil.ignore_patterns("__pycache__"))
        system = "/workspace/" + (out / "source/system.py").relative_to(PROJECT).as_posix()
    relative_out = "/workspace/" + out.relative_to(PROJECT).as_posix()
    command = ["docker", "run", "--rm", "--mount", f"type=bind,source={PROJECT},target=/workspace",
               "--workdir", "/workspace", "--env", "P2_TOKEN", "--env", "P2_BASE_URL", "eais-p2:py310",
               "python", "-I", "eais-p2-kit/run_practice.py", "--system", system,
               "--only", ",".join(ids), "--max-cost-usd", str(args.cap), "--timeout-s", "180",
               "--out", relative_out]
    manifest = {"kind": label, "ids": ids, "cap_usd": args.cap,
                "source_sha256": None if args.probe else hashlib.sha256((out / "source/system.py").read_bytes()).hexdigest(),
                "note": "Token supplied in memory and forwarded by environment name only."}
    (out / "live_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Run: {out}\nCost cap: ${args.cap:.3f}", flush=True)
    try:
        completed = subprocess.run(command, env=environment, check=False)
    finally:
        environment.pop("P2_TOKEN", None)
    summary_path = out / "summary.json"
    if not summary_path.is_file():
        raise SystemExit(f"Docker/runner did not produce a summary (exit {completed.returncode}).")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if not args.probe:
        cases = json.loads((PROJECT / "eais-p2-kit/practice/practice_requests.json").read_text(encoding="utf-8"))
        report = make_report(summary_path, {case["id"]: case for case in cases}, ids, "live-development-smoke")
        (out / "evaluation.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        (out / "evaluation.md").write_text(render_markdown(report), encoding="utf-8")
    print(f"Actual spend: ${summary['usd']:.6f}\nSaved results: {out}", flush=True)
    failed_calls = any(call.get("error") for call in summary.get("model_calls", []))
    return completed.returncode or (2 if failed_calls else 0)


if __name__ == "__main__":
    raise SystemExit(main())
