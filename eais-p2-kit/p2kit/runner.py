"""Run a system on a set of requests: the team's processes, a fresh tools object per request, the budgets, the log
and the score lines.

The practice runner (kit/run_practice.py) and the staff grader (grader/run.py) both call run_set, so they print the
same lines and write the same log and summary. The team's code runs in child processes (p2kit/isolation.py and
p2kit/child.py): one process for setup(tools), once per run, if the system has it, and then one fresh process for
each solve(request_text, tools). We kill each process, with everything in it, before we start the next one. setup()
can write to the run's cache folder (tools.cache_dir); the request processes get it read-only. Everything that counts,
costs or scores happens here, in the runner's process. chat.py alone passes a SetupCache, which keeps the cache folder
from one run to the next.
"""
import hashlib
import json
import os
import re
import stat
import tempfile
import time
from pathlib import Path

from .forms import FormDesk
from .isolation import (SANDBOX_CACHE, SETUP_METHODS, ChildProcess, Dispatcher, SandboxPlan, cache_usage,
                        check_sandbox_paths, empty_folder)
from .rules import RuleLibrary
from .score import HEADER, check_keys, failed_calls, grade_request, issue_codes, no_claim_carry, points, row_line, totals
from .tools import Tools
from .traveler import TOPICS, Traveler

TIME_BUDGET_STOP = "time budget of the run used up"  # the stop message for start_by
STOP_STATUS = {"BudgetExceeded": "budget", "TimeLimit": "time", "CostLimit": "cost-cap", "ModelAccessError": "no-model"}
END_STATUS = {"timeout": "time", "exited": "error:ProcessExited", "protocol": "error:Protocol",
              "limit": "error:CacheTooLarge"}
LOG_LINE_MAX = 256 << 10  # bytes of one log line; we shorten longer events, so one team can't fill the disk
MIN_FREE_BYTES, MIN_FREE_INODES = 2 << 30, 200_000  # what grader/run.py wants free before a run


class NotEnoughSpace(Exception):
    """The file system of the cache or the logs is too full to start a run."""


def check_free_space(paths, min_bytes=MIN_FREE_BYTES, min_inodes=MIN_FREE_INODES, statvfs=os.statvfs):
    """Raise NotEnoughSpace if a path's file system has less free space or fewer free inodes than asked. A file
    system that reports no inode count (e.g. btrfs) passes the inode check."""
    for p in paths:
        st = statvfs(p)
        free = st.f_bavail * st.f_frsize
        if free < min_bytes:
            raise NotEnoughSpace(f"{p} has {free / 2**30:.2f} GB free, and we start a run only with "
                                 f"{min_bytes / 2**30:.2f} GB or more")
        if st.f_files and st.f_favail < min_inodes:
            raise NotEnoughSpace(f"{p} has {st.f_favail:,} free inodes, and we start a run only with "
                                 f"{min_inodes:,} or more")


def load_practice(path):
    """(requests, facts, answer key) from a practice file with id, text, traveler and answer per request."""
    p = json.loads(Path(path).read_text())
    return ([{"id": r["id"], "text": r["text"]} for r in p], {r["id"]: r["traveler"] for r in p},
            {r["id"]: r["answer"] for r in p})


def pick_requests(requests, only):
    """The requests named in only, e.g. "P001,P016", in the set's order; all of them for an empty only. Raises
    SystemExit for an id that isn't in the set, so that a typo never runs nothing and passes."""
    if not only:
        return requests
    keep = [x.strip() for x in only.split(",") if x.strip()]
    ids = [r["id"] for r in requests]
    unknown = [x for x in keep if x not in ids]
    if unknown or not keep:
        raise SystemExit(f"There is no practice request {', '.join(unknown) or repr(only)}. The ids are {ids[0]} to "
                         f"{ids[-1]}, e.g. --only {ids[0]},{ids[min(15, len(ids) - 1)]}.")
    return [r for r in requests if r["id"] in keep]


def status_of(outcome, dispatch):
    """A request's status from what we saw here, never from what the child says about itself."""
    if dispatch.stop:
        return STOP_STATUS[dispatch.stop]
    if outcome["end"] != "done":
        return END_STATUS[outcome["end"]]
    if outcome.get("error"):
        return "error:" + (re.sub(r"[^\w.]", "", outcome["error"])[:40] or "Exception")
    return "ok"


def last_line(trace):
    """The last line of a traceback, e.g. "ModuleNotFoundError: No module named 'rank_bm25'", shortened."""
    lines = [x.strip() for x in str(trace or "").splitlines() if x.strip()]
    return lines[-1][:300] if lines else ""


def not_run_row(r, facts, key):
    claims = key[r["id"]].get("claims", [])
    return {"id": r["id"], "points": 0.0, "penalty": 0.0, "max": len(claims) or 1, "correct": 0, "extra": 0, "questions": 0,
            "limit": facts[r["id"]]["limit"], "over": 0, "tools": 0, "model": 0, "usd": 0.0, "status": "not-run",
            "issues": [], "seconds": 0.0}


def set_cache_writable(path, writable):
    """Make the cache folder read-only after setup(), or writable again so that we can delete it. Without the sandbox
    this is the only protection, and the team's code could undo it; in the sandbox, solve() gets a read-only mount."""
    for root, dirs, files in os.walk(path, topdown=False):
        for name in files:
            p = os.path.join(root, name)
            if not os.path.islink(p):
                os.chmod(p, 0o644 if writable else 0o444)
        os.chmod(root, 0o755 if writable else 0o555)


def real_or_none(path):
    """The real path of a file, or None for a link whose target is missing or a path we can't resolve."""
    try:
        return os.path.realpath(path) if os.path.exists(path) else None
    except (OSError, ValueError):
        return None


def real_of(entry, real_folder):
    """The real path of an os.DirEntry in a folder whose real path we know: we resolve only links, so that a deep
    folder costs one step per level."""
    try:
        return real_or_none(entry.path) if entry.is_symlink() else os.path.join(real_folder, entry.name)
    except OSError:
        return None


def team_folders(top, skip=(), follow_links=True):
    """(path below top, real folder path, [os.DirEntry, ...]) of top and every folder below it, following links to
    folders, each real folder once, without __pycache__ folders, the folders in skip and the ones we can't read. We
    walk with a list of folders to visit, without recursion, because os.walk of Python 3.10 and 3.11 recurses once per
    level, and a team's folder may be deeper than the recursion limit. With follow_links=False, we don't enter links
    to folders."""
    skip = {os.path.realpath(x) for x in skip}
    seen, todo = set(), [("", real_or_none(os.fspath(top)))]
    while todo:
        rel, real = todo.pop()
        if real is None or real in seen or real in skip:
            continue
        seen.add(real)
        try:
            with os.scandir(real) as it:
                entries = list(it)
        except (OSError, ValueError):
            continue
        yield rel, real, entries
        for e in entries:
            try:
                is_dir = e.is_dir(follow_symlinks=follow_links)
            except OSError:
                is_dir = False
            if is_dir and e.name != "__pycache__":
                todo.append((os.path.join(rel, e.name) if rel else e.name, real_of(e, real)))


def tree_files(path, skip=()):
    """[(path below path, real path), ...] of every file at or below path, sorted. We follow links to files and
    folders, and we visit each real folder once. We leave out __pycache__ folders, .pyc files and the folders in skip.
    A link whose target is missing counts with the real path None."""
    top = os.fspath(path)
    if not os.path.isdir(top):
        return [(os.path.basename(top), real_or_none(top))] if os.path.lexists(top) else []
    files = []
    for rel, real, entries in team_folders(top, skip):
        for e in entries:
            try:
                is_dir = e.is_dir()
            except OSError:
                is_dir = False
            if not is_dir and not e.name.endswith(".pyc") and e.name != "__pycache__":
                files.append((os.path.join(rel, e.name) if rel else e.name, real_of(e, real)))
    return sorted(files)


def kit_inputs():
    """The kit folder, i.e. everything of the kit that setup() and solve() can read: p2kit/, topics.json,
    packages.txt, the rules, the forms, the data, the practice set and, in the student bundle, MANIFEST.json. Leave
    out its runs/ folder (kit_skip()), where the runners write their logs."""
    return [Path(__file__).resolve().parent.parent]


def kit_skip():
    return [Path(__file__).resolve().parent.parent / "runs"]


def clear_bytecode(folder):
    """Delete every __pycache__ folder below folder, so that a child started with -B compiles the team's code from
    the source files, even after an edit that keeps the size and the mtime in seconds of a file. We don't recurse
    (team_folders, empty_folder), we don't follow links to folders, and we skip a __pycache__ that we can't delete."""
    found = [os.path.join(f, e.name) for _, f, entries in team_folders(folder, follow_links=False) for e in entries
             if e.name == "__pycache__" and e.is_dir(follow_symlinks=False)]
    for p in found:
        try:
            empty_folder(p)
            os.rmdir(p)
        except OSError:
            pass


class SetupCache:
    """chat.py's cache folder, kept from one run_set() to the next: we run setup(tools) again only when the hash of
    its inputs has changed since the last setup, after rebuild(), when the last setup didn't end with ok or none, or
    when the cache itself has changed since setup() wrote it. The inputs are the system's folder and the other folders
    and files in inputs, e.g. the rule documents, the forms, the train store and the kit (kit_inputs()), and we hash
    the path and the SHA-256 of the contents of every file in them, following links. The children of a run with a
    SetupCache start with -B, after we delete the __pycache__ folders of the system's folder, so that they run the
    code that we hashed. The practice runner and the grader never make one, so they get a fresh cache folder and
    setup(tools) in every run. skip: folders that don't count, e.g. chat.py's log folder."""

    def __init__(self, system_path, inputs=(), root=None, skip=()):
        self.system_folder = Path(system_path).resolve().parent
        self.inputs = [Path(x).resolve() for x in inputs]
        self.skip = tuple(skip)
        self.path = Path(tempfile.mkdtemp(prefix="p2-cache-", dir=root))
        self.files = None        # the inputs' files at the last setup that we keep, or None to run setup again
        self.digest = None       # their hash
        self.cache_files = None  # the cache's files after that setup
        self.kept = None         # (setup status, cache entries, cache bytes) of that setup
        self.last = None         # {"status", "at", "reason"} of the last setup that ran, for the page
        self.forced = False
        self._sums = {}          # real path -> (its os.stat signature, the SHA-256 of its contents)

    def rebuild(self):
        """Run setup(tools) again with the next request, as after a change."""
        self.forced = True

    def file_sum(self, real):
        """The SHA-256 of a file's contents, or "missing", "unreadable" or "special". We hash a file again only when
        its device, inode, size, mtime or ctime has changed; every write changes the ctime, and nothing can set it back.
        We never open anything but a regular file: a pipe, a socket or a device counts as "special"."""
        try:
            st = os.stat(real)
        except (OSError, TypeError):
            return "missing"
        if not stat.S_ISREG(st.st_mode):
            return "special"
        sig = (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
        known = self._sums.get(real)
        if known and known[0] == sig:
            return known[1]
        h = hashlib.sha256()
        try:
            with open(real, "rb") as f:
                for block in iter(lambda: f.read(1 << 20), b""):
                    h.update(block)
            digest = h.hexdigest()
        except OSError:
            digest = "unreadable"
        self._sums[real] = (sig, digest)
        return digest

    def tree_sums(self, path):
        """[(path below path, SHA-256 of the contents), ...] of every file of tree_files(path)."""
        return [(name, self.file_sum(real)) for name, real in tree_files(path, self.skip)]

    def stamp(self):
        """([(input, its files), ...] for the system's folder and every other input, and their hash)."""
        files = [(str(p), self.tree_sums(p)) for p in (self.system_folder, *self.inputs)]
        return files, hashlib.sha256(json.dumps(files).encode()).hexdigest()

    def changed(self, files):
        """The first file that differs from the last setup's, e.g. "system.py" in the system's folder, "rules/x.md"
        in a rules folder or "trains.sqlite"."""
        for (path, now), (_, before) in zip(files, self.files):
            if now != before:
                diff = sorted(set(now) ^ set(before))
                name = diff[0][0] if diff else os.path.basename(path)
                inside = path != str(self.system_folder) and os.path.isdir(path)
                return f"{os.path.basename(path)}/{name}" if inside else name
        return "an input"

    def check(self):
        """(the inputs' files and their hash now, why setup(tools) has to run again, or None to keep the cache)."""
        files, digest = self.stamp()
        if self.files is None:
            why = "first request" if self.last is None else f"the last setup ended with {self.last['status']}"
        elif self.forced:
            why = "Rebuild index"
        elif digest != self.digest:
            why = f"{self.changed(files)} changed"
        elif self.tree_sums(self.path) != self.cache_files:
            why = "the cache changed after setup"
        else:
            why = None
        return (files, digest), why

    def close(self):
        set_cache_writable(self.path, True)
        empty_folder(self.path)
        os.rmdir(self.path)


def log_writer(logf):
    def log(ev):
        line = json.dumps(ev, ensure_ascii=False, default=str)
        if len(line) > LOG_LINE_MAX:
            line = json.dumps({k: ev[k] for k in ("req", "kind", "name", "model") if k in ev} |
                              {"shortened_from_bytes": len(line)}, ensure_ascii=False, default=str)
        logf.write(line + "\n")
    return log


def teed(log, on_event):
    """log(ev), then on_event(ev)."""
    def both(ev):
        log(ev)
        on_event(ev)
    return both


def run_set(system_path, *, set_name, requests, facts, key, data, rule_dirs, form_dirs, gateway, out, timeout_s=600,
            show_checks=True, sandbox=False, extra_ro=(), packages=(), forbidden=(), cache_root=None, echo=print,
            traveler_factory=None, on_event=None, on_child=None, start_by=None, setup_cache=None):
    """Run every request, print one score line per request and the totals, and write out/log.jsonl,
    out/summary.json and out/child-output.txt (anything the team's processes wrote outside setup() and solve()).
    show_checks prints each claim's checks under its line (practice only). With sandbox=True, the team's processes run
    in bubblewrap; a SandboxError means we refuse to run. packages: (host path, name) pairs that the sandbox mounts
    read-only at /extra/packages/<name> (see isolation.installed_packages). cache_root: where we make the run's cache folder (default:
    the temporary folder). start_by: a time.time() after which we start no new request, as for the Gradescope time
    limit; the remaining requests are not-run, as after the cost cap, and the summary's stopped is TIME_BUDGET_STOP.
    Returns the summary.

    Hooks for chat.py; the practice runner and the grader leave them None, and then nothing changes:
    traveler_factory(request id, fact sheet) returns the request's traveler instead of Traveler(answers, limit);
    on_event(event) gets every log event after we write it, and a "start" event when a counted call starts;
    on_child(child) gets each ChildProcess before it starts, so that a Stop button can end it.
    setup_cache: a SetupCache, whose folder we use instead of a fresh one. We run setup(tools) only when its check()
    gives a reason, log a "setup-check" event before setup and a "setup-done" event after it, and keep the folder,
    read-only, when the run ends. The children start with -B, after we delete the system folder's __pycache__."""
    check_keys(key, [r["id"] for r in requests])  # before any team code runs
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    if setup_cache is None:
        cache, files, reason = Path(tempfile.mkdtemp(prefix="p2-cache-", dir=cache_root)), None, None
    else:
        cache = setup_cache.path
        clear_bytecode(setup_cache.system_folder)  # with -B below, the children compile the code that we hash
        files, reason = setup_cache.check()
    rerun = setup_cache is None or reason is not None
    try:
        setup_plan = solve_plan = None
        if sandbox:
            setup_plan = SandboxPlan(system_path, extra_ro, cache=cache, cache_writable=True, packages=packages)
            solve_plan = SandboxPlan(system_path, extra_ro, cache=cache, packages=packages)
            check_sandbox_paths(setup_plan.sources(), [*forbidden, out], not_above=[Path.home()])
        cache_dir = SANDBOX_CACHE if sandbox else str(cache)
        rules = RuleLibrary(rule_dirs)
        with open(out / "log.jsonl", "w") as logf, open(out / "child-output.txt", "ab") as child_out:
            log = log_writer(logf)
            if on_event is not None:
                log = teed(log, on_event)
            fresh = {} if setup_cache is None else {"bytecode": False}  # chat.py: no .pyc files, see SetupCache
            child_for = lambda plan: ChildProcess(system_path, TOPICS, output=child_out, sandbox=plan,  # noqa: E731
                                                  cache_dir=cache_dir, **fresh)
            echo(f"set={set_name} system={system_path} requests={len(requests)} cap=${gateway.max_cost:.2f} "
                 f"models={gateway.describe()} sandbox={'on' if sandbox else 'off'} log={out}")

            # setup(tools), once, in its own process, with a writable cache. We watch the cache while the system
            # loads (module code runs then) and while setup() runs, and check it once more when the process is gone.
            setup_status, load_error, outcome, t0 = "none", None, {}, time.time()
            if setup_cache is not None:  # chat.py: setup again only after a change, and the page shows it
                log({"req": "setup", "kind": "setup-check", "rerun": rerun, "reason": reason, "last": setup_cache.last})
                if rerun:
                    setup_cache.files = setup_cache.digest = setup_cache.kept = None  # until a setup ends well
                    setup_cache.forced = False
                    set_cache_writable(cache, True)
                    empty_folder(cache)
                else:
                    setup_status, cache_entries, cache_bytes = setup_cache.kept
                    if setup_status != "none" or cache_entries:
                        echo(f"setup reused, from {time.strftime('%H:%M:%S', time.localtime(setup_cache.last['at']))}: "
                             f"no change in {setup_cache.system_folder} since then")
            if rerun:
                watch = lambda: cache_usage(cache)[2]  # noqa: E731
                child = child_for(setup_plan)
                if on_child is not None:
                    on_child(child)
                try:
                    loaded = child.start(watch=watch)
                    log({"req": "setup", "kind": "loaded", **loaded})
                    if loaded.get("limit"):
                        setup_status, outcome = "error:CacheTooLarge", {"trace": loaded["trace"]}
                    elif not loaded["ok"]:
                        load_error = loaded
                    elif loaded["setup"]:
                        deadline = time.time() + timeout_s
                        # The forms only give list_rule_documents their names: SETUP_METHODS has no form tool.
                        d = Dispatcher(Tools("setup", None, rules, FormDesk(form_dirs), None, gateway, log, deadline), SETUP_METHODS)
                        outcome = child.run({"op": "setup"}, d, deadline,
                                            on_reject=lambda info: log({"req": "setup", "kind": "rejected", **info}),
                                            watch=watch)
                        setup_status = status_of(outcome, d)
                finally:
                    child.kill()
                cache_entries, cache_bytes, problem = cache_usage(cache)
                if problem or setup_status == "error:CacheTooLarge":
                    setup_status = "error:CacheTooLarge"
                    cache_entries = empty_folder(cache)  # every entry left when we killed the process
                    problem = problem or outcome.get("error") or outcome.get("trace")
                if setup_status != "none" or cache_entries:
                    log({"req": "setup", "kind": "setup", "status": setup_status, "cache_entries": cache_entries,
                         "cache_bytes": cache_bytes, "cache_problem": problem, "stdout": outcome.get("stdout", ""),
                         "trace": outcome.get("trace")})
                if setup_status != "none" or cache_entries or load_error:
                    shown = "error:" + (load_error["error"] or "ImportError") if load_error else setup_status
                    echo(f"setup {shown} cache={cache_bytes / 1e6:.1f} MB entries={cache_entries} "
                         f"seconds={time.time() - t0:.1f}")
                    if load_error:  # the file didn't load, so every request ends with the same error
                        echo(f"        {last_line(load_error.get('trace')) or load_error['error']} (your file didn't load, "
                             f"so every request ends with this error; the traceback is in {out / 'log.jsonl'})")
                    elif setup_status.startswith("error:") and outcome.get("trace"):
                        echo(f"        {last_line(outcome.get('trace'))} (the traceback is in {out / 'log.jsonl'})")
                if setup_status == "error:CacheTooLarge":
                    cache_bytes = 0
            set_cache_writable(cache, False)
            if setup_cache is not None and rerun:
                setup_cache.last = {"status": "error:" + (load_error["error"] or "ImportError") if load_error
                                    else setup_status, "at": t0, "reason": reason}
                if load_error is None and setup_status in ("ok", "none"):
                    setup_cache.files, setup_cache.digest = files
                    setup_cache.kept = (setup_status, cache_entries, cache_bytes)
                    setup_cache.cache_files = setup_cache.tree_sums(cache)
                log({"req": "setup", "kind": "setup-done", "last": setup_cache.last})

            echo(HEADER)
            rows, stopped = [], None
            for r in requests:
                if not stopped and start_by is not None and time.time() >= start_by:
                    stopped = TIME_BUDGET_STOP
                    log({"req": r["id"], "kind": "stop", "reason": stopped})
                    echo(f"STOPPED: {stopped}")
                if stopped:
                    row = not_run_row(r, facts, key)
                    rows.append(row)
                    log({"req": r["id"], "kind": "grade", "grade": None, "row": row})
                    echo(row_line(row))
                    continue
                t0 = time.time()
                f = facts[r["id"]]
                forms = FormDesk(form_dirs)
                trav = traveler_factory(r["id"], f) if traveler_factory else Traveler(f["answers"], f["limit"])
                spent0 = gateway.spent
                on_start = None if on_event is None else \
                    (lambda ev, rid=r["id"]: on_event({"req": rid, "kind": "start", "call": ev}))
                tools = Tools(r["id"], data, rules, forms, trav, gateway, log, t0 + timeout_s, on_start=on_start)
                d = Dispatcher(tools)
                if load_error:
                    outcome = {"end": "done", "error": load_error["error"] or "ImportError", "stdout": "",
                               "trace": load_error.get("trace")}
                else:
                    child = child_for(solve_plan)  # a fresh process for every request
                    if on_child is not None:
                        on_child(child)
                    try:
                        loaded = child.start()
                        if not loaded["ok"]:
                            outcome = {"end": "done", "error": loaded["error"] or "ImportError", "stdout": "",
                                       "trace": loaded.get("trace")}
                        else:
                            outcome = child.run({"op": "solve", "text": r["text"]}, d, t0 + timeout_s,
                                                on_reject=lambda info, rid=r["id"]: log({"req": rid, "kind": "rejected",
                                                                                         **info}))
                    finally:
                        child.kill()  # the whole process and sandbox, before anything goes out for the next request
                status = status_of(outcome, d)
                if d.stop:
                    log({"req": r["id"], "kind": "stop", "reason": d.stop_message})
                    if d.stop in ("CostLimit", "ModelAccessError"):
                        stopped = d.stop_message
                if status.startswith("error:"):
                    log({"req": r["id"], "kind": "exception", "end": outcome["end"], "trace": outcome.get("trace")})
                log({"req": r["id"], "kind": "stdout", "text": outcome.get("stdout", "")})
                g = grade_request(r["text"], key[r["id"]], tools.events, forms, rules)
                over = trav.over_limit
                row = {"id": r["id"], "points": points(g, over), "penalty": no_claim_carry(g, over), "max": g["max"], "correct": g["correct"],
                       "extra": g["extra_filings"], "questions": len(trav.asked), "limit": trav.limit, "over": over,
                       "tools": tools.tool_calls, "model": tools.model_calls, "usd": round(gateway.spent - spent0, 5),
                       "status": status, "issues": issue_codes(g), "seconds": round(time.time() - t0, 2)}
                if g.get("wrong_no_claims") or g.get("wrong_filings"):
                    row.update(wrong_no_claims=g.get("wrong_no_claims", 0))
                rows.append(row)
                log({"req": r["id"], "kind": "grade", "grade": g, "row": row})
                echo(row_line(row))
                if show_checks:
                    for c in g["checks"]:
                        echo("        " + json.dumps(c, ensure_ascii=False))
                logf.flush()
                if stopped:
                    echo(f"STOPPED: {stopped}")
            tot, full, tin, tout, lines = totals(rows, gateway.calls, gateway.spent)
            for line in lines:
                echo(line)
            failed = failed_calls(gateway.calls)
            if failed:
                echo(f"{len(failed)} of {len(gateway.calls)} model calls failed. The first error: "
                     f"{str(failed[0]['error'])[:300].rstrip('.')}. You find every error in {out / 'log.jsonl'}.")
            summary = {"set": set_name, "system": str(system_path), "rows": rows, "totals": tot, "fully_right": full,
                       "tokens": {"input": tin, "output": tout}, "usd": gateway.spent, "model_calls": gateway.calls,
                       "stopped": stopped, "setup": setup_status, "cache_entries": cache_entries,
                       "cache_bytes": cache_bytes, "sandbox": sandbox}
            (out / "summary.json").write_text(json.dumps(summary, indent=1, default=str))
        return summary
    finally:
        if setup_cache is None:
            empty_folder(cache)
            os.rmdir(cache)
