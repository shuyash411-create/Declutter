"""End-to-end tests on a generated two-folder dataset.

    pip install -r requirements-dev.txt
    pytest -q
"""

import json
import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

pytest.importorskip("skimage")
pytest.importorskip("reportlab")

import make_fixtures  # noqa: E402

from declutter import actions  # noqa: E402
from declutter.pipeline import run_scan  # noqa: E402
from declutter.resources import configure_tesseract  # noqa: E402

HAS_OCR = configure_tesseract()[0] is not None


@pytest.fixture(scope="module")
def scanned(tmp_path_factory):
    base = tmp_path_factory.mktemp("fx")
    make_fixtures.build(base)
    report = base / "report.html"
    res = run_scan([base / "old_phone_backup", base / "new_phone_backup"], report_path=report)
    html = report.read_text(encoding="utf-8")
    data = json.loads(re.search(r'<script type="application/json" id="dl-data">(.*?)</script>', html, re.S)
                      .group(1).replace("<\\/", "</"))
    return base, res, html, data


def _by_name(data, name):
    return next((k, v) for k, v in data["items"].items() if os.path.basename(v["path"]) == name)


def _checked(html, item_id):
    m = re.search(r'<input type="checkbox" class="chk" data-id="%s"( checked)?' % item_id, html)
    return bool(m and m.group(1))


def _category(html, item_id):
    sel = re.search(r'<select class="cat" data-id="%s"[^>]*>(.*?)</select>' % item_id, html, re.S).group(1)
    return re.search(r'<option value="([^"]+)" selected', sel).group(1)


def test_counts(scanned):
    _, res, _, _ = scanned
    assert res["photos"] == 16          # hidden folder skipped, corrupt file counted
    assert res["documents"] == 9
    assert res["photo_dup_groups"] == 5
    assert res["doc_dup_groups"] == 3


def test_cross_folder_duplicates(scanned):
    _, _, html, data = scanned
    exact_old, _ = _by_name(data, "IMG_20230205_083000.jpg")
    exact_new, _ = _by_name(data, "coffee.jpg")
    assert data["items"][exact_old]["group"] == data["items"][exact_new]["group"]
    assert _checked(html, exact_old) != _checked(html, exact_new)  # exactly one copy pre-checked
    small, _ = _by_name(data, "astronaut_small_copy.jpg")
    big, _ = _by_name(data, "IMG_20230101_101010.jpg")
    assert data["items"][small]["group"] == data["items"][big]["group"]
    assert _checked(html, small) and not _checked(html, big)  # the higher-resolution copy is kept
    meme, _ = _by_name(data, "funny.jpg")
    assert data["items"][meme]["group"] is None  # an edit with different shape is not a duplicate


def test_photo_categories(scanned):
    _, _, html, data = scanned
    expect = {
        "IMG_20230101_101010.jpg": "people",
        "Screenshot_20240115-094100.png": "screenshot",
        "IMG_20230420_170000.jpg": "scenery",
        "IMG_20230205_083000.jpg": "food",
    }
    if HAS_OCR:
        expect.update({"IMG_20240314_184500.jpg": "receipt", "IMG_20240320_090000.jpg": "note",
                       "funny.jpg": "meme"})
    for name, cat in expect.items():
        assert _category(html, _by_name(data, name)[0]) == cat, name


def test_low_quality_flagged(scanned):
    _, _, html, data = scanned
    blur, _ = _by_name(data, "IMG_20230310_120001_blur.jpg")
    tiny, _ = _by_name(data, "tiny_icon.png")
    assert _checked(html, blur) and _checked(html, tiny)
    assert "blurry" in html and "very small" in html


def test_documents(scanned):
    _, _, html, data = scanned
    a, _ = _by_name(data, "invoice_feb.pdf")
    b, _ = _by_name(data, "invoice_feb (copy).pdf")   # re-saved: different bytes, same text
    assert data["items"][a]["group"] and data["items"][a]["group"] == data["items"][b]["group"]
    r1, _ = _by_name(data, "Priya_Resume.docx")
    r2, _ = _by_name(data, "CV_final_v2.docx")         # near-identical text
    assert data["items"][r1]["group"] == data["items"][r2]["group"]
    assert _category(html, a) == "invoice"
    assert _category(html, r1) == "resume"
    assert _category(html, _by_name(data, "certificate.pdf")[0]) == "id"
    assert _category(html, _by_name(data, "meeting.docx")[0]) == "notes"
    assert _category(html, _by_name(data, "budget.xlsx")[0]) == "other"
    if HAS_OCR:
        assert _category(html, _by_name(data, "scanned_bill.pdf")[0]) == "invoice"


def _export(data, html, list_type):
    """Mimic the report's export buttons with the default (pre-checked) state."""
    out = {"tool": "declutter", "list_type": list_type, "scanned_folders": data["roots"], "items": []}
    for k, it in data["items"].items():
        marked = _checked(html, k)
        if list_type == "delete" and marked:
            out["items"].append(dict(it))
        elif list_type == "move" and not marked:
            cat = _category(html, k)
            out["items"].append(dict(it, category=cat, subfolder=data["dest"][it["kind"]][cat]))
    return out


def test_apply_moves_and_deletions(scanned, tmp_path):
    base, _, html, data = scanned
    dele, move = _export(data, html, "delete"), _export(data, html, "move")
    assert dele["items"] and move["items"]
    assert not {i["path"] for i in dele["items"]} & {i["path"] for i in move["items"]}

    # a file modified after the scan must be skipped
    victim = dele["items"][0]["path"]
    with open(victim, "ab") as f:
        f.write(b"changed")
    plan = actions.plan_deletions(dele)
    assert any(it["path"] == victim for it, _ in plan.skipped)
    done, failed = actions.apply_deletions(plan)
    assert done == len(dele["items"]) - 1 and not failed
    assert os.path.exists(victim)

    plan = actions.plan_moves(move, destination=str(tmp_path / "organized"))
    moved, failed, log = actions.apply_moves(plan, log_dir=tmp_path)
    assert moved == len(move["items"]) and not failed
    assert (tmp_path / "organized" / "Photos" / "People" / "IMG_20230101_101010.jpg").exists()
    assert (tmp_path / "organized" / "Documents" / "Invoices").is_dir()

    restored, failed = actions.undo_moves(log)
    assert restored == moved and not failed
    assert all(os.path.exists(i["path"]) for i in move["items"])


def test_rejects_paths_outside_scanned_folders(tmp_path):
    f = tmp_path / "elsewhere.txt"
    f.write_text("x")
    evil = {"tool": "declutter", "list_type": "delete", "scanned_folders": [str(tmp_path / "scanned")],
            "items": [{"path": str(f), "root": str(tmp_path / "scanned"), "size": 1}]}
    plan = actions.plan_deletions(evil)
    assert not plan.ok and plan.skipped[0][1] == "outside the scanned folders"
    with pytest.raises(actions.ListError):
        p = tmp_path / "wrong.json"
        p.write_text(json.dumps(dict(evil, list_type="move")))
        actions.load_list(p, "delete")
