"""One-command build of the single-file declutter app for the current OS.

    python packaging/build.py            # Windows -> dist/declutter.exe
                                         # macOS   -> dist/declutter.app (+ dist/declutter)
                                         # Linux   -> dist/declutter

Steps: stage Tesseract (packaging/collect_tesseract.py), draw the icon, then run
PyInstaller with the required console-free flags:
    macOS / Linux:  pyinstaller --onefile --windowed main.py
    Windows:        pyinstaller --onefile --noconsole main.py
plus --add-data for the Haar cascade and the Tesseract bundle.
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build"
SEP = os.pathsep  # PyInstaller --add-data separator


def make_icon():
    from PIL import Image, ImageDraw

    size = 1024
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((40, 40, size - 40, size - 40), radius=220, fill=(37, 99, 235, 255))
    # three stacked "cards" being sorted
    for k, (dx, col) in enumerate(((-150, (191, 219, 254)), (0, (255, 255, 255)), (150, (147, 197, 253)))):
        x0, y0 = 300 + dx + k * 10, 250 + k * 120
        d.rounded_rectangle((x0, y0, x0 + 420, y0 + 300), radius=40, fill=col, outline=(30, 64, 175), width=14)
    d.line((330, 820, 700, 820), fill=(255, 255, 255), width=40)
    BUILD.mkdir(exist_ok=True)
    png = BUILD / "icon.png"
    img.save(png)
    img.save(BUILD / "icon.ico", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    try:
        img.save(BUILD / "icon.icns")
    except Exception as e:  # older Pillow without ICNS writer
        print("icns not written:", e)
    return png


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-tesseract", action="store_true", help="build without bundling Tesseract")
    ap.add_argument("--tesseract", help="path to the tesseract executable to bundle")
    a = ap.parse_args()

    os.chdir(ROOT)
    make_icon()
    if not a.no_tesseract:
        cmd = [sys.executable, "packaging/collect_tesseract.py"]
        if a.tesseract:
            cmd += ["--tesseract", a.tesseract]
        subprocess.run(cmd, check=True)

    windows, mac = os.name == "nt", sys.platform == "darwin"
    args = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile",
            "--noconsole" if windows else "--windowed",
            "--name", "declutter",
            "--add-data", f"declutter/assets{SEP}declutter/assets",
            # heavy optional imports we never use
            "--exclude-module", "matplotlib", "--exclude-module", "IPython", "--exclude-module", "pytest",
            "--exclude-module", "skimage", "--exclude-module", "reportlab", "--exclude-module", "playwright"]
    if not a.no_tesseract:
        args += ["--add-data", f"build/tesseract_bundle{SEP}tesseract"]
    if windows:
        args += ["--icon", "build/icon.ico"]
    elif mac:
        if (BUILD / "icon.icns").exists():
            args += ["--icon", "build/icon.icns"]
        args += ["--osx-bundle-identifier", "app.declutter.organizer"]
    args.append("main.py")
    print(" ".join(args))
    subprocess.run(args, check=True)

    out = ROOT / "dist" / ("declutter.exe" if windows else "declutter.app" if mac else "declutter")
    print(f"\nBuilt: {out}")


if __name__ == "__main__":
    main()
