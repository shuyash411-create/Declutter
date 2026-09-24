"""Locating bundled resources (Haar cascade, Tesseract) in dev and frozen builds."""

import os
import shutil
import sys
import threading
from pathlib import Path

_PKG_DIR = Path(__file__).resolve().parent


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def bundle_dir() -> Path:
    """Root of the PyInstaller bundle (or the repo root when running from source)."""
    if is_frozen() and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return _PKG_DIR.parent


def asset_path(name: str) -> Path:
    """Path of a file in declutter/assets, both from source and when frozen."""
    return bundle_dir() / "declutter" / "assets" / name


def _tesseract_exe_name() -> str:
    return "tesseract.exe" if os.name == "nt" else "tesseract"


def _candidate_tesseract_dirs():
    env = os.environ.get("DECLUTTER_TESSERACT_DIR")
    if env:
        yield Path(env)
    # Bundled by packaging/collect_tesseract.py -> "<bundle>/tesseract/"
    yield bundle_dir() / "tesseract"
    # Staged next to the sources (dev builds after running collect_tesseract.py)
    yield _PKG_DIR.parent / "build" / "tesseract_bundle"


_TESSERACT_CACHE = {"resolved": False, "cmd": None, "bundled": False}
_TESSERACT_LOCK = threading.Lock()


def configure_tesseract():
    """Point pytesseract at a bundled Tesseract if present, else at the system one.

    Returns (command_path or None, is_bundled).
    """
    with _TESSERACT_LOCK:
        if not _TESSERACT_CACHE["resolved"]:
            _resolve_tesseract()
    return _TESSERACT_CACHE["cmd"], _TESSERACT_CACHE["bundled"]


def _resolve_tesseract():

    # Several Tesseract processes run at once (one per worker thread); stop each
    # from also spawning an OpenMP thread per core, which is ~30x slower overall.
    os.environ.setdefault("OMP_THREAD_LIMIT", "1")

    cmd, bundled = None, False
    for d in _candidate_tesseract_dirs():
        exe = d / _tesseract_exe_name()
        if exe.is_file():
            cmd, bundled = str(exe), True
            tessdata = d / "tessdata"
            if tessdata.is_dir():
                os.environ["TESSDATA_PREFIX"] = str(tessdata)
            lib = d / "lib"
            if lib.is_dir():
                var = "DYLD_LIBRARY_PATH" if sys.platform == "darwin" else "LD_LIBRARY_PATH"
                if os.name != "nt":
                    old = os.environ.get(var, "")
                    os.environ[var] = str(lib) + (os.pathsep + old if old else "")
            if os.name != "nt" and not os.access(exe, os.X_OK):
                try:
                    exe.chmod(0o755)
                except OSError:
                    pass
            break

    if cmd is None:
        found = shutil.which("tesseract")
        if not found:
            for p in (
                r"C:\Program Files\Tesseract-OCR\tesseract.exe",
                r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
                "/opt/homebrew/bin/tesseract",
                "/usr/local/bin/tesseract",
                "/usr/bin/tesseract",
            ):
                if os.path.isfile(p):
                    found = p
                    break
        cmd = found

    if cmd:
        try:
            import pytesseract
            pytesseract.pytesseract.tesseract_cmd = cmd
            pytesseract.get_tesseract_version()
        except Exception:
            cmd = None

    _TESSERACT_CACHE.update(resolved=True, cmd=cmd, bundled=bundled and cmd is not None)
