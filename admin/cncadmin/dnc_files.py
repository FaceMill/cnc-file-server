"""Path safety, file listing and request-name resolution for NC programs.

Ported from serial-dnc's files.py. Folders are relative to /srv/cnc: a serial
machine's programs are in <name>/ and what it punches goes to <name>/received/.
"""
from __future__ import annotations

import os
import re

DATA_ROOT = os.path.realpath(os.environ.get("CNC_SRV_DIR", "/srv/cnc"))

# A received program containing "(SEND <name>)" is a request, not a program to save.
REQUEST_RE = re.compile(rb"\(\s*SEND\s+([^)]+?)\s*\)", re.IGNORECASE)
PROGRAM_NO_RE = re.compile(rb"^\s*[O:](\d+)", re.MULTILINE)


def resolve_dir(rel: str) -> str:
    """Map a folder to an absolute path that must stay inside DATA_ROOT."""
    path = os.path.realpath(os.path.join(DATA_ROOT, rel.strip().lstrip("/")))
    if path != DATA_ROOT and not path.startswith(DATA_ROOT + os.sep):
        raise ValueError(f"Folder '{rel}' is outside the data share")
    return path


def safe_file(folder_rel: str, name: str) -> str:
    folder = resolve_dir(folder_rel)
    path = os.path.realpath(os.path.join(folder, name))
    if not path.startswith(folder + os.sep):
        raise ValueError("Invalid file name")
    return path


def parse_excluded(excluded: str) -> set[str]:
    out = set()
    for ext in re.split(r"[,\s;]+", excluded or ""):
        ext = ext.strip().lower().lstrip("*").lstrip(".")
        if ext:
            out.add(ext)
    return out


def is_excluded(filename: str, excluded: set[str]) -> bool:
    ext = os.path.splitext(filename)[1].lower().lstrip(".")
    return ext in excluded


def list_files(folder_rel: str, excluded: set[str] = frozenset()) -> list[dict]:
    """Files directly in a folder (no hidden files or skipped types), sorted by name."""
    folder = resolve_dir(folder_rel)
    out = []
    try:
        names = sorted(os.listdir(folder), key=str.lower)
    except OSError:
        return out
    for name in names:
        path = os.path.join(folder, name)
        if name.startswith(".") or not os.path.isfile(path) or is_excluded(name, excluded):
            continue
        st = os.stat(path)
        out.append({"name": name, "size": st.st_size, "mtime": st.st_mtime})
    return out


def list_programs(machine: dict) -> list[dict]:
    """Files in the machine's program folder, excluding skipped extensions."""
    return list_files(machine["program_dir"], parse_excluded(machine["excluded_ext"]))


def program_number(data: bytes) -> int | None:
    m = PROGRAM_NO_RE.search(data)
    return int(m.group(1)) if m else None


def find_request(data: bytes) -> str | None:
    m = REQUEST_RE.search(data)
    return m.group(1).decode("ascii", "replace").strip() if m else None


def resolve_request(machine: dict, name: str) -> str:
    """Find the file a "(SEND name)" request refers to.

    Tried in order: exact file name, file name without extension, then (for
    names like O1234) the program number inside each file.
    """
    files = [f["name"] for f in list_programs(machine)]
    folder = resolve_dir(machine["program_dir"])
    lname = name.lower()

    exact = [f for f in files if f.lower() == lname]
    if exact:
        return os.path.join(folder, exact[0])

    stem = [f for f in files if os.path.splitext(f)[0].lower() == lname]
    if len(stem) == 1:
        return os.path.join(folder, stem[0])
    if len(stem) > 1:
        raise LookupError(f"'{name}' matches several files: {', '.join(stem)}")

    m = re.fullmatch(r"[Oo:]?(\d+)", name)
    if m:
        wanted = int(m.group(1))
        hits = []
        for f in files:
            path = os.path.join(folder, f)
            try:
                with open(path, "rb") as fh:
                    head = fh.read(4096)
            except OSError:
                continue
            if program_number(head) == wanted:
                hits.append(f)
        if len(hits) == 1:
            return os.path.join(folder, hits[0])
        if len(hits) > 1:
            raise LookupError(f"Program O{wanted} found in several files: {', '.join(hits)}")

    raise LookupError(f"No file matching '{name}' in {machine['program_dir'] or '/'}")
