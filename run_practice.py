"""Run your system on the practice requests and print the same score lines as the grader.

Usage, from the starter code folder:
  python3 -I run_practice.py --system path/to/your_system.py [--only P001,P003] [--max-cost-usd 0.25]
                             [--trains data/trains.sqlite] [--timeout-s 600] [--out DIR] [--no-model] [--sandbox]
                             [--sandbox-ro PATH ...]

Your system runs in processes of its own, and its `tools` object sends every call to this runner. We call your
setup(tools) once per run, if you have one, in its own process, and solve(request_text, tools) once per request, each
time in a new process. setup() can write files into tools.cache_dir; solve() can read them. With --sandbox (Linux
with bubblewrap), your system runs as in our grading runs: no network, and read-only access to your code's folder
and the starter code only. We mount the packages of packages.txt that this Python has installed, e.g. in your venv,
read-only, as we do with the package folder in grading, and no other package. --sandbox-ro adds another read-only
folder to the path.

Model calls go to the course app. Set these environment variables:
  P2_BASE_URL  the course app's Project 2 address, from the Project 2 page of the course app
  P2_TOKEN     your token from the course app; your calls are paid from your team's budget
We remove P2_TOKEN from the environment before we start your system. With --no-model, we make no model calls:
each call of tools.llm returns an error, at no cost, so you can try the tools without the course app.

The train store is --trains, or the file in P2_TRAINS, or data/trains.sqlite next to this file. You build
data/trains.sqlite once from the practice files with: python3 -I make_store.py (about 25 seconds, 0.7 GB)
The log of every call goes to runs/<time>-<process id>-<system>/log.jsonl, next to this file, unless you give --out.
"""

import argparse
import os
import sys
import time
from pathlib import Path

STARTER = Path(__file__).resolve().parent
sys.path.insert(0, str(STARTER))

from p2.data import TrainData  # noqa: E402
from p2.isolation import SandboxError, team_packages  # noqa: E402
from p2.llm import ModelGateway  # noqa: E402
from p2.runner import load_practice, pick_requests, run_set  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description="Run a system on the practice requests.")
    ap.add_argument(
        "--system",
        required=True,
        help="your system: a .py file with solve(request_text, tools)",
    )
    ap.add_argument("--only", default="", help="request ids, e.g. P001,P003")
    ap.add_argument(
        "--max-cost-usd",
        type=float,
        default=0.25,
        help="cost cap for the whole run (default 0.25)",
    )
    ap.add_argument(
        "--trains",
        default=os.environ.get("P2_TRAINS") or str(STARTER / "data/trains.sqlite"),
        help="the train store (default: P2_TRAINS, or data/trains.sqlite)",
    )
    ap.add_argument(
        "--timeout-s",
        type=float,
        default=600,
        help="time per request in seconds (default 600)",
    )
    ap.add_argument(
        "--out",
        default="",
        help="folder for log.jsonl and summary.json (default "
        "runs/<time>-<process id>-<system>). If you give --out to several runs at once, give each run its "
        "own folder.",
    )
    ap.add_argument(
        "--no-model",
        action="store_true",
        help="make no model calls: each call of tools.llm returns an error, at no cost",
    )
    ap.add_argument(
        "--sandbox",
        action="store_true",
        help="run your system in bubblewrap, as in our grading runs, "
        "with the packages of packages.txt from this Python",
    )
    ap.add_argument(
        "--sandbox-ro",
        action="append",
        default=[],
        help="with --sandbox: another read-only folder for your system's imports; mounted at /extra/<n> "
        "and added to the path",
    )
    a = ap.parse_args(argv)
    if not Path(a.trains).is_file():
        if Path(a.trains).resolve() == (STARTER / "data/trains.sqlite").resolve():
            raise SystemExit(
                "There is no train store yet, so first run python3 -I make_store.py in the starter code folder "
                "(once, about 25 seconds)."
            )
        raise SystemExit(
            f"There is no train store at {a.trains}, so check the path you gave with --trains or P2_TRAINS."
        )
    requests, facts, key = load_practice(STARTER / "practice/practice_requests.json")
    requests = pick_requests(requests, a.only)
    # The process id keeps two runs that start in the same second apart.
    out = (
        Path(a.out)
        if a.out
        else STARTER
        / "runs"
        / f"{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}-{Path(a.system).stem}"
    )
    if not Path(a.system).is_file():
        raise SystemExit(f"{a.system} is not a file")
    gateway = ModelGateway.from_env(a.max_cost_usd, default_backend="course")
    if a.no_model:
        gateway = ModelGateway(a.max_cost_usd, "off")
    packages = team_packages() if a.sandbox else ()
    try:
        summary = run_set(
            a.system,
            set_name="practice",
            requests=requests,
            facts=facts,
            key=key,
            data=TrainData(a.trains),
            rule_dirs=[STARTER / "rules"],
            form_dirs=[STARTER / "forms"],
            gateway=gateway,
            out=out,
            timeout_s=a.timeout_s,
            show_checks=True,
            sandbox=a.sandbox,
            extra_ro=a.sandbox_ro,
            packages=packages,
        )
    except SandboxError as e:
        raise SystemExit(f"can't run the system: {e}") from None
    print(
        f"The log is {out / 'log.jsonl'}. What your system printed is in the \"stdout\" line of each request, "
        'and each tool call and its result are in a "tool" line.'
    )
    return summary


if __name__ == "__main__":
    main()
