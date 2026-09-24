"""Recursive discovery of images and documents across several root folders."""

import os
from dataclasses import dataclass
from pathlib import Path

from .config import DOC_EXTS, IMAGE_EXTS, SKIP_DIR_NAMES


@dataclass
class FoundFile:
    path: str        # absolute path
    root: str        # the selected folder it was found under
    kind: str        # "image" | "document"
    size: int
    mtime: float


def normalize_roots(folders):
    """Absolute, de-duplicated roots; a folder nested inside another selected
    folder is dropped (its files are already covered by the parent)."""
    roots = []
    for f in folders:
        p = os.path.realpath(os.path.expanduser(str(f)))
        if os.path.isdir(p) and p not in roots:
            roots.append(p)
    roots.sort(key=len)
    kept = []
    for r in roots:
        if not any(r == k or r.startswith(k.rstrip(os.sep) + os.sep) for k in kept):
            kept.append(r)
    return kept


def scan_folders(folders, cancel_event=None):
    """Walk every folder and return a list of FoundFile (one combined dataset).

    Hidden directories and a few system/tool directories are skipped. The same
    physical file reached twice (symlinks, overlapping roots) is reported once.
    """
    results, seen = [], set()
    for root in normalize_roots(folders):
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            if cancel_event is not None and cancel_event.is_set():
                return results
            dirnames[:] = [
                d for d in dirnames
                if not d.startswith(".") and d.lower() not in SKIP_DIR_NAMES
            ]
            for name in filenames:
                if name.startswith(".") or name.startswith("~$"):
                    continue  # hidden files, Office lock files
                ext = os.path.splitext(name)[1].lower()
                if ext in IMAGE_EXTS:
                    kind = "image"
                elif ext in DOC_EXTS:
                    kind = "document"
                else:
                    continue
                full = os.path.join(dirpath, name)
                try:
                    st = os.stat(full)
                except OSError:
                    continue
                if st.st_size == 0:
                    continue
                key = os.path.realpath(full)
                if key in seen:
                    continue
                seen.add(key)
                results.append(FoundFile(str(Path(full)), root, kind, st.st_size, st.st_mtime))
    return results
