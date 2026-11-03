"""Chat with your system in the browser: you type a traveler's request, your system runs on it, and you answer its
questions to the traveler yourself. Or replay a practice request and see its score.

Usage, from the kit folder:
  python3 -I chat.py --system path/to/your_system.py [--max-cost-usd 0.25] [--trains data/trains.sqlite]
                     [--timeout-s 600] [--questions 6] [--port 0] [--no-browser] [--sandbox] [--out DIR]
                     [--rules-extra DIR ...] [--forms-extra DIR ...]

Each request is one run, as in run_practice.py: solve(request_text, tools) in a new process, with the same tools,
budgets and log. Unlike run_practice.py, every request uses the same cache folder: setup(tools), if you have it, runs
with the first request and again only after a file changes in your system's folder, the rule documents, the forms,
the train store or the kit, or after "Rebuild index". After a change of the train store, we also open it again, so
that your next request searches the new data. The time you take to answer the traveler's questions doesn't
count against the time limit. --questions is the traveler's question limit for your own requests; a practice request
keeps its own limit. Model calls go to the course app, with P2_BASE_URL and P2_TOKEN, as in run_practice.py.
--rules-extra and --forms-extra add your own folders of rule documents and claim forms, in the same layout as rules/
and forms/, to the kit's folders.

The page runs on this computer only (127.0.0.1). Stop the server with Ctrl+C.
"""
import argparse
import json
import os
import sys
import threading
import time
import webbrowser
from pathlib import Path

KIT = Path(__file__).resolve().parent
sys.path.insert(0, str(KIT))

from chat_ui.server import STATIC, Hub, cancellable_post, cancellable_sleep, serve  # noqa: E402
from p2kit.data import TrainData  # noqa: E402
from p2kit.forms import FormDesk  # noqa: E402
from p2kit.isolation import team_packages  # noqa: E402
from p2kit.llm import ModelGateway, http_post  # noqa: E402
from p2kit.rules import RuleLibrary  # noqa: E402


def extra_dirs(rules_extra=(), forms_extra=()):
    """The kit's rules/ and forms/ folders, followed by the folders of --rules-extra and --forms-extra. A rules folder
    needs its index.json. We load each folder once here, so that we stop with a message naming the option for a folder
    that doesn't exist or that we can't load, e.g., a rules folder given to --forms-extra."""
    rule_dirs, form_dirs = [KIT / "rules"], [KIT / "forms"]
    for option, given, out, load in (("--rules-extra", rules_extra, rule_dirs, RuleLibrary),
                                     ("--forms-extra", forms_extra, form_dirs, FormDesk)):
        for d in given:
            if not Path(d).is_dir():
                raise SystemExit(f"{option} {d}: there is no folder at this path.")
            if option == "--rules-extra" and not (Path(d) / "index.json").is_file():
                raise SystemExit(f"{option} {d}: the folder has no index.json, so it isn't in the same layout as rules/.")
            try:
                load([d])
            except Exception as e:
                raise SystemExit(f"{option} {d}: we can't load the folder ({type(e).__name__}: {e}).") from None
            out.append(Path(d).resolve())
    return rule_dirs, form_dirs


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="Chat with your system in the browser.")
    ap.add_argument("--system", required=True, help="your system: a .py file with solve(request_text, tools)")
    ap.add_argument("--max-cost-usd", type=float, default=0.25, help="cost cap while chat.py runs (default 0.25)")
    ap.add_argument("--trains", default=os.environ.get("P2_TRAINS") or str(KIT / "data/trains.sqlite"),
                    help="the train store (default: P2_TRAINS, or data/trains.sqlite)")
    ap.add_argument("--timeout-s", type=float, default=600, help="time per request in seconds (default 600)")
    ap.add_argument("--questions", type=int, default=6, help="question limit for your own requests (default 6)")
    ap.add_argument("--port", type=int, default=0, help="port on 127.0.0.1 (default: any free port)")
    ap.add_argument("--no-browser", action="store_true", help="don't open the browser")
    ap.add_argument("--sandbox", action="store_true", help="run your system in bubblewrap, as in our grading runs, "
                    "with the packages of packages.txt from this Python")
    ap.add_argument("--out", default="", help="folder for the logs (default runs/<time>-<process id>-chat-<system>)."
                    " If you give --out to several runs at once, give each run its own folder.")
    ap.add_argument("--rules-extra", action="append", default=[], metavar="DIR",
                    help="another folder of rule documents, in the same layout as rules/ (you can give it more than once)")
    ap.add_argument("--forms-extra", action="append", default=[], metavar="DIR",
                    help="another folder of claim forms, in the same layout as forms/ (you can give it more than once)")
    a = ap.parse_args(argv)
    rule_dirs, form_dirs = extra_dirs(a.rules_extra, a.forms_extra)
    if not Path(a.system).is_file():
        raise SystemExit(f"{a.system} is not a file")
    if not Path(a.trains).is_file():
        if Path(a.trains).resolve() == (KIT / "data/trains.sqlite").resolve():
            raise SystemExit("There is no train store yet, so first run python3 -I make_store.py in the kit folder "
                             "(once, about 25 seconds).")
        raise SystemExit(f"There is no train store at {a.trains}, so check the path you gave with --trains or P2_TRAINS.")
    if not (STATIC / "index.html").is_file():
        raise SystemExit(f"The chat page is missing: {STATIC / 'index.html'}")
    practice = json.loads((KIT / "practice/practice_requests.json").read_text())
    out = Path(a.out) if a.out else KIT / "runs" / f"{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}-chat-{Path(a.system).stem}"
    gateway = ModelGateway.from_env(a.max_cost_usd, default_backend="course", post=cancellable_post(http_post),
                                    sleep=cancellable_sleep)
    hub = Hub(system=str(Path(a.system).resolve()), gateway=gateway, data=TrainData(a.trains),
              rule_dirs=rule_dirs, form_dirs=form_dirs, out=out, practice=practice,
              timeout_s=a.timeout_s, sandbox=a.sandbox, questions=max(1, a.questions),
              packages=team_packages() if a.sandbox else (), trains=a.trains)
    server, _ = serve(hub, a.port)
    url = f"http://127.0.0.1:{server.server_address[1]}/#token={hub.token}"
    print(f"Chat page: {url}", flush=True)
    print("If no browser opens, e.g. on WSL, copy the whole address, with #token=, into your browser.", flush=True)
    print(f"Logs: {out}", flush=True)
    problem = gateway.settings_problem()
    if problem:
        print(f"No model calls: {problem}", flush=True)
    print("Stop with Ctrl+C.", flush=True)
    if not a.no_browser:
        threading.Thread(target=webbrowser.open, args=(url,), daemon=True).start()
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nStopping.", flush=True)
    finally:
        with hub.cond:
            if hub.run is not None and not hub.run.final:
                hub.run.stop()
        run = hub.run
        if run is not None:
            run.thread.join(timeout=10)
        server.shutdown()
        if run is None or not run.thread.is_alive():
            hub.close()


if __name__ == "__main__":
    main()
