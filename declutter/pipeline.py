"""Orchestrates a full scan: discover -> analyse -> group duplicates -> report."""

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from .photos import analyze_image, group_duplicates
from .report import write_report
from .resources import configure_tesseract
from .scanner import normalize_roots, scan_folders


class Cancelled(Exception):
    pass


def default_report_dir() -> Path:
    return Path.home() / "Declutter Reports"


def _workers():
    return max(2, min(8, (os.cpu_count() or 2)))


def _run_parallel(fn, items, stage, progress, cancel_event, workers):
    out = [None] * len(items)
    if not items:
        return out
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(fn, it): i for i, it in enumerate(items)}
        try:
            for fut in as_completed(futs):
                if cancel_event is not None and cancel_event.is_set():
                    raise Cancelled()
                out[futs[fut]] = fut.result()
                done += 1
                progress(stage, done, len(items), os.path.basename(items[futs[fut]].path))
        except Cancelled:
            for f in futs:
                f.cancel()
            raise
    return out


def run_scan(folders, progress=None, cancel_event=None, use_ocr=True, report_path=None):
    """Run the whole pipeline and write the HTML report.

    progress(stage, done, total, message) is called from worker threads.
    Returns a summary dict including 'report_path'.
    """
    progress = progress or (lambda *a: None)
    cancel_event = cancel_event or threading.Event()
    t0 = time.time()
    roots = normalize_roots(folders)
    if not roots:
        raise ValueError("None of the selected folders exist.")

    progress("scan", 0, 0, "Looking for photos and documents…")
    files = scan_folders(roots, cancel_event)
    if cancel_event.is_set():
        raise Cancelled()
    images = [f for f in files if f.kind == "image"]
    docs = [f for f in files if f.kind == "document"]
    progress("scan", len(files), len(files), f"Found {len(images)} photos and {len(docs)} documents")

    tess_cmd, tess_bundled = configure_tesseract() if use_ocr else (None, False)
    ocr_on = bool(use_ocr and tess_cmd)
    workers = _workers()

    photo_recs = _run_parallel(lambda f: analyze_image(f, use_ocr=ocr_on), images,
                               "photos", progress, cancel_event, workers)
    progress("dups", 0, 1, "Grouping duplicate photos…")
    photo_groups = group_duplicates(photo_recs)

    doc_recs, doc_groups = [], 0
    if docs:
        from .documents import analyze_document, group_duplicate_docs
        doc_recs = _run_parallel(lambda f: analyze_document(f, use_ocr=ocr_on), docs,
                                 "documents", progress, cancel_event, workers)
        progress("dups", 0, 1, "Grouping duplicate documents…")
        doc_groups = group_duplicate_docs(doc_recs)

    if report_path is None:
        default_report_dir().mkdir(parents=True, exist_ok=True)
        report_path = default_report_dir() / f"declutter_report_{datetime.now():%Y%m%d_%H%M%S}.html"
    progress("report", 0, 1, "Writing report…")
    meta = {
        "roots": roots,
        "created": datetime.now().isoformat(timespec="seconds"),
        "ocr": "bundled Tesseract" if tess_bundled else ("system Tesseract" if ocr_on else
                                                           ("disabled" if not use_ocr else "not available")),
        "duration_s": round(time.time() - t0, 1),
        "photo_groups": photo_groups,
        "doc_groups": doc_groups,
    }
    write_report(report_path, photo_recs, doc_recs, meta)
    progress("done", 1, 1, "Report ready")
    return {
        "report_path": str(report_path),
        "photos": len(photo_recs),
        "documents": len(doc_recs),
        "photo_dup_groups": photo_groups,
        "doc_dup_groups": doc_groups,
        "ocr": meta["ocr"],
        "duration_s": round(time.time() - t0, 1),
    }
