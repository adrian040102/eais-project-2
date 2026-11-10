"""The runner's side of the process boundary: start the team's process, in a bubblewrap sandbox if asked, and answer
its tool calls with the runner's own tools object.

The team's code runs in a child process (p2/child.py) and talks to us over a socket pair, one JSON object per line.
We keep the gateway with the key or token, the traveler's facts, the train store, the forms, the rule documents, the
counters and the event log. The child gets only tool results. Its environment has no token, key or other secret.

Each invocation, i.e. setup() once per run and solve() once per request, gets a fresh child with a fresh socket pair,
and we kill the whole child after it, before anything goes out for the next invocation. setup() can write to a cache
folder, which every later child gets read-only (tools.cache_dir). No file may grow past CACHE_LIMIT. During setup()
we check the cache every WATCH_S seconds, and end the child at once if it has more than CACHE_ENTRIES entries, is
more than CACHE_DEPTH folders deep, or uses more than CACHE_LIMIT bytes on disk, counting ENTRY_COST per entry.

With a SandboxPlan, we start the child with bubblewrap: no network, a private /tmp, and read-only access to /usr and
to a few folders that we mount at fixed paths, never at their own paths, so that the sandbox holds none of our paths:

  /team    the team's folder (the system file and its own modules)
  /starter the starter code: p2/ and topics.json
  /python  the interpreter, if it lives outside /usr (e.g. a uv or pyenv Python in the home folder): only its
           executable, its standard library and its libpython, not the rest of its installation folder
  /extra/N each --sandbox-ro folder, added to sys.path
  /extra/packages  the packages of starter/packages.txt that the runner's own Python has installed, e.g. in the team's
           venv: each top-level folder or file of each package, mounted on its own, so that the team's code can
           import those packages and no other package of the venv (see installed_packages)
  /cache   the run's cache folder: writable in setup(), read-only in solve()

We start the interpreter with -S, without its site-packages, e.g. the Debian packages in /usr/lib/python3/dist-packages,
so that the team's code imports only the standard library, the starter code, its own modules and the /extra folders.
Processes that the team's code starts, e.g. joblib's workers for scikit-learn's n_jobs, may start Python without -I
and -S. For them, PYTHONPATH holds the /extra folders, PYTHONNOUSERSITE is set, and the interpreter's site-packages
folders are empty and read-only in the sandbox, so they import from the same folders as the team's process.

If we can't build the plan for this interpreter, or bubblewrap can't start the child, we raise SandboxError, and the
runner refuses to run.
"""
import json
import os
import re
import resource
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

from .errors import STOPS

STARTER = Path(__file__).resolve().parent.parent
SANDBOX_STARTER, SANDBOX_TEAM, SANDBOX_PYTHON, SANDBOX_EXTRA, SANDBOX_CACHE = "/starter", "/team", "/python", "/extra", "/cache"
SANDBOX_PACKAGES = "/extra/packages"
BOOT = ("import sys; sys.path.insert(0, sys.argv[3]); sys.path.extend(sys.argv[4:]); from p2.child import main; "
        "main(int(sys.argv[1]), sys.argv[2])")
REQUEST_METHODS = {"ask_traveler", "list_forms", "get_form", "file_claim", "no_claim", "departures", "find_trains",
                   "train_stops", "llm", "list_rule_documents", "read_rule_document", "budget"}
SETUP_METHODS = {"list_rule_documents", "read_rule_document", "budget"}
SECRET_ENV = re.compile(r"^(P2_|OPENAI)|KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|AUTH", re.I)
START_S = 30           # seconds for the child to start and load the system
GRACE_S = 5            # seconds after a request's deadline before we kill the child
KILL_S = 5             # seconds for the sandbox to end after we kill it
WATCH_S = 0.5          # seconds between checks of the cache during setup()
CACHE_LIMIT = 500 << 20  # bytes the cache may use on disk, and the largest file a child may write
CACHE_ENTRIES = 20_000   # files plus folders in the cache
CACHE_DEPTH = 64         # folders deep in the cache
ENTRY_COST = 4096        # bytes we count for each entry, on top of its allocated blocks
TMP_SIZE = 512 << 20     # bytes in the sandbox's own /tmp (memory)
STOP_GRACE_S = 10      # seconds the child has to end a request after a stop (budget, time, cost cap)
MAX_LINE = 16 << 20    # bytes of one message


class SandboxError(Exception):
    """The sandbox can't start the team's process."""


class ProtocolError(Exception):
    """The child sent something that isn't a JSON line."""


def child_env():
    """Our environment without the token, the key and anything else that looks like a secret."""
    return {k: v for k, v in os.environ.items() if not SECRET_ENV.search(k)}


def _under(path, root):
    path, root = Path(path), Path(root)
    return path == root or root in path.parents


def python_mounts(executable=None, base_prefix=None, base_exec_prefix=None, version=None):
    """([(host path, sandbox path), ...], the interpreter's path in the sandbox) for the Python that runs us.

    From a venv we use its base interpreter, without the venv's packages. A Python under /usr comes with /usr. Any
    other Python goes to /python with only its executable, its standard library and its libpython, so that the
    sandbox holds nothing else of its installation folder, e.g. the rest of ~/.local. Raises SandboxError if we can't
    find the base installation."""
    version = version or sys.version_info
    prefix = Path(os.path.realpath(base_prefix or sys.base_prefix))
    exec_prefix = Path(os.path.realpath(base_exec_prefix or sys.base_exec_prefix))
    name = f"python{version[0]}.{version[1]}"
    candidates = [executable or sys.executable, prefix / "bin" / name, prefix / "bin" / f"python{version[0]}"]
    exe = next((Path(os.path.realpath(c)) for c in candidates
                if _under(os.path.realpath(c), prefix) and os.access(os.path.realpath(c), os.X_OK)), None)
    if exe is None:
        raise SandboxError(f"can't find the base installation of this Python ({executable or sys.executable}); "
                           "run the grader with the Python of an installation, e.g. /usr/bin/python3")
    if _under(prefix, "/usr") and _under(exec_prefix, "/usr"):
        return [], str(exe)
    stdlib = prefix / "lib" / name
    if exec_prefix != prefix or not (stdlib / "os.py").is_file():
        raise SandboxError(f"this Python ({exe}) has no standard library at {stdlib}; "
                           "run the grader with another Python, e.g. /usr/bin/python3")
    mounts = [(exe, f"{SANDBOX_PYTHON}/bin/{exe.name}"), (stdlib, f"{SANDBOX_PYTHON}/lib/{name}")]
    mounts += [(p, f"{SANDBOX_PYTHON}/lib/{p.name}") for p in sorted((prefix / "lib").glob("libpython*.so*"))]
    return mounts, f"{SANDBOX_PYTHON}/bin/{exe.name}"


SITE_FOLDERS = "import json, site; print(json.dumps(site.getsitepackages()))"


def site_folders(python, mounts):
    """The sandbox paths of the site-packages folders of the interpreter python (its path on the host), e.g.
    /usr/lib/python3/dist-packages or /python/lib/python3.10/site-packages, that exist in the sandbox: under /usr, or
    under a folder of mounts. We ask the interpreter itself, because the folders depend on its version and build, and
    raise SandboxError if it doesn't answer."""
    try:
        r = subprocess.run([str(python), "-I", "-S", "-c", SITE_FOLDERS], capture_output=True, text=True, timeout=30,
                           env={"PATH": "/usr/bin:/bin"})
        found = json.loads(r.stdout) if r.returncode == 0 else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        found = None
    if not isinstance(found, list) or not all(isinstance(p, str) for p in found):
        raise SandboxError(f"can't ask {python} for its site-packages folders")
    out = []
    for p in found:
        p = Path(os.path.realpath(p))
        if not p.is_dir():
            continue
        if _under(p, "/usr"):
            out.append(str(p))
            continue
        for host, dest in mounts:
            if Path(host).is_dir() and _under(p, host):
                out.append(str(Path(dest) / p.relative_to(host)))
    return sorted(set(out))


def installed_packages(packages_txt=STARTER / "packages.txt", path=None):
    """The packages of the package list that this Python has installed, e.g. in the team's venv, for a read-only
    mount in the sandbox: (mounts, found, missing). mounts is [(host path, name), ...], one for each top-level folder
    or file of each package (e.g. numpy/, numpy.libs/ and numpy-2.2.6.dist-info/), found is [(name, installed
    version, listed version), ...], and missing the listed names that we can't mount. path: the folders to search
    (default sys.path). We read the list with the upload check's parser, and we mount nothing else of the venv, so
    a package that isn't on the list fails to import in the sandbox, as in grading.

    Each mount must be a real top-level entry of the package's own site-packages folder: not a symbolic link (e.g. a
    link to the whole site-packages folder would bring every package of the venv into the sandbox), and named after
    the package, one of its import names or its .dist-info folder, so that a package's file list can't name the
    folder of another package. We leave out every other entry."""
    from importlib import metadata
    from .upload import read_package_list
    mounts, found, missing, seen = [], [], [], set()
    for pkg in read_package_list(packages_txt):
        dists = list(metadata.distributions(name=pkg["name"], path=path if path is not None else sys.path))
        if not dists:
            missing.append(pkg["name"])
            continue
        dist, before = dists[0], len(mounts)
        site = Path(os.path.realpath(dist.locate_file("")))
        own = {pkg["name"].replace("-", "_").lower(), *(n.lower() for n in pkg["imports"])}
        tops = {Path(str(f)).parts[0] for f in dist.files or [] if Path(str(f)).parts}  # with the .dist-info folder
        for top in sorted(tops):
            if not top or top in ("..", "__pycache__", ".") or top.endswith(".pth") or top in seen:
                continue
            if not (top.lower().endswith(".dist-info") or top.split(".")[0].lower() in own):
                continue  # another package's folder, or a stray file
            entry = Path(dist.locate_file(top))
            if entry.is_symlink() or not entry.exists():
                continue
            host = Path(os.path.realpath(entry))
            if host.parent != site or host.name != top:  # e.g. a folder above it that is a link
                continue
            mounts.append((host, top))
            seen.add(top)
        if len(mounts) > before:
            found.append((pkg["name"], dist.version, pkg["version"]))
        else:  # e.g. a Debian package without a list of its files
            missing.append(pkg["name"])
    return mounts, found, missing


def team_packages(echo=print, packages_txt=STARTER / "packages.txt"):
    """installed_packages() for the practice runner, chat.py and check_upload.py, with a line for the team about
    what the sandbox can import."""
    mounts, found, missing = installed_packages(packages_txt)
    where = os.path.commonpath([str(Path(h).parent) for h, _ in mounts]) if mounts else None
    total = len(found) + len(missing)
    echo(f"In the sandbox, you can import {len(found)} of the {total} packages in packages.txt"
         + (f", from {where}." if where else "."))
    if missing:
        echo(f"    Not installed for this Python: {', '.join(missing)}. To install them, run "
             "python3 -m pip install --no-deps -r packages.txt in the starter code folder, with your venv active.")
    other = [f"{n} {v} (we grade with {w})" for n, v, w in found if v != w]
    if other:
        echo(f"    Other versions than in grading: {', '.join(other)}.")
    return mounts


class SandboxPlan:
    """What the bubblewrap sandbox mounts for one system, and the command that starts the child in it."""

    def __init__(self, system_path, extra_ro=(), cache=None, cache_writable=False, packages=(), **python):
        """cache: the host folder that we mount at /cache, writable for setup() and read-only for solve().
        packages: [(host path, name), ...] from installed_packages, mounted at /extra/packages/<name>."""
        bwrap = shutil.which("bwrap")
        if not bwrap:
            raise SandboxError("bubblewrap (bwrap) is not installed, so we can't sandbox the system")
        self.bwrap = bwrap
        system_path = Path(system_path).resolve()
        pymounts, self.python = python_mounts(**python)
        self.system = f"{SANDBOX_TEAM}/{system_path.name}"
        self.extra = [f"{SANDBOX_EXTRA}/{i}" for i in range(len(extra_ro))]
        self.mounts = [(system_path.parent, SANDBOX_TEAM), (STARTER / "p2", f"{SANDBOX_STARTER}/p2"),
                       (STARTER / "topics.json", f"{SANDBOX_STARTER}/topics.json"), *pymounts,
                       *[(Path(p).resolve(), d) for p, d in zip(extra_ro, self.extra)],
                       *[(Path(p), f"{SANDBOX_PACKAGES}/{name}") for p, name in packages]]
        if packages:
            self.extra.append(SANDBOX_PACKAGES)
        self.prefix = ["--unshare-all", "--die-with-parent", "--new-session", "--clearenv",
                       "--setenv", "PATH", "/usr/bin:/bin", "--setenv", "HOME", "/tmp", "--setenv", "LANG", "C.UTF-8",
                       "--setenv", "PYTHONDONTWRITEBYTECODE", "1", "--setenv", "PYTHONNOUSERSITE", "1",
                       "--ro-bind", "/usr", "/usr"]
        if self.extra:
            self.prefix += ["--setenv", "PYTHONPATH", ":".join(self.extra)]
        for name in ("bin", "lib", "lib64", "sbin"):
            p = Path("/") / name
            if p.is_symlink():
                self.prefix += ["--symlink", os.readlink(p), str(p)]
            elif p.is_dir():
                self.prefix += ["--ro-bind", str(p), str(p)]
        self.prefix += ["--proc", "/proc", "--dev", "/dev", "--size", str(TMP_SIZE), "--tmpfs", "/tmp"]
        for host, dest in self.mounts:
            self.prefix += ["--ro-bind", str(host), dest]
        host_python = pymounts[0][0] if self.python.startswith(SANDBOX_PYTHON + "/") else self.python
        self.site_folders = site_folders(host_python, pymounts)
        for d in self.site_folders:  # empty and read-only, for a Python started without -S
            self.prefix += ["--size", "4096", "--tmpfs", d, "--remount-ro", d]
        self.cache = Path(cache).resolve() if cache is not None else None
        if self.cache is not None:
            self.prefix += ["--bind" if cache_writable else "--ro-bind", str(self.cache), SANDBOX_CACHE]
        # Last, the sandbox's own root folder read-only: bubblewrap makes it as a writable tmpfs, with the folders
        # that hold our mounts, e.g. /starter, /extra/packages and /python. /tmp, the cache, /proc and /dev are mounts of
        # their own and keep their rights.
        self.prefix += ["--remount-ro", "/", "--chdir", SANDBOX_TEAM, "--"]

    def sources(self):
        return [host for host, _ in self.mounts] + ([self.cache] if self.cache is not None else [])

    def command(self, fd, info_fd, flags=()):
        return [self.bwrap, "--info-fd", str(info_fd), *self.prefix, self.python, "-I", "-S", *flags, "-c", BOOT,
                str(fd), self.system, SANDBOX_STARTER, *self.extra]


def check_sandbox_paths(sources, hidden=(), not_above=()):
    """Refuse to mount a path that overlaps a path the sandbox must hide (e.g. private/, the train store, the log
    folder), or that contains a path of not_above (e.g. the home folder)."""
    for src in sources:
        src = Path(src).resolve()
        for p in hidden:
            p = Path(p).resolve()
            if p == src or src in p.parents or p in src.parents:
                raise SandboxError(f"the sandbox would mount {src}, which overlaps {p}, a path it must hide; "
                                   "put the system in a folder of its own")
        for p in not_above:
            p = Path(p).resolve()
            if p == src or src in p.parents:
                raise SandboxError(f"the sandbox would mount {src}, which contains {p}; "
                                   "put the system in a folder of its own")


class Dispatcher:
    """Answers the calls of one request, or of setup(), with the runner's tools object.

    After a stop (BudgetExceeded, TimeLimit, CostLimit, ModelAccessError), every further call of the request gets the
    same stop. `stop` holds the name of the first one."""

    def __init__(self, tools, methods=REQUEST_METHODS):
        self.tools, self.methods = tools, methods
        self.stop = self.stop_message = None

    def __call__(self, m):
        if self.stop:
            return {"op": "raise", "kind": self.stop, "message": self.stop_message}
        name = m.get("name")
        if not isinstance(name, str) or name not in REQUEST_METHODS:
            return {"op": "raise", "kind": "PermissionError", "message": f"there is no tool {name!r}"}
        if name not in self.methods:
            return {"op": "result", "value": self.tools.refuse(
                name, f"{name} isn't available in setup(); there you can read the rule documents")}
        if "bad" in m:  # counted like the call it was meant to be, so it can hit a stop too
            run = lambda: self.tools.bad_arguments(name, str(m["bad"])[:300])  # noqa: E731
        else:
            args, kwargs = m.get("args", []), m.get("kwargs", {})
            if not isinstance(args, list) or not isinstance(kwargs, dict):
                return {"op": "raise", "kind": "TypeError", "message": "args must be a list and kwargs a dict"}
            run = lambda: getattr(self.tools, name)(*args, **kwargs)  # noqa: E731
        try:
            return {"op": "result", "value": run()}
        except tuple(STOPS.values()) as e:
            self.stop, self.stop_message = type(e).__name__, str(e)
            return {"op": "raise", "kind": self.stop, "message": self.stop_message}
        except TypeError as e:  # wrong arguments for the method, as in a direct call
            return {"op": "raise", "kind": "TypeError", "message": str(e)}
        except Exception as e:  # a bug of ours: the system sees an error, and the run goes on
            return {"op": "raise", "kind": "RuntimeError", "message": f"the runner failed on this call: {type(e).__name__}"}


class ChildProcess:
    """One team process for one invocation: setup() or one solve(). start() loads the system and returns the "loaded"
    message; run() sends the invocation and answers its calls until "done", the deadline, or the end of the process;
    kill() ends the whole process with everything in it, and with its sandbox, and waits until it is gone. The runner
    calls kill() after every invocation, before it sends anything for the next one, so that nothing of one request's
    process, e.g. a thread or a forged "done", reaches the next request."""

    def __init__(self, system_path, topics, *, output, sandbox=None, cache_dir=None, bytecode=True):
        """sandbox: a SandboxPlan, or None to run the child without bubblewrap. cache_dir: tools.cache_dir in the
        child, i.e. /cache in the sandbox, or the cache folder's own path without it. bytecode=False (chat.py only)
        starts Python with -B, so that the child writes no .pyc files."""
        self.system_path = Path(system_path).resolve()
        self.flags = () if bytecode else ("-B",)
        self.team_dir = self.system_path.parent
        self.topics, self.output, self.sandbox, self.cache_dir = topics, output, sandbox, cache_dir
        self.proc = self.sock = self.sandbox_pid = None
        self._buf, self._seen = bytearray(), 0

    # ---- lines over the socket ---------------------------------------------------------------------------
    def _send(self, msg):
        self.sock.settimeout(60)
        self.sock.sendall(json.dumps(msg, ensure_ascii=False).encode() + b"\n")

    def _recv(self, deadline):
        while self._buf.find(b"\n", self._seen) < 0:  # only the new bytes, so a long message costs one pass
            remaining = deadline - time.time()
            if remaining <= 0:
                raise TimeoutError
            self.sock.settimeout(remaining)
            try:
                chunk = self.sock.recv(1 << 16)
            except socket.timeout:
                raise TimeoutError from None
            except OSError:
                raise EOFError from None
            if not chunk:
                raise EOFError
            self._seen = len(self._buf)
            self._buf += chunk
            if len(self._buf) > MAX_LINE:
                raise ProtocolError("message too long")
        line, rest = bytes(self._buf).split(b"\n", 1)
        self._buf, self._seen = bytearray(rest), 0
        try:
            msg = json.loads(line)
        except Exception as e:  # any decoder failure, e.g. ValueError, UnicodeDecodeError, RecursionError
            raise ProtocolError(f"not JSON ({type(e).__name__})") from None
        if not isinstance(msg, dict):
            raise ProtocolError("not a JSON object")
        return msg

    # ---- the process -------------------------------------------------------------------------------------
    def start(self, watch=None):
        """Start the child and load the system. Returns {"ok": ..., "setup": ..., "error": ..., "trace": ...}.
        watch(), as in run(), also runs while the system loads, because module code runs then. Raises SandboxError
        if the child doesn't start at all."""
        ours, theirs = socket.socketpair()
        fds = [theirs.fileno()]
        info_r = info_w = None
        if self.sandbox:
            info_r, info_w = os.pipe()  # bubblewrap writes the sandbox's first process id here
            fds.append(info_w)
            if self.flags:  # chat.py only; Gradescope's plan (grader/gradescope/userbox.py) takes no flags
                cmd = self.sandbox.command(theirs.fileno(), info_w, self.flags)
            else:
                cmd = self.sandbox.command(theirs.fileno(), info_w)
        else:
            cmd = [sys.executable, "-I", *self.flags, "-c", BOOT, str(theirs.fileno()), str(self.system_path), str(STARTER)]
        env = {"PATH": "/usr/bin:/bin"} if self.sandbox else child_env()
        try:
            self.proc = subprocess.Popen(cmd, pass_fds=fds, env=env, cwd=self.team_dir, stdin=subprocess.DEVNULL,
                                         stdout=self.output, stderr=self.output, start_new_session=True,
                                         preexec_fn=_limit_file_size)
        except OSError as e:
            raise SandboxError(f"can't start the system's process: {e}") from None
        finally:
            theirs.close()
            if info_w is not None:
                os.close(info_w)
        self.sock, self._buf, self._seen = ours, bytearray(), 0
        deadline = time.time() + START_S
        try:
            self._send({"op": "init", "topics": self.topics, "cache": self.cache_dir})
            hello = self._recv(deadline)
            if hello.get("op") != "hello":
                raise ProtocolError("no hello")
            if info_r is not None:
                self.sandbox_pid = int(json.loads(os.read(info_r, 1 << 16))["child-pid"])
        except (TimeoutError, EOFError, ProtocolError, OSError, ValueError, KeyError, TypeError) as e:
            self.kill()
            raise SandboxError(f"the system's process didn't start ({type(e).__name__}); "
                               f"see {getattr(self.output, 'name', 'its output')}") from None
        finally:
            if info_r is not None:
                os.close(info_r)
        next_watch = time.time() + WATCH_S
        while True:
            if watch is not None and time.time() >= next_watch:
                reason = watch()
                if reason:
                    self.kill()
                    return {"ok": False, "limit": True, "error": "CacheTooLarge", "trace": reason}
                next_watch = time.time() + WATCH_S
            try:
                loaded = self._recv(deadline if watch is None else min(deadline, next_watch))
                break
            except TimeoutError as e:
                if watch is not None and time.time() < deadline:
                    continue
                self.kill()
                return {"ok": False, "error": type(e).__name__, "trace": "the system didn't load in time"}
            except (EOFError, ProtocolError) as e:
                self.kill()
                return {"ok": False, "error": type(e).__name__, "trace": "the system didn't load"}
        return {"ok": bool(loaded.get("ok")), "setup": bool(loaded.get("setup")),
                "error": str(loaded.get("error") or "")[:60] or None, "trace": str(loaded.get("trace") or "")[-20000:]}

    def run(self, msg, dispatch, deadline, on_reject=None, watch=None):
        """Send "setup" or "solve" with a new random invocation id, and answer the child's calls with
        dispatch(message) until the child is done. We neither run nor answer a message with another id;
        on_reject(info) can log it. watch() is called about every second; if it returns a reason, we end the process.
        Returns {"end": "done" | "timeout" | "exited" | "protocol" | "limit", "error", "trace", "stdout"}."""
        invocation = secrets.token_hex(16)
        try:
            self._send({**msg, "id": invocation})
        except OSError:
            self.kill()
            return {"end": "exited"}
        hard = deadline + GRACE_S
        next_watch = time.time() + WATCH_S
        while True:
            if watch is not None and time.time() >= next_watch:  # on the clock, also while calls keep coming
                reason = watch()
                if reason:
                    self.kill()
                    return {"end": "limit", "error": reason}
                next_watch = time.time() + WATCH_S
            try:
                m = self._recv(hard if watch is None else min(hard, next_watch))
            except TimeoutError:
                if watch is not None and time.time() < hard:
                    continue
                self.kill()
                return {"end": "timeout"}
            except EOFError:
                self.kill()
                return {"end": "exited"}
            except ProtocolError:
                self.kill()
                return {"end": "protocol"}
            op = m.get("op")
            if m.get("id") != invocation:
                if on_reject:
                    on_reject({"op": str(op)[:20], "name": str(m.get("name"))[:40],
                               "reason": "not the id of the active setup() or solve() call"})
                continue
            if op == "done":
                return {"end": "done", "error": (str(m["error"])[:60] if m.get("error") else None),
                        "trace": str(m.get("trace") or "")[-20000:], "stdout": str(m.get("stdout") or "")[-20000:]}
            if op == "call":
                paused = getattr(getattr(dispatch, "tools", None), "paused_s", 0.0)
                reply = dispatch(m)
                # chat.py: the time a person took to answer as the traveler moves the deadline on (never in grading)
                moved = getattr(getattr(dispatch, "tools", None), "paused_s", 0.0) - paused
                if moved:
                    hard += moved
                if getattr(dispatch, "stop", None):
                    hard = min(hard, time.time() + STOP_GRACE_S)
            else:
                reply = {"op": "raise", "kind": "PermissionError", "message": "we accept only tool calls"}
            try:
                self._send({**reply, "id": invocation})
            except OSError:
                self.kill()
                return {"end": "exited"}

    def interrupt(self):
        """Kill the child's processes from another thread, e.g. for the Stop button of chat.py, without waiting or
        closing anything: the thread in run() then sees the end of the process and cleans up with kill()."""
        if self.sandbox_pid:
            _signal(self.sandbox_pid, signal.SIGKILL)
        proc = self.proc
        if proc is not None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                pass

    def kill(self):
        """End the child's whole process group, and in the sandbox its whole pid namespace, and wait until they are
        gone. Safe to call more than once."""
        if self.sandbox_pid:
            _signal(self.sandbox_pid, signal.SIGKILL)  # the sandbox's first process: its pid namespace ends with it
        if self.proc is not None:
            try:
                os.killpg(self.proc.pid, signal.SIGKILL)  # also after the leader ended, for what it left behind
            except OSError:
                pass
            self.proc.wait()
        if self.sandbox_pid:
            deadline = time.time() + KILL_S
            while _running(self.sandbox_pid):
                if time.time() > deadline:
                    raise SandboxError(f"the sandbox (process {self.sandbox_pid}) didn't end")
                time.sleep(0.005)
        if self.sock is not None:
            self.sock.close()
        self.proc = self.sock = self.sandbox_pid = None


def _limit_file_size():
    """In the child, before it starts: no file larger than the cache limit."""
    resource.setrlimit(resource.RLIMIT_FSIZE, (CACHE_LIMIT, CACHE_LIMIT))


def _signal(pid, sig):
    try:
        os.kill(pid, sig)
    except OSError:
        pass


def _running(pid):
    """False once the process is gone or a zombie: a zombie runs nothing, and the kernel ends every process of a pid
    namespace before its first process becomes one."""
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"
    except (OSError, IndexError):
        return False


def _walk(path, with_size):
    entries = used = 0
    stack = [(os.fspath(path), 0)]
    while stack:
        folder, depth = stack.pop()
        try:
            with os.scandir(folder) as it:
                for e in it:
                    entries += 1
                    if entries > CACHE_ENTRIES:
                        return entries, used, f"more than {CACHE_ENTRIES:,} entries"
                    try:
                        if with_size:
                            used += e.stat(follow_symlinks=False).st_blocks * 512 + ENTRY_COST
                            if used > CACHE_LIMIT:
                                return entries, used, f"more than {CACHE_LIMIT >> 20} MB"
                        if e.is_dir(follow_symlinks=False):
                            if depth + 1 > CACHE_DEPTH:
                                return entries, used, f"more than {CACHE_DEPTH} folders deep"
                            stack.append((e.path, depth + 1))
                    except FileNotFoundError:  # removed while we count, during setup()
                        pass
        except FileNotFoundError:
            continue
        except OSError as e:
            return entries, used, f"can't read {folder}: {e.strerror}"
    return entries, used, None


def cache_usage(path):
    """(entries, bytes, problem) of a cache folder: files plus folders below it, and the bytes they use on disk
    (allocated blocks plus ENTRY_COST per entry). problem is None, or the first limit it passes; we stop counting
    there. We walk without recursion and never follow links, first counting entries and depth from the folder
    listings alone, which is fast, and then the bytes. A folder we can't read counts as a problem too."""
    entries, _, problem = _walk(path, with_size=False)
    if problem:
        return entries, entries * ENTRY_COST, problem
    return _walk(path, with_size=True)


def empty_folder(path):
    """Delete everything below path, however deep, and return how many entries there were. We never descend: we move
    the children of each folder at the top up to the top, then remove the folder, so every path stays two names
    long, with no recursion and no open folder chain."""
    root = os.fspath(path)
    removed = moved = 0
    os.chmod(root, 0o700)
    while True:
        with os.scandir(root) as it:
            top = list(it)
        if not top:
            return removed
        for e in top:
            if e.is_dir(follow_symlinks=False):
                os.chmod(e.path, 0o700)
                with os.scandir(e.path) as it:
                    children = list(it)
                for c in children:
                    while True:
                        moved += 1
                        target = os.path.join(root, f".p2-moved-{moved}")
                        if not os.path.lexists(target):
                            break
                    os.rename(c.path, target)
                os.rmdir(e.path)
            else:
                os.unlink(e.path)
            removed += 1
