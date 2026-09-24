# declutter

A free, **fully offline** desktop tool that sorts, de-duplicates and safely cleans up
photos and documents spread across several folders (for example an old phone backup
and a new one).

- Pick any number of folders; they are scanned as **one combined collection**, so
  duplicates *across* folders are found too.
- Photos: near-duplicate grouping, blur / low-quality detection, faces → *People*,
  screenshots, receipts, note/document photos, and a best-effort guess at memes,
  scenery, food and "random".
- Documents (PDF, DOCX, XLSX): text extraction, duplicate detection by **text content**
  (so re-saved copies with different metadata still match), and classification into
  invoices/bills, IDs/certificates, resumes, notes and other.
- One self-contained **HTML report** with thumbnails, duplicate groups and editable
  "suggested for cleanup" checkboxes.
- **Nothing is ever moved or deleted automatically.** You export a move list and/or
  delete list from the report and apply it as a separate, confirmed step.

> **Privacy:** declutter has no cloud APIs, no telemetry and no network code. All
> analysis (perceptual hashing, OpenCV, Tesseract OCR) runs on your computer, and no
> data ever leaves the device. The report is a local file with its thumbnails inline.

---

## Using the app

1. **Add folder…** as many times as you like (the list is remembered). Use
   **Remove selected** to take folders off the list.
2. Press **Scan all folders**. A progress bar shows each stage and you can cancel at any
   time. Scanning skips hidden folders and system/trash folders.
3. The report opens in your browser when it's done. **Open report** re-opens it, and
   **Reports folder** shows every report (`~/Declutter Reports/`).
4. In the report:
   - Photos are shown as a thumbnail grid per category. **Duplicate groups** are drawn
     together inside a dashed orange box, with the best copy labelled *Best copy* (highest
     resolution, then sharpest, then largest). Documents are listed per category, and their
     duplicate groups are highlighted in the same way.
   - Every item has a **Clean up (delete)** checkbox. These are **pre-checked**:
     duplicate copies (all except the best copy), low-quality photos (blurry or tiny),
     and photos put in the low-confidence *random* category. Uncheck anything you want
     to keep. If you mark every copy in a group, the report warns you.
   - Every item has a **Move to** drop-down, so you can fix a wrong category or choose
     *Don't move*.
   - **Export move list** saves `declutter_move_list.json`: every item *not* marked for
     deletion, going into category subfolders such as `Photos/People`,
     `Photos/Screenshots`, `Photos/Receipts`, `Documents/Invoices`, `Documents/Resumes` …
   - **Export delete list** saves `declutter_delete_list.json`: the items you left checked.
5. Apply the lists. Either use **Apply move list…** / **Apply delete list…** in the app
   (you get a summary and must confirm), or use the scripts below.

By default, files are moved into a `Declutter Organized` folder **inside each scanned
folder**, for example `D:/Old phone/Declutter Organized/Photos/People/IMG_0001.jpg`. You
can enter a different destination in the report's "Move destination" box or pass
`--dest` to the script.

## Applying lists with the scripts

Running from source? These scripts do exactly what the in-app buttons do:

```bash
# Moves: show the plan without changing anything, then run it for real
python apply_moves.py ~/Downloads/declutter_move_list.json --dry-run
python apply_moves.py ~/Downloads/declutter_move_list.json            # asks you to type "yes"
python apply_moves.py ~/Downloads/declutter_move_list.json --dest ~/Pictures/Sorted

# Undo a move run (each run writes a log to ~/Declutter Reports/)
python apply_moves.py --undo "~/Declutter Reports/declutter_move_log_20240101_120000.json"

# Deletions
python apply_deletions.py ~/Downloads/declutter_delete_list.json --dry-run
python apply_deletions.py ~/Downloads/declutter_delete_list.json      # asks you to type "delete"
python apply_deletions.py ~/Downloads/declutter_delete_list.json --quarantine ~/declutter_trash
```

Safety checks are applied to every item, both in the scripts and in the app:

- Only a list exported by a declutter report is accepted, and it must be the right kind
  (a move list is never treated as a delete list).
- A file must be inside one of the scanned folders. If it isn't, it is skipped.
- A file must still have the **same size and modification time** it had during the scan.
  Anything that changed since then is skipped.
- Moves never overwrite anything. If a name clashes, the file gets a ` (1)` suffix.
- `--quarantine` (or *Move to quarantine…* in the app) moves "deleted" files into a folder
  instead of deleting them, so you can check them before emptying it.

---

## What to trust: reliability by category

Everything is heuristic. The report shows the reason behind each decision (hover a
thumbnail for details), and the low-confidence categories are labelled as such.

| Reliability | Feature | How it works |
|---|---|---|
| **High** | Exact duplicates | SHA-256 of the file bytes |
| **High** | Near-duplicate photos (resized, re-compressed, different format) | pHash **and** dHash within a small distance, and the same aspect ratio. Crops and edits are deliberately *not* grouped. |
| **High** | Duplicate documents (re-saved, re-exported) | Identical normalised text, or ≥ 85 % word-shingle overlap (simhash pre-filter + Jaccard) |
| **High** | Blurry / tiny photos | Laplacian variance (90th-percentile tile), plus a minimum-size check |
| **Good** | Faces → *People / selfies* | OpenCV Haar cascade, frontal faces only. It misses profiles and tiny faces and can occasionally fire on patterns. |
| **Good** | Screenshots | File name (`Screenshot_…`, `Screen Shot …`), or an exact screen resolution/aspect ratio with no camera EXIF. Camera names such as `IMG_`/`DSC_`/`PXL_` count against it. |
| **Good** | Receipts / note photos | Tesseract text density; bill keywords (total, ₹, $, invoice, GST, date …) make it a receipt |
| **Good** | Document categories | Weighted keyword and file-name rules for invoice, ID/certificate, resume and notes. Anything below the score threshold becomes *Other*. |
| **Low** | Memes | Caption-style text at the top or bottom of an image without camera EXIF |
| **Low** | Scenery vs food | Colour statistics only (sky/greenery vs warm, saturated close-ups). An orange cat can look like food. |
| **Low** | Random | Whatever is left. It's pre-checked for cleanup, so review it before deleting. |

You can tune every threshold in `declutter/config.py`.

Other limits:

- Formats: jpg/jpeg/png/webp photos; pdf/docx/xlsx documents (not .doc, .xls or HEIC).
- Scanned PDFs with no text layer are OCR'd (first two pages), in English only by default.
- Password-protected PDFs are listed with a note but not read.

---

## Running from source

Requires Python 3.10+ with Tkinter, and optionally Tesseract for OCR.

```bash
git clone https://github.com/shuyash411-create/Declutter.git
cd Declutter
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python main.py                                          # the GUI
python main.py --scan ~/Pictures ~/Backup --out report.html   # headless scan
```

Tesseract, only needed from source, because the packaged app bundles it:
- Windows: <https://github.com/UB-Mannheim/tesseract/wiki> or `choco install tesseract`
- macOS: `brew install tesseract`
- Debian/Ubuntu: `sudo apt install tesseract-ocr python3-tk`

Without Tesseract everything still works except receipt, note and meme detection. The
report header shows the OCR status.

Tests (they generate a realistic two-folder dataset and run the whole pipeline):

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

## Building the single-file app

The app is packaged with PyInstaller as **one executable that opens only the GUI
window**, with no terminal or console at any point. Tesseract (binary, libraries and
English data) is bundled inside, so end users install nothing.

One command does everything for the current OS: it stages Tesseract, draws the icon and
runs PyInstaller.

```bash
pip install -r requirements-dev.txt
python packaging/build.py
```

It runs PyInstaller with the required console-free flags:

| OS | PyInstaller flags | Output |
|---|---|---|
| Windows | `pyinstaller --onefile --noconsole main.py` | `dist/declutter.exe` |
| macOS | `pyinstaller --onefile --windowed main.py` | `dist/declutter.app` |
| Linux | `pyinstaller --onefile --windowed main.py` | `dist/declutter` |

…plus `--add-data declutter/assets` (the face model) and
`--add-data build/tesseract_bundle:tesseract` (the bundled OCR).

Per-OS steps:

**Windows**
1. Install Python 3.12 from python.org (includes Tkinter) and Tesseract
   (`choco install tesseract`, or the UB-Mannheim installer, into the default
   `C:\Program Files\Tesseract-OCR`).
2. `pip install -r requirements-dev.txt`
3. `python packaging\build.py` → `dist\declutter.exe`

**macOS**
1. `brew install tesseract`, then Python 3.12 from python.org or `brew install python-tk@3.12`.
2. `pip install -r requirements-dev.txt`
3. `python packaging/build.py` → `dist/declutter.app`. `collect_tesseract.py` copies
   Homebrew's dylibs, rewrites their load paths to `@executable_path/lib`, and ad-hoc
   re-signs them.
4. The app is unsigned, so the first time you open it, right-click → **Open**. To
   distribute it widely, sign and notarise it with your Apple Developer ID.
   PyInstaller 6 prints a deprecation notice for `--onefile` with `.app` bundles. The build
   still works.

**Linux**
1. `sudo apt install tesseract-ocr python3-tk`
2. `pip install -r requirements-dev.txt`
3. `python packaging/build.py` → `dist/declutter`

**CI builds.** `.github/workflows/build.yml` builds Windows, macOS and Linux on every push.
For each OS it runs the tests, builds the app, and smoke-tests the packaged binary with
the system Tesseract out of the way, checking that the *bundled* OCR is used. The results
are uploaded as downloadable artifacts (`declutter-Windows`, `declutter-macOS`,
`declutter-Linux`) on the workflow run page.

## Project layout

```
main.py                   entry point (GUI; `--scan` for headless)
apply_moves.py            apply an exported move list (dry-run, confirm, undo)
apply_deletions.py        apply an exported delete list (dry-run, confirm, quarantine)
declutter/
  gui.py                  Tkinter window
  pipeline.py             scan -> analyse (threaded) -> group -> report
  scanner.py              recursive multi-folder discovery
  photos.py               hashing, blur, faces, screenshots, OCR, soft categories
  documents.py            text extraction, content dedup, classification
  report.py               single-file HTML report + export buttons
  actions.py              safe move/delete/undo logic shared by the app and scripts
  config.py               all thresholds and category folders
  resources.py            finds the bundled Tesseract / face model
packaging/
  build.py                one-command PyInstaller build
  collect_tesseract.py    stages a self-contained Tesseract for bundling
tests/                    fixture generator + end-to-end tests
```
