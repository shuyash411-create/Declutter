"""Applying exported move / delete lists - the only code that touches user files.

Safety rules (enforced for every item, never skipped):
  * only lists exported by the declutter report are accepted (list_type checked)
  * the file must sit inside one of the folders that were scanned
  * the file must still exist with the same size and modification time it had
    at scan time; anything changed since is skipped
  * moves never overwrite: name clashes get a " (1)", " (2)" ... suffix
  * every applied move is written to an undo log
"""

import json
import os
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

DEFAULT_OUTPUT_DIRNAME = "Declutter Organized"
MTIME_TOLERANCE_S = 2.0


class ListError(Exception):
    pass


@dataclass
class Plan:
    list_type: str
    ok: list = field(default_factory=list)       # (item, destination-or-None)
    skipped: list = field(default_factory=list)  # (item, reason)

    @property
    def total_bytes(self):
        return sum(it.get("size", 0) for it, _ in self.ok)


def load_list(path, expected_type):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        raise ListError(f"Could not read {path}: {e}")
    if not isinstance(data, dict) or data.get("tool") != "declutter" or not isinstance(data.get("items"), list):
        raise ListError(f"{path} is not a list exported from a declutter report.")
    if data.get("list_type") != expected_type:
        raise ListError(f"{path} is a '{data.get('list_type')}' list, expected a '{expected_type}' list.")
    return data


def _inside(path, folder):
    path, folder = os.path.normcase(os.path.abspath(path)), os.path.normcase(os.path.abspath(folder))
    return path == folder or path.startswith(folder.rstrip(os.sep) + os.sep)


def _check(item, roots):
    p, root = item.get("path"), item.get("root")
    if not p or not root:
        return "malformed entry"
    if not any(_inside(root, r) or _inside(r, root) for r in roots) or not _inside(p, root):
        return "outside the scanned folders"
    if not os.path.isfile(p):
        return "file no longer exists"
    st = os.stat(p)
    if "size" in item and st.st_size != item["size"]:
        return "file changed since the scan (size differs)"
    if "mtime" in item and abs(st.st_mtime - float(item["mtime"])) > MTIME_TOLERANCE_S:
        return "file changed since the scan (modified time differs)"
    return None


def _unique(dest: Path) -> Path:
    if not dest.exists():
        return dest
    stem, suf, n = dest.stem, dest.suffix, 1
    while True:
        cand = dest.with_name(f"{stem} ({n}){suf}")
        if not cand.exists():
            return cand
        n += 1


def plan_moves(data, destination=None):
    """destination overrides the list's destination; default is
    '<scanned folder>/Declutter Organized/<category subfolder>'."""
    roots = data.get("scanned_folders") or []
    dest_base = destination or data.get("destination")
    plan, claimed = Plan("move"), set()
    for it in data["items"]:
        why = _check(it, roots)
        sub = it.get("subfolder")
        if not why and not sub:
            why = "no destination category"
        if why:
            plan.skipped.append((it, why))
            continue
        base = Path(os.path.expanduser(dest_base)) if dest_base else Path(it["root"]) / DEFAULT_OUTPUT_DIRNAME
        target_dir = base.joinpath(*sub.split("/"))
        src = Path(it["path"])
        if src.parent.resolve() == target_dir.resolve():
            plan.skipped.append((it, "already in its category folder"))
            continue
        dest = target_dir / src.name
        # avoid two sources in this batch picking the same new name
        n, stem, suf = 1, dest.stem, dest.suffix
        while dest.exists() or str(dest).lower() in claimed:
            dest = target_dir / f"{stem} ({n}){suf}"
            n += 1
        claimed.add(str(dest).lower())
        plan.ok.append((it, dest))
    return plan


def plan_deletions(data):
    roots = data.get("scanned_folders") or []
    plan = Plan("delete")
    for it in data["items"]:
        why = _check(it, roots)
        if why:
            plan.skipped.append((it, why))
        else:
            plan.ok.append((it, None))
    return plan


def apply_moves(plan, log_dir=None, progress=None):
    """Execute a move plan. Returns (moved, failed list, undo log path)."""
    done, failed = [], []
    for k, (it, dest) in enumerate(plan.ok):
        try:
            dest = _unique(Path(dest))
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(it["path"], str(dest))
            done.append({"from": it["path"], "to": str(dest)})
        except OSError as e:
            failed.append((it, str(e)))
        if progress:
            progress(k + 1, len(plan.ok))
    log_path = None
    if done:
        log_dir = Path(log_dir) if log_dir else Path.home() / "Declutter Reports"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"declutter_move_log_{datetime.now():%Y%m%d_%H%M%S}.json"
        log_path.write_text(json.dumps({"tool": "declutter", "log_type": "moves", "moves": done}, indent=2),
                            encoding="utf-8")
    return len(done), failed, log_path


def undo_moves(log_path):
    """Move files from a move log back to where they came from."""
    data = json.loads(Path(log_path).read_text(encoding="utf-8"))
    if data.get("log_type") != "moves":
        raise ListError(f"{log_path} is not a declutter move log.")
    restored, failed = 0, []
    for m in reversed(data["moves"]):
        try:
            if not os.path.exists(m["to"]):
                raise OSError("file is no longer at its moved location")
            if os.path.exists(m["from"]):
                raise OSError("original location is occupied")
            os.makedirs(os.path.dirname(m["from"]), exist_ok=True)
            shutil.move(m["to"], m["from"])
            restored += 1
        except OSError as e:
            failed.append((m, str(e)))
    return restored, failed


def apply_deletions(plan, quarantine=None, progress=None):
    """Delete the planned files (or move them into a quarantine folder).
    Returns (deleted, failed list)."""
    done, failed = 0, []
    qdir = None
    if quarantine:
        qdir = Path(os.path.expanduser(quarantine)) / f"deleted_{time.strftime('%Y%m%d_%H%M%S')}"
    for k, (it, _) in enumerate(plan.ok):
        try:
            if qdir:
                rel = os.path.relpath(it["path"], os.path.dirname(it["root"].rstrip(os.sep)) or it["root"])
                dest = _unique(qdir / rel)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(it["path"], str(dest))
            else:
                os.remove(it["path"])
            done += 1
        except OSError as e:
            failed.append((it, str(e)))
        if progress:
            progress(k + 1, len(plan.ok))
    return done, failed
