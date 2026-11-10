"""A team's upload: copy it safely, find the system file, and check its structure before any team code runs.

The batch runner (grader/batch.py), the Gradescope quick score (grader/gradescope/) and the students' own upload check
(starter/check_upload.py) all use this module, so a team gets the same structure messages in all three places. The grader
imports it as `upload` (grader/upload.py makes that name point to this module). We never import or run the team's code here: we read the Python
files as text and parse them with `ast`.

copy_upload(src, dest) copies the upload without following symbolic links. We leave out links, special files, caches
and version control folders, and we unpack an uploaded zip when the upload has no Python file of its own. Then
check_structure(folder) finds the system file and checks it:

- The system file is `system.py`, at the top of the upload or in its one top folder. Without a `system.py`, we take the
  one Python file that defines `solve`, with a note.
- It parses, and it defines `solve(request_text, tools)` at the top level, and `setup(tools)` if it has one.
- Every import is from the standard library, the team's own modules, p2, or the package list (starter/packages.txt).
  We report an import inside `try ... except ImportError` as a note, because the team's code handles it.
- No relative import (`from . import x`) in a module that isn't in a package, because we load the system file on its
  own, as a single file.

The messages go to students, so they name only the team's own files, line numbers and module names.
"""
import ast
import os
import re
import shutil
import stat
import struct
import sys
import zipfile
from pathlib import Path

STARTER = Path(__file__).resolve().parent.parent
PACKAGES_TXT = STARTER / "packages.txt"
ENTRY = "system.py"
SKIP_NAMES = {"__pycache__", ".git", ".hg", ".svn", ".venv", "venv", ".DS_Store", "__MACOSX", ".ipynb_checkpoints"}
MAX_BYTES = 200 << 20      # an upload may unpack to at most this many bytes
MAX_ENTRIES = 10_000       # and at most this many files and folders
MAX_DEPTH = 64             # and folders at most this many deep
MAX_MEMBERS = 3 * MAX_ENTRIES  # members of an uploaded zip, with the ones we skip, e.g. __pycache__/ and the
#                               __MACOSX/ copies of a zip from the macOS Finder
MAX_DIRECTORY = 4 << 20    # bytes of a zip's list of contents, read before we look at any member
ALWAYS = {"p2", "__future__"}  # p2 is the starter code's own package, on the path in every run
LIST_LINE = re.compile(r"^([A-Za-z0-9_.\-]+)==([^\s#]+)\s*#\s*import:\s*([A-Za-z0-9_, ]+?)\s*(\((.*)\))?\s*$")


class UploadError(Exception):
    """The upload can't be graded at all, e.g. it is too large. The message is for the team."""


# ---- the package list --------------------------------------------------------------------------------------------

def read_package_list(path=PACKAGES_TXT):
    """[{"name", "version", "imports": [...], "needed_by": text or None}, ...] from starter/packages.txt."""
    out = []
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = LIST_LINE.match(line)
        if not m:
            raise ValueError(f"{path}: can't read the line {line!r}; write 'name==version  # import: module'")
        out.append({"name": m.group(1), "version": m.group(2),
                    "imports": [x.strip() for x in m.group(3).split(",") if x.strip()],
                    "needed_by": m.group(5)})
    return out


def package_imports(path=PACKAGES_TXT):
    return {name for p in read_package_list(path) for name in p["imports"]}


def package_words(path=PACKAGES_TXT):
    """The main packages for a message, e.g. "numpy, scikit-learn (import sklearn) and pydantic"."""
    words = []
    for p in read_package_list(path):
        if p["needed_by"] and "needed by" in p["needed_by"]:
            continue
        name = p["name"]
        words.append(name if p["imports"] == [name] else f"{name} (import {', '.join(p['imports'])})")
    return _and(words)


def _and(words):
    words = list(words)
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]


# ---- copying the upload ------------------------------------------------------------------------------------------

def copy_upload(src, dest, skip=()):
    """Copy the upload in src to the new folder dest: regular files and folders only, never following links, without
    caches, version control folders and the names in skip. If the copy has no Python file and exactly one zip file,
    we unpack the zip in its place. Returns notes for the team. Raises UploadError if the upload is too large: more
    than MAX_BYTES, more than MAX_ENTRIES files and folders, or folders more than MAX_DEPTH deep. We count before we
    copy each entry, so a large upload stops at the limit and never fills the disk."""
    src, dest = Path(src), Path(dest)
    dest.mkdir(parents=True)
    notes, budget = [], new_budget()
    skipped_links = []
    stack = [(src, dest, "", 0)]
    while stack:
        s_dir, d_dir, rel, depth = stack.pop()
        with os.scandir(s_dir) as it:
            entries = sorted(it, key=lambda e: e.name)
        for e in entries:
            if e.name in SKIP_NAMES or (not rel and e.name in skip):
                continue
            r = f"{rel}{e.name}"
            st = os.lstat(e.path)
            if stat.S_ISLNK(st.st_mode):
                skipped_links.append(r)
            elif stat.S_ISDIR(st.st_mode):
                _check_depth(depth + 1)
                _count(budget, 0)
                (d_dir / e.name).mkdir()
                stack.append((Path(e.path), d_dir / e.name, r + "/", depth + 1))
            elif stat.S_ISREG(st.st_mode):
                _count(budget, st.st_size)
                _copy_file(e.path, d_dir / e.name)
    if skipped_links:
        notes.append(_links_note(skipped_links))
    pys = [p for p in dest.rglob("*.py")]
    zips = [p for p in dest.iterdir() if p.suffix.lower() == ".zip" and p.is_file()]
    if not pys and len(zips) == 1:
        z = zips[0]
        links = _unpack(z, dest, budget)
        z.unlink()
        notes.append(f"We unpacked {z.name}, because your upload had no Python file outside it.")
        if links:
            notes.append(_links_note(links))
    _readable(dest)
    return notes


def _links_note(names):
    return (f"We left out {_and(names[:5])}{' and others' if len(names) > 5 else ''}, because we copy only files "
            "and folders, not symbolic links.")


def unpack_zip(zip_path, dest):
    """Unpack the zip file at zip_path into the new folder dest, as Gradescope unpacks an uploaded zip, with the same
    limits as copy_upload, checked from the zip's list of contents before we write anything (starter/check_upload.py).
    We never copy the zip file itself. Raises UploadError."""
    zip_path, dest = Path(zip_path), Path(dest)
    if not zipfile.is_zipfile(zip_path):
        raise UploadError(f"{zip_path.name} isn't a zip file. Make the zip file again from your team folder.")
    dest.mkdir(parents=True)
    links = _unpack(zip_path, dest, new_budget())
    _readable(dest)
    return [_links_note(links)] if links else []


def new_budget():
    return {"bytes": 0, "files": 0}


def _readable(dest):
    for root, dirs, files in os.walk(dest):  # readable by all, whatever the umask: the team's processes read it
        os.chmod(root, 0o755)
        for name in files:
            os.chmod(os.path.join(root, name), 0o644)


def _too_large():
    return UploadError(f"Your upload is larger than {MAX_BYTES >> 20} MB, so we didn't grade it. Leave out the "
                       "files that you don't need in your system, e.g., the train data.")


def _too_many():
    return UploadError(f"Your upload has more than {MAX_ENTRIES:,} files and folders, so we didn't grade it. Leave "
                       "out the files that you don't need in your system.")


def _check_depth(depth):
    if depth > MAX_DEPTH:
        raise UploadError(f"Your upload has folders more than {MAX_DEPTH} deep, so we didn't grade it. Make the zip "
                          "file again from your team folder.")


def _count(budget, size):
    """Count one entry (a file, or a folder with size 0) before we write it, and raise at a limit."""
    budget["bytes"] += size
    budget["files"] += 1
    if budget["bytes"] > MAX_BYTES:
        raise _too_large()
    if budget["files"] > MAX_ENTRIES:
        raise _too_many()


def _copy_file(src, dest):
    fd = os.open(src, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as fin, open(dest, "xb") as fout:
        shutil.copyfileobj(fin, fout)
    os.chmod(dest, 0o644)


def _zip_plan(z, root):
    """The entries of an open zip that we would write, checked before we write any of them: [(info, target)], every
    folder, also the folders that only appear in the path of a file, and the names of the links that we leave out.
    Raises UploadError for a path outside the zip's folder, folders deeper than MAX_DEPTH, more than MAX_ENTRIES
    entries, or more than MAX_BYTES in all, and for a path that is a file and also a folder (e.g. `a` and `a/b`).
    We check the limits on each member, as we go, so the folders we collect never pass MAX_ENTRIES + MAX_DEPTH,
    whatever the zip holds."""
    plan, folders, files, size, links = [], set(), set(), 0, []
    for info in z.infolist():
        parts = Path(info.filename).parts
        if not parts or any(p in SKIP_NAMES for p in parts):
            continue
        target = (root / info.filename).resolve()
        if root not in target.parents:
            raise UploadError(f"Your zip file has a file named {info.filename!r}, outside the zip's own folder, "
                              "so we didn't grade it. Make the zip again from your project folder.")
        if stat.S_ISLNK(info.external_attr >> 16):
            links.append(info.filename)
        else:
            rel = target.relative_to(root).parts
            _check_depth(len(rel) - (0 if info.is_dir() else 1))
            last = len(rel) if info.is_dir() else len(rel) - 1
            new = [rel[:i] for i in range(1, last + 1)]
            clash = next((f for f in new if f in files or (root.joinpath(*f)).is_file()), None)
            if clash is None and not info.is_dir() and (rel in folders or target.is_dir()):
                clash = rel
            if clash is not None:
                raise UploadError(f"Your zip file has a file and a folder with the same name, {'/'.join(clash)!r}, "
                                  "so we didn't grade it. Make the zip file again from your team folder.")
            folders.update(new)
            if not info.is_dir():
                files.add(rel)
                size += info.file_size
                plan.append((info, target))
        if len(plan) + len(folders) + len(links) > MAX_ENTRIES:
            raise _too_many()
        if size > MAX_BYTES:
            raise _too_large()
    return plan, folders, links


def check_zip_file(zip_path):
    """Refuse a zip file before zipfile reads its list of contents into memory. Raises UploadError.

    We find the end records with zipfile's own _EndRecData, the function that ZipFile uses: the ordinary end record,
    and the ZIP64 end record whenever its locator sits 20 bytes before the ordinary one, read where zipfile reads it.
    So we check the values that ZipFile will use, and also the ordinary record's own values:
    - each set of values has at most MAX_MEMBERS members and a list of contents of at most MAX_DIRECTORY bytes (a
      ZIP64 marker, 0xFFFF or 0xFFFFFFFF, in the ordinary record stands for the ZIP64 value);
    - in each set, the list of contents ends where the end record (or the ZIP64 end record) begins, so the offset
      and the size agree with the file;
    - the member headers in the list, walked as ZipFile walks them, are whole and as many as the end record says.
    The zip file itself may have at most MAX_BYTES + MAX_DIRECTORY bytes."""
    name = Path(zip_path).name
    unreadable = UploadError(f"We can't read the zip file {name}. Make the zip file again from your team folder.")
    size = os.path.getsize(zip_path)
    if size > MAX_BYTES + MAX_DIRECTORY:
        raise _too_large()
    with open(zip_path, "rb") as f:
        try:
            end = zipfile._EndRecData(f)
        except (zipfile.BadZipFile, OSError, struct.error, ValueError):
            raise unreadable from None
        if not end:
            raise UploadError(f"{name} isn't a zip file. Make the zip file again from your team folder.")
        location = end[zipfile._ECD_LOCATION]  # where the end record, or the ZIP64 end record, begins
        sets = [(end[zipfile._ECD_ENTRIES_TOTAL], end[zipfile._ECD_SIZE], end[zipfile._ECD_OFFSET])]
        if end[zipfile._ECD_SIGNATURE] == zipfile.stringEndArchive64:
            f.seek(location + 4)
            record_size = struct.unpack("<Q", f.read(8))[0]
            ordinary_at = location + 12 + record_size + zipfile.sizeEndCentDir64Locator
            f.seek(ordinary_at)
            ordinary = f.read(zipfile.sizeEndCentDir)
            if len(ordinary) != zipfile.sizeEndCentDir or ordinary[:4] != zipfile.stringEndArchive:
                raise unreadable
            _, _, _, _, members, directory, offset, _ = struct.unpack(zipfile.structEndArchive, ordinary)
            sets.append((None if members == 0xFFFF else members, None if directory == 0xFFFFFFFF else directory,
                         None if offset == 0xFFFFFFFF else offset))
        for members, directory, offset in sets:
            if (members or 0) > MAX_MEMBERS or (directory or 0) > MAX_DIRECTORY:
                raise _too_many()
            if directory is not None and offset is not None and offset + directory != location:
                raise unreadable
        members, directory, start = sets[0][0], sets[0][1], sets[0][2]
        for other in sets[1:]:  # an ordinary value that isn't a marker must be the ZIP64 value
            if any(o is not None and o != v for o, v in zip(other, sets[0])):
                raise unreadable
        if directory < 0 or start < 0 or start + directory > size:
            raise unreadable
        f.seek(start)
        data = f.read(directory)
    count, at = 0, 0
    while at < len(data):  # the member headers, as ZipFile reads them
        if len(data) - at < 46 or data[at:at + 4] != b"PK\x01\x02":
            raise unreadable
        at += 46 + sum(struct.unpack("<3H", data[at + 28:at + 34]))
        count += 1
        if count > MAX_MEMBERS:
            raise _too_many()
    if at != len(data) or count != members:
        raise unreadable


def _unpack(zip_path, dest, budget):
    root = Path(dest).resolve()
    check_zip_file(zip_path)
    with zipfile.ZipFile(zip_path) as z:
        plan, folders, links = _zip_plan(z, root)
        for rel in sorted(folders, key=len):
            _count(budget, 0)
            (root.joinpath(*rel)).mkdir(exist_ok=True)
        for info, target in plan:
            _count(budget, info.file_size)
            written = 0
            with z.open(info) as fin, open(target, "wb") as fout:
                while True:
                    chunk = fin.read(1 << 20)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > info.file_size or written > MAX_BYTES:
                        raise _too_large()
                    fout.write(chunk)
            os.chmod(target, 0o644)
    return links


# ---- finding and checking the system file ------------------------------------------------------------------------

class Structure:
    """The result of check_structure: the system file (relative to the upload), the team folder that holds it, and
    the messages. ok is False when we can't run the system as it is."""

    def __init__(self):
        self.entry = None
        self.team_root = None
        self.errors, self.notes = [], []

    @property
    def ok(self):
        return not self.errors and self.entry is not None

    def system_path(self):
        return None if self.entry is None else self.team_root / Path(self.entry).name

    def as_dict(self):
        return {"ok": self.ok, "entry": self.entry, "errors": self.errors, "notes": self.notes}


def _python_files(folder):
    """Regular .py files below folder, never following links, as paths relative to folder."""
    out, stack = [], [Path(folder)]
    while stack:
        d = stack.pop()
        with os.scandir(d) as it:
            for e in it:
                if e.name in SKIP_NAMES or e.is_symlink():
                    continue
                if e.is_dir(follow_symlinks=False):
                    stack.append(Path(e.path))
                elif e.name.endswith(".py") and e.is_file(follow_symlinks=False):
                    out.append(Path(e.path).relative_to(folder))
    return sorted(out, key=lambda p: (len(p.parts), str(p)))


def _read(path):
    """The text of a regular file, without following a link."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as f:
        return f.read().decode("utf-8", errors="replace")


def _parse(path):
    """(tree, None) or (None, (line, message))."""
    try:
        return ast.parse(_read(path), filename=path.name), None
    except SyntaxError as e:
        return None, (e.lineno or 0, e.msg or "invalid syntax")
    except (ValueError, RecursionError, MemoryError) as e:
        return None, (0, type(e).__name__)


def _defines_solve(tree):
    return _top_level(tree, "solve") is not None


def _top_level(tree, name):
    """The top-level statement that defines name: a def, an assignment or an import, else None."""
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == name:
            return node
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(t, ast.Name) and t.id == name for t in targets):
                return node
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if any((a.asname or a.name.split(".")[0]) == name for a in node.names):
                return node
    return None


def _arity_problem(node, name, wanted, call):
    """A message if the def can't take `wanted` positional arguments."""
    if isinstance(node, ast.AsyncFunctionDef):
        return f"You defined {name} with async def, but we call it as a normal function, as {call}. Use def instead."
    if not isinstance(node, ast.FunctionDef):
        return None
    a = node.args
    positional = len(a.posonlyargs) + len(a.args)
    required = positional - len(a.defaults)
    kw_required = [k.arg for k, d in zip(a.kwonlyargs, a.kw_defaults) if d is None]
    if (positional < wanted and a.vararg is None) or required > wanted or kw_required:
        return (f"You defined {name} in line {node.lineno} with {_args_words(node)}, but we call it with {wanted} "
                f"argument{'s' if wanted != 1 else ''}, as {call}.")
    return None


def _args_words(node):
    names = [x.arg for x in node.args.posonlyargs + node.args.args]
    if not names:
        return "no arguments"
    return f"{len(names)} argument{'s' if len(names) != 1 else ''} ({', '.join(names)})"


def _guarded(stack):
    """True if an import sits in a try block that catches ImportError, or under `if TYPE_CHECKING:`."""
    for node, field in stack:
        if isinstance(node, ast.Try) and field == "body":
            for h in node.handlers:
                if h.type is None:
                    return "try"
                names = [h.type] if not isinstance(h.type, ast.Tuple) else list(h.type.elts)
                for n in names:
                    word = n.id if isinstance(n, ast.Name) else (n.attr if isinstance(n, ast.Attribute) else "")
                    if word in ("ImportError", "ModuleNotFoundError", "Exception", "BaseException"):
                        return "try"
        if isinstance(node, ast.If) and field == "body":
            t = node.test
            word = t.id if isinstance(t, ast.Name) else (t.attr if isinstance(t, ast.Attribute) else "")
            if word == "TYPE_CHECKING":
                return "type-checking"
    return None


def _imports(tree):
    """(node, guard) for every import in the tree, also inside functions, with how it is guarded."""
    out = []

    def walk(node, stack):
        for field, value in ast.iter_fields(node):
            items = value if isinstance(value, list) else [value]
            for child in items:
                if isinstance(child, ast.AST):
                    if isinstance(child, (ast.Import, ast.ImportFrom)):
                        out.append((child, _guarded(stack + [(node, field)])))
                    walk(child, stack + [(node, field)])
    walk(tree, [])
    return out


def check_structure(folder, packages_txt=PACKAGES_TXT, entry_name=ENTRY, also_allowed=()):
    """Find the system file in the copied upload and check it. Returns a Structure. also_allowed: more top-level
    names that the team may import, which the grader provides."""
    folder = Path(folder)
    res = Structure()
    pys = _python_files(folder)
    if not pys:
        res.errors.append(f"We found no Python file in your upload. Upload {entry_name}, the file with your "
                          "solve(request_text, tools) function, and the modules and files it uses.")
        return res
    tops = [p for p in folder.iterdir() if p.is_dir() and not p.is_symlink() and p.name not in SKIP_NAMES
            and not p.name.startswith(".")]
    loose = [p for p in folder.iterdir() if p.is_file() and not p.name.startswith(".")]
    named = [p for p in pys if p.name == entry_name]
    if Path(entry_name) in named:
        entry = Path(entry_name)
    elif len(tops) == 1 and not any(p.suffix == ".py" for p in loose) and Path(tops[0].name, entry_name) in named:
        entry = Path(tops[0].name, entry_name)  # everything inside one top folder, as in a zip of the project
    elif named:
        res.errors.append(f"We found {entry_name} only in {_and(str(p.parent) + '/' for p in named[:3])}. Put "
                          f"{entry_name} at the top of your upload, next to the modules it imports.")
        return res
    else:
        with_solve = []
        for p in pys:
            tree, _ = _parse(folder / p)
            if tree is not None and _defines_solve(tree):
                with_solve.append(p)
        if len(with_solve) == 1:
            entry = with_solve[0]
            res.notes.append(f"We used {entry} as your system, because your upload has no {entry_name} and {entry} is "
                             f"the only file with solve(). Name it {entry_name}, so that we always pick the right "
                             "file.")
        elif not with_solve:
            res.errors.append(f"Your upload has no {entry_name}, and none of its Python files defines solve. Upload "
                              f"{entry_name} with your solve(request_text, tools) function.")
            return res
        else:
            res.errors.append(f"Your upload has no {entry_name}, and we found solve() in "
                              f"{_and(str(p) for p in with_solve[:4])}, so we don't know which file is your "
                              f"system. Name the file with your system {entry_name}.")
            return res
    res.entry = str(entry)
    res.team_root = folder / entry.parent
    root = res.team_root
    shown = lambda p: str(p.relative_to(root)) if root in p.parents or p == root else str(p.relative_to(folder))  # noqa: E731

    tree, problem = _parse(folder / entry)
    if problem:
        line, msg = problem
        res.errors.append(f"{shown(folder / entry)} has a syntax error in line {line} ({msg}). Fix it, then submit "
                          "again.")
        return res
    solve = _top_level(tree, "solve")
    if solve is None:
        res.errors.append(f"{shown(folder / entry)} has no solve function. Define solve(request_text, tools) at the "
                          "top level of the file, not inside a class or another function.")
    else:
        p = _arity_problem(solve, "solve", 2, "solve(request_text, tools)")
        if p:
            res.errors.append(p)
    setup = _top_level(tree, "setup")
    if setup is not None:
        p = _arity_problem(setup, "setup", 1, "setup(tools)")
        if p:
            res.errors.append(p)

    # Imports, in the system file and in every other Python file in the team folder.
    allowed = set(sys.stdlib_module_names) | package_imports(packages_txt) | ALWAYS | set(also_allowed)
    own = {p.stem for p in root.iterdir() if p.is_file() and p.suffix == ".py"} | \
          {p.name for p in root.iterdir() if p.is_dir() and not p.is_symlink()}
    unlisted, guarded_notes, relative = {}, {}, []
    for p in _python_files(root):
        path = root / p
        t = tree if path == folder / entry else _parse(path)[0]
        if t is None:
            line, msg = _parse(path)[1]
            res.notes.append(f"{p} has a syntax error in line {line} ({msg}). If you import {p.stem} in your system, "
                             "your system fails to load.")
            continue
        in_package = len(p.parts) > 1 and (root / p.parent / "__init__.py").is_file()
        for node, guard in _imports(t):
            if isinstance(node, ast.ImportFrom) and node.level:
                if not in_package and guard != "type-checking":
                    relative.append((p, node.lineno))
                continue
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            for name in names:
                top = name.split(".")[0]
                if not top or top in allowed or top in own:
                    continue
                if guard == "type-checking":
                    continue
                target = guarded_notes if guard else unlisted
                target.setdefault(top, (p, node.lineno))
    words = package_words(packages_txt)
    for top, (p, line) in sorted(unlisted.items()):
        res.errors.append(f"You import {top} in {p}, line {line}, but {top} isn't on the package list, so we can't "
                          f"load your system in grading. Use the standard library, your own modules, or one of these "
                          f"packages: {words}.")
    for top, (p, line) in sorted(guarded_notes.items()):
        res.notes.append(f"You import {top} in {p}, line {line}, inside try and except. {top} isn't on the package "
                         "list, so in grading the import fails and your except branch runs.")
    for p, line in relative[:3]:
        res.errors.append(f"You use a relative import in {p}, line {line} (from . import ...). We load {res.entry} "
                          "as a single file, not as a package, so write import helpers instead of from . import "
                          "helpers.")
    return res


def structure_text(res):
    """The upload check's text for the team, from Structure.as_dict(): the same words on Gradescope and in
    starter/check_upload.py."""
    lines = []
    if res["ok"]:
        lines.append(f"We found your system in {res['entry']}, with solve(request_text, tools), and every import is "
                     "from the standard library, your own modules or the package list.")
    lines += [f"- {e}" for e in res["errors"]] + [f"- {x}" for x in res["notes"]]
    return "\n".join(lines)
