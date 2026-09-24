"""Headless scan: `declutter --scan FOLDER [FOLDER ...] [--out report.html]`.

Useful for scripting and for smoke-testing a packaged build."""

import argparse
import sys

from .pipeline import run_scan


def main(argv):
    ap = argparse.ArgumentParser(prog="declutter", description="Scan folders and write the HTML report.")
    ap.add_argument("--scan", nargs="+", metavar="FOLDER", required=True)
    ap.add_argument("--out", help="report path (default: ~/Declutter Reports/declutter_report_<time>.html)")
    ap.add_argument("--no-ocr", action="store_true", help="skip Tesseract OCR")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)

    def progress(stage, done, total, msg):
        if not a.quiet and sys.stdout:
            print(f"[{stage}] {done}/{total} {msg}", flush=True)

    res = run_scan(a.scan, progress=progress, use_ocr=not a.no_ocr, report_path=a.out)
    if sys.stdout:
        print(res, flush=True)
    return 0
