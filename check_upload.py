"""Check your upload before you hand it in on Gradescope.

Usage, from the starter code folder:
  python3 -I check_upload.py upload.zip [--only P001,P016] [--no-sandbox] [--model] [--max-cost-usd 0.05]
                             [--trains data/trains.sqlite] [--out DIR]

First, we check the upload as our Gradescope upload check does, with the same code (p2/upload.py) and the same
messages: we find system.py, at the top of the upload or in its one top folder, check that it defines
solve(request_text, tools), and check that every import is from the standard library, your own modules, p2 or
packages.txt. Then we run your system from the unpacked upload on two practice requests, as we do on Gradescope:
setup(tools) once, then solve() for each request, in the sandbox if bubblewrap is installed. By default we make no
model calls, so each call of tools.llm returns an error, and the check costs nothing; with --model, the model calls
go to the course app, as in run_practice.py.

You can also give a folder instead of a zip. The exit code is 0 when the upload passes both checks.
"""
import argparse
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

STARTER = Path(__file__).resolve().parent
sys.path.insert(0, str(STARTER))

from p2 import upload  # noqa: E402
from p2.data import TrainData  # noqa: E402
from p2.isolation import SandboxError, team_packages  # noqa: E402
from p2.llm import ModelGateway  # noqa: E402
from p2.runner import load_practice, pick_requests, run_set  # noqa: E402
from p2.score import failed_calls  # noqa: E402

GRADED_PYTHON = (3, 10)
TIMEOUT_S = 180  # seconds per request in the Gradescope quick score


def main(argv=None):
    ap = argparse.ArgumentParser(description="Check your upload before you hand it in on Gradescope.")
    ap.add_argument("upload", help="your upload: the zip file that you hand in, or a folder")
    ap.add_argument("--only", default="P001,P016", help="the practice requests to run (default P001,P016)")
    ap.add_argument("--no-sandbox", action="store_true", help="run your system without bubblewrap")
    ap.add_argument("--model", action="store_true",
                    help="make model calls through the course app, with P2_BASE_URL and P2_TOKEN")
    ap.add_argument("--max-cost-usd", type=float, default=0.05, help="cost cap with --model (default 0.05)")
    ap.add_argument("--trains", default=str(STARTER / "data/trains.sqlite"), help="the train store")
    ap.add_argument("--out", default="", help="folder for the log (default runs/<time>-<process id>-check-upload)."
                    " If you give --out to several runs at once, give each run its own folder.")
    a = ap.parse_args(argv)
    src = Path(a.upload)
    if not src.exists():
        raise SystemExit(f"{src} doesn't exist")
    if not Path(a.trains).is_file():
        raise SystemExit("There is no train store yet, so first run python3 -I make_store.py in the starter code folder "
                         "(once, about 25 seconds).")
    if sys.version_info[:2] != GRADED_PYTHON:
        print(f"Note: you run Python {sys.version_info[0]}.{sys.version_info[1]}, and we grade with Python "
              f"{GRADED_PYTHON[0]}.{GRADED_PYTHON[1]}.")

    work = Path(tempfile.mkdtemp(prefix="p2-upload-"))
    try:
        copy = work / "upload"
        print("1. The upload check, as on Gradescope")
        try:  # both with the limits of the upload check, counted before we write anything
            notes = upload.copy_upload(src, copy) if src.is_dir() else upload.unpack_zip(src, copy)
        except upload.UploadError as e:
            print(f"- {e}")
            return fail()
        res = upload.check_structure(copy)
        res.notes[:0] = notes
        print(upload.structure_text(res.as_dict()))
        if not res.ok:
            return fail()

        print()
        print("2. A run of your system on two practice requests, from the unpacked upload")
        sandbox = not a.no_sandbox and shutil.which("bwrap") is not None
        if not a.no_sandbox and not sandbox:
            print("bubblewrap isn't installed, so we run your system without the sandbox. On Ubuntu or WSL, you "
                  "install it with: sudo apt install bubblewrap")
        if a.model:
            gateway = ModelGateway.from_env(a.max_cost_usd, default_backend="course")
        else:
            gateway = ModelGateway(0, "off")
        requests, facts, key = load_practice(STARTER / "practice/practice_requests.json")
        requests = pick_requests(requests, a.only)
        out = Path(a.out) if a.out else STARTER / "runs" / f"{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}-check-upload"
        try:
            summary = run_set(res.system_path(), set_name="upload check", requests=requests, facts=facts, key=key,
                              data=TrainData(a.trains), rule_dirs=[STARTER / "rules"], form_dirs=[STARTER / "forms"],
                              gateway=gateway, out=out, timeout_s=TIMEOUT_S, show_checks=False, sandbox=sandbox,
                              packages=team_packages() if sandbox else ())
        except SandboxError as e:
            print(f"We can't run your system in the sandbox: {e}")
            return fail()
        broken = [r["id"] for r in summary["rows"] if r["status"].startswith("error:") or r["status"] == "time"]
        print()
        if str(summary.get("setup", "")).startswith("error:"):
            print(f"Your setup(tools) ended with {summary['setup']}, so check the setup line above.")
            return fail()
        if broken:
            print(f"Your system ended with an error or ran out of time on {', '.join(broken)}, so check the status "
                  f"column above and the log in {out / 'log.jsonl'}.")
            if not a.model and failed_calls(gateway.calls):
                print('We made no model calls in this check, so each call of tools.llm returned {"error": "..."}. '
                      'If your system reads resp["choices"] without checking for "error" first, the error can come '
                      "from that. Handle the error in your system, or run the check with --model, which makes the "
                      "model calls through the course app.")
            return fail()
        print(f"Your upload passed both checks. Hand in {src.name} on Gradescope.")
        return 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


def fail():
    print()
    print("Your upload didn't pass the check, so fix the problem above and run the check again before you hand it in.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
