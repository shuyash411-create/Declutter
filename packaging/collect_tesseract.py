"""Stage a self-contained Tesseract (binary + shared libraries + English data)
into build/tesseract_bundle so PyInstaller can embed it in the app.

    python packaging/collect_tesseract.py [--tesseract PATH] [--langs eng,osd]

Layout produced (what declutter/resources.py expects at runtime):
    tesseract_bundle/tesseract[.exe]
    tesseract_bundle/lib/...          (Linux/macOS: private shared libraries)
    tesseract_bundle/*.dll            (Windows: the installer's DLLs)
    tesseract_bundle/tessdata/eng.traineddata

Windows: install Tesseract first (UB-Mannheim build, e.g. `choco install tesseract`).
macOS:   `brew install tesseract`.   Linux: `apt install tesseract-ocr`.
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "build" / "tesseract_bundle"

# never bundle the C runtime / loader on Linux (must come from the host)
LINUX_SYSTEM_LIBS = re.compile(
    r"^(linux-vdso|ld-linux|libc\.so|libm\.so|libpthread|libdl\.so|librt\.so|libresolv|libutil\.so|libnsl)")


def find_tesseract(explicit=None):
    if explicit:
        return Path(explicit).resolve()
    found = shutil.which("tesseract")
    if not found and os.name == "nt":
        for p in (r"C:\Program Files\Tesseract-OCR\tesseract.exe", r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"):
            if os.path.isfile(p):
                found = p
    if not found:
        sys.exit("Tesseract not found. Install it first (see the docstring) or pass --tesseract PATH.")
    p = Path(found).resolve()
    # Chocolatey puts a shim in its bin folder; the real install is Program Files
    if os.name == "nt" and "chocolatey" in str(p).lower():
        real = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
        if real.exists():
            p = real
    return p


def tessdata_dir(exe: Path) -> Path:
    try:
        out = subprocess.run([str(exe), "--list-langs"], capture_output=True, text=True, timeout=30)
        m = re.search(r'"(.+?)"', out.stdout + out.stderr)
        if m and Path(m.group(1)).is_dir():
            return Path(m.group(1))
    except OSError:
        pass
    for cand in (exe.parent / "tessdata", exe.parent.parent / "share" / "tessdata",
                 Path("/usr/share/tesseract-ocr/5/tessdata"), Path("/usr/share/tessdata"),
                 Path("/opt/homebrew/share/tessdata"), Path("/usr/local/share/tessdata")):
        if cand.is_dir():
            return cand
    sys.exit("Could not find the tessdata folder.")


def copy_tessdata(src: Path, dst: Path, langs):
    dst.mkdir(parents=True, exist_ok=True)
    for lang in langs:
        f = src / f"{lang}.traineddata"
        if f.exists():
            shutil.copy2(f, dst / f.name)
        elif lang == "eng":
            sys.exit(f"{f} is missing - English language data is required.")
    for sub in ("configs", "tessconfigs"):
        if (src / sub).is_dir():
            shutil.copytree(src / sub, dst / sub, dirs_exist_ok=True)


# ------------------------------------------------------------------ Windows --
def collect_windows(exe: Path, langs):
    install = exe.parent
    OUT.mkdir(parents=True)
    for f in install.iterdir():
        if f.suffix.lower() in (".exe", ".dll") and not f.name.lower().startswith("unins"):
            shutil.copy2(f, OUT / f.name)
    copy_tessdata(install / "tessdata" if (install / "tessdata").is_dir() else tessdata_dir(exe),
                  OUT / "tessdata", langs)


# -------------------------------------------------------------------- Linux --
def collect_linux(exe: Path, langs):
    (OUT / "lib").mkdir(parents=True)
    shutil.copy2(exe, OUT / "tesseract")
    out = subprocess.run(["ldd", str(exe)], capture_output=True, text=True, check=True).stdout
    for line in out.splitlines():
        m = re.match(r"\s*(\S+)\s+=>\s+(\S+)", line)
        if not m or LINUX_SYSTEM_LIBS.match(m.group(1)) or not os.path.isfile(m.group(2)):
            continue
        shutil.copy2(os.path.realpath(m.group(2)), OUT / "lib" / m.group(1))
    copy_tessdata(tessdata_dir(exe), OUT / "tessdata", langs)


# -------------------------------------------------------------------- macOS --
def _otool_deps(path):
    out = subprocess.run(["otool", "-L", str(path)], capture_output=True, text=True, check=True).stdout
    return [ln.strip().split(" (")[0] for ln in out.splitlines()[1:] if ln.strip()]


def _is_system_dylib(dep):
    return dep.startswith("/usr/lib/") or dep.startswith("/System/")


def _resolve_dylib(dep, referrer: Path, search):
    if dep.startswith("@"):
        name = dep.split("/", 1)[1] if "/" in dep else dep
        for d in [referrer.parent, *search]:
            cand = Path(d) / Path(name).name
            if cand.exists():
                return cand.resolve()
        return None
    p = Path(dep)
    return p.resolve() if p.exists() else None


def collect_macos(exe: Path, langs):
    lib = OUT / "lib"
    lib.mkdir(parents=True)
    exe = exe.resolve()
    search = [exe.parent.parent / "lib", Path("/opt/homebrew/lib"), Path("/usr/local/lib")]
    target = OUT / "tesseract"
    shutil.copy2(exe, target)
    os.chmod(target, 0o755)

    copied = {}  # original real path -> bundled Path
    queue = [(exe, target)]
    while queue:
        src, bundled = queue.pop()
        for dep in _otool_deps(src):
            if _is_system_dylib(dep):
                continue
            real = _resolve_dylib(dep, src, search)
            if real is None:
                print(f"warning: cannot resolve {dep} (needed by {src.name})")
                continue
            if real not in copied:
                dst = lib / real.name
                shutil.copy2(real, dst)
                os.chmod(dst, 0o755)
                copied[real] = dst
                subprocess.run(["install_name_tool", "-id", f"@executable_path/lib/{real.name}", str(dst)], check=True)
                queue.append((real, dst))
            subprocess.run(["install_name_tool", "-change", dep, f"@executable_path/lib/{real.name}", str(bundled)],
                           check=True)
    # rewriting load commands invalidates signatures; ad-hoc re-sign everything
    for f in [target, *lib.iterdir()]:
        subprocess.run(["codesign", "--force", "--sign", "-", str(f)], check=False)
    copy_tessdata(tessdata_dir(exe), OUT / "tessdata", langs)


def verify():
    exe = OUT / ("tesseract.exe" if os.name == "nt" else "tesseract")
    env = dict(os.environ, TESSDATA_PREFIX=str(OUT / "tessdata"))
    if sys.platform.startswith("linux"):
        env["LD_LIBRARY_PATH"] = str(OUT / "lib")
    out = subprocess.run([str(exe), "--list-langs"], capture_output=True, text=True, env=env, timeout=60)
    print(out.stdout.strip() or out.stderr.strip())
    if out.returncode != 0 or "eng" not in out.stdout:
        sys.exit("Bundled tesseract failed its self-test.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tesseract", help="path to the tesseract executable to bundle")
    ap.add_argument("--langs", default="eng")
    a = ap.parse_args()
    exe = find_tesseract(a.tesseract)
    langs = [x.strip() for x in a.langs.split(",") if x.strip()]
    if OUT.exists():
        shutil.rmtree(OUT)
    print(f"Bundling {exe} -> {OUT}")
    if os.name == "nt":
        collect_windows(exe, langs)
    elif sys.platform == "darwin":
        collect_macos(exe, langs)
    else:
        collect_linux(exe, langs)
    verify()
    size = sum(f.stat().st_size for f in OUT.rglob("*") if f.is_file())
    print(f"OK: {size / 1e6:.1f} MB staged in {OUT}")


if __name__ == "__main__":
    main()
