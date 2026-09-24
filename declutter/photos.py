"""Per-image analysis and cross-folder near-duplicate grouping.

Everything here is heuristic and runs locally: perceptual hashes (imagehash),
Laplacian-variance sharpness and Haar-cascade faces (OpenCV), Tesseract OCR
for text density, and colour statistics for the low-confidence categories.
"""

import base64
import hashlib
import io
import os
import re
import threading

import cv2
import imagehash
import numpy as np
from PIL import Image, ImageOps

from . import config as C
from .grouping import BKTree, UnionFind, hamming
from .resources import asset_path, configure_tesseract

try:  # optional: HEIF/AVIF etc. are not in scope, but webp needs no plugin
    Image.MAX_IMAGE_PIXELS = 300_000_000
except Exception:
    pass

# Photos are analysed in parallel threads; let each OpenCV call stay single
# threaded instead of oversubscribing the CPU with nested thread pools.
cv2.setNumThreads(1)

_tls = threading.local()


def _face_cascade():
    c = getattr(_tls, "cascade", None)
    if c is None:
        c = cv2.CascadeClassifier(str(asset_path("haarcascade_frontalface_default.xml")))
        if c.empty():
            c = None
        _tls.cascade = c
    return c


def _sha256(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def _resize_max(img: Image.Image, max_side: int) -> Image.Image:
    w, h = img.size
    s = max(w, h)
    if s <= max_side:
        return img
    r = max_side / s
    return img.resize((max(1, int(w * r)), max(1, int(h * r))), Image.LANCZOS)


def _thumb_b64(img: Image.Image) -> str:
    t = img.copy()
    t.thumbnail((C.THUMB_MAX_SIDE, C.THUMB_MAX_SIDE), Image.LANCZOS)
    buf = io.BytesIO()
    t.save(buf, "JPEG", quality=C.THUMB_QUALITY, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def _camera_exif(img: Image.Image):
    """(has_camera_exif, 'Make Model') - screenshots and downloads lack these."""
    try:
        exif = img.getexif()
    except Exception:
        return False, ""
    make = str(exif.get(271, "") or "").strip("\x00 ").strip()
    model = str(exif.get(272, "") or "").strip("\x00 ").strip()
    return bool(make or model), (make + " " + model).strip()


# ------------------------------------------------------------- sharpness ----
def sharpness(gray: np.ndarray) -> float:
    """90th-percentile Laplacian variance over a 4x4 tile grid."""
    lap = cv2.Laplacian(gray, cv2.CV_64F)
    h, w = gray.shape
    vals = []
    for i in range(4):
        for j in range(4):
            tile = lap[i * h // 4:(i + 1) * h // 4, j * w // 4:(j + 1) * w // 4]
            if tile.size:
                vals.append(float(tile.var()))
    return float(np.percentile(vals, 90)) if vals else 0.0


# ----------------------------------------------------------------- faces ----
def detect_faces(gray: np.ndarray) -> int:
    cascade = _face_cascade()
    if cascade is None:
        return 0
    eq = cv2.equalizeHist(gray)
    m = max(24, int(min(gray.shape) * C.FACE_MIN_SIZE_FRACTION))
    faces = cascade.detectMultiScale(
        eq, scaleFactor=1.1, minNeighbors=C.FACE_MIN_NEIGHBORS, minSize=(m, m)
    )
    return 0 if faces is None else len(faces)


# ------------------------------------------------------------ screenshot ----
_SS_RE = [re.compile(p, re.I) for p in C.SCREENSHOT_NAME_PATTERNS]
_CAM_RE = [re.compile(p, re.I) for p in C.CAMERA_NAME_PATTERNS]


def _ratio_match(w, h, ratios):
    r = max(w, h) / max(1, min(w, h))
    return any(abs(r - x) / x <= C.SCREENSHOT_RATIO_TOLERANCE for x in ratios)


def screenshot_score(name, ext, w, h, has_cam_exif):
    """Returns (confidence, reason) - 0 when it does not look like a screenshot."""
    stem = os.path.splitext(name)[0]
    if any(r.search(stem) for r in _SS_RE):
        return 0.9, "file name looks like a screenshot"
    if has_cam_exif or any(r.search(stem) for r in _CAM_RE):
        return 0.0, ""
    short, long_ = min(w, h), max(w, h)
    widths = C.COMMON_SCREEN_WIDTHS
    phone_ratio = _ratio_match(w, h, C.SCREENSHOT_PHONE_RATIOS)
    other_ratio = _ratio_match(w, h, C.SCREENSHOT_OTHER_RATIOS)
    # phones: tall ratio + known short side; tablets/desktops: both sides known
    exact = (phone_ratio and short in widths) or (other_ratio and short in widths and long_ in widths)
    if ext == ".png":
        if exact:
            return 0.75, "screen-sized PNG without camera data"
        if phone_ratio:
            return 0.55, "phone-screen aspect ratio PNG, no camera data"
        return 0.0, ""
    if exact:
        return 0.6, "exact screen resolution, no camera data"
    return 0.0, ""


# ------------------------------------------------------------------- OCR ----
def text_likelihood(gray: np.ndarray) -> int:
    """Cheap count of text-line-like blobs, used to skip OCR on text-free photos."""
    g = gray
    if max(g.shape) > 900:
        r = 900 / max(g.shape)
        g = cv2.resize(g, (int(g.shape[1] * r), int(g.shape[0] * r)), interpolation=cv2.INTER_AREA)
    grad = cv2.morphologyEx(g, cv2.MORPH_GRADIENT, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))
    _, bw = cv2.threshold(grad, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    closed = cv2.morphologyEx(bw, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (9, 1)))
    contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    n = 0
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        if 6 <= h <= max(80, g.shape[0] * 0.2) and w > 2.2 * h:
            fill = cv2.countNonZero(bw[y:y + h, x:x + w]) / float(w * h)
            if fill > 0.25:
                n += 1
    return n


def run_ocr(img: Image.Image):
    """OCR summary dict, or None if Tesseract is unavailable."""
    cmd, _ = configure_tesseract()
    if not cmd:
        return None
    import pytesseract

    g = _resize_max(img.convert("L"), C.OCR_MAX_SIDE)
    W, H = g.size
    try:
        d = pytesseract.image_to_data(g, output_type=pytesseract.Output.DICT, config="--psm 3", timeout=60)
    except Exception:
        return None
    words, all_text, area = [], [], 0
    top = bottom = 0
    heights = []
    for i, txt in enumerate(d.get("text", [])):
        t = (txt or "").strip()
        if not t:
            continue
        try:
            conf = float(d["conf"][i])
        except (TypeError, ValueError):
            conf = -1
        if conf >= 30:
            all_text.append(t)
        if conf < C.OCR_MIN_CONFIDENCE or not any(ch.isalnum() for ch in t):
            continue
        x, y, w, h = d["left"][i], d["top"][i], d["width"][i], d["height"][i]
        words.append(t)
        area += w * h
        heights.append(h / H)
        cy = (y + h / 2) / H
        if cy < 0.25:
            top += 1
        elif cy > 0.75:
            bottom += 1
    text = " ".join(all_text)
    return {
        "words": len(words),
        "area": area / float(W * H),
        "top_frac": top / len(words) if words else 0.0,
        "bottom_frac": bottom / len(words) if words else 0.0,
        "mean_word_height": float(np.mean(heights)) if heights else 0.0,
        "text": text[:4000],
    }


def caption_ocr(img: Image.Image) -> int:
    """Count words of meme-style captions (white, outlined text over a photo)
    in the top and bottom quarter. Normal OCR misses these, so the bright
    pixels are isolated and read as black-on-white text."""
    cmd, _ = configure_tesseract()
    if not cmd:
        return 0
    import pytesseract

    a = np.asarray(_resize_max(img.convert("L"), 1000))
    H = a.shape[0]
    total = 0
    for band in (a[: H // 4], a[3 * H // 4:]):
        bright = float((band > 215).mean())
        if not 0.02 <= bright <= 0.45:
            continue  # no caption-like amount of white pixels
        mask = cv2.medianBlur(np.where(band > 215, 0, 255).astype(np.uint8), 3)
        try:
            d = pytesseract.image_to_data(Image.fromarray(mask), output_type=pytesseract.Output.DICT,
                                          config="--psm 6", timeout=30)
        except Exception:
            continue
        for t, c in zip(d.get("text", []), d.get("conf", [])):
            t = (t or "").strip()
            try:
                c = float(c)
            except (TypeError, ValueError):
                c = -1
            if c >= 50 and len(t) >= 2 and sum(ch.isalpha() for ch in t) >= 2:
                total += 1
    return total


def receipt_keyword_hits(text: str) -> int:
    low = text.lower()
    hits = 0
    for k in C.RECEIPT_KEYWORDS:
        if k.isalpha():
            if re.search(r"\b" + re.escape(k) + r"\b", low):
                hits += 1
        elif k in low:
            hits += 1
    # prices like 12.50 / 1,299.00 are a strong receipt signal
    if len(re.findall(r"\d+[.,]\d{2}\b", low)) >= 3:
        hits += 1
    return hits


# ---------------------------------------------------------- colour stats ----
def colour_stats(rgb_small: np.ndarray):
    hsv = cv2.cvtColor(rgb_small, cv2.COLOR_RGB2HSV)
    Hh, S, V = hsv[..., 0].astype(int), hsv[..., 1].astype(int), hsv[..., 2].astype(int)
    h, w = Hh.shape
    top = slice(0, max(1, h // 3))

    def frac(mask):
        return float(mask.mean()) if mask.size else 0.0

    blue_sky = (Hh >= 90) & (Hh <= 130) & (S > 35) & (V > 110)
    overcast = (S < 25) & (V > 200)
    green = (Hh >= 33) & (Hh <= 88) & (S > 50) & (V > 40)
    warm = ((Hh <= 22) | (Hh >= 165)) & (S > 90) & (V > 60)
    brown = (Hh >= 8) & (Hh <= 25) & (S > 60) & (V > 40) & (V < 210)
    cy0, cy1, cx0, cx1 = h // 4, 3 * h // 4, w // 4, 3 * w // 4
    return {
        "sky_top": frac(blue_sky[top]) + 0.5 * frac(overcast[top]),
        "green": frac(green),
        "warm_center": frac((warm | brown)[cy0:cy1, cx0:cx1]),
        "warm": frac(warm | brown),
        "sat_mean": float(S.mean()) / 255.0,
        "blue_green": frac(blue_sky | green),
    }


def guess_soft_category(w, h, cs, ocr, has_cam_exif, caption_words=0):
    """Best-effort meme / scenery / food / random. Returns (cat, conf, reason)."""
    if caption_words >= 2 and not has_cam_exif:
        return "meme", 0.45, f"caption text over the image ({caption_words} words)"
    # meme: a few words of big text hugging the top and/or bottom edge
    if ocr and 2 <= ocr["words"] <= 60:
        edge = ocr["top_frac"] + ocr["bottom_frac"]
        big = ocr["mean_word_height"] >= 0.035
        score = 0.0
        if edge >= 0.6:
            score += 0.45
        if big:
            score += 0.25
        if not has_cam_exif:
            score += 0.15
        r = max(w, h) / max(1, min(w, h))
        if r < 1.1 or r > 2.3 or max(w, h) <= 1280:
            score += 0.1
        if score >= 0.7:
            return "meme", 0.45, "caption-like text near the top/bottom edge"

    scenery = 0.0
    if cs["sky_top"] > 0.25:
        scenery += 0.5
    if cs["green"] > 0.3:
        scenery += 0.35
    if cs["blue_green"] > 0.45:
        scenery += 0.2
    if w > h:
        scenery += 0.1

    food = 0.0
    if cs["warm_center"] > 0.35:
        food += 0.45
    if cs["warm"] > 0.3:
        food += 0.2
    if cs["sat_mean"] > 0.35:
        food += 0.15
    if cs["sky_top"] < 0.1 and cs["green"] < 0.15:
        food += 0.15

    if scenery >= 0.6 and scenery >= food:
        return "scenery", 0.35, "lots of sky / greenery"
    if food >= 0.7:
        return "food", 0.3, "warm, saturated close-up colours"
    return "random", 0.2, "no strong signal"


# ------------------------------------------------------------ main entry ----
def analyze_image(ff, use_ocr=True):
    """Analyse one image file. ff is a scanner.FoundFile. Returns a dict."""
    rec = {
        "path": ff.path, "root": ff.root, "size": ff.size, "mtime": ff.mtime,
        "name": os.path.basename(ff.path), "kind": "image", "error": None,
    }
    try:
        rec["sha256"] = _sha256(ff.path)
        with Image.open(ff.path) as im:
            full_w, full_h = im.size
            has_cam, camera = _camera_exif(im)
            try:
                orientation = im.getexif().get(0x0112, 1)
            except Exception:
                orientation = 1
            if im.format == "JPEG":
                # decode big camera JPEGs at 1/2..1/8 scale: far faster, and every
                # analysis step works on <= 1600px anyway
                im.draft("RGB", (1600, 1600))
            im.load()
            im = ImageOps.exif_transpose(im)
            if orientation in (5, 6, 7, 8):
                full_w, full_h = full_h, full_w
            if im.mode not in ("RGB", "L"):
                bg = Image.new("RGB", im.size, (255, 255, 255))
                rgba = im.convert("RGBA")
                bg.paste(rgba, mask=rgba.split()[-1])
                im = bg
            else:
                im = im.convert("RGB")
        w, h = full_w, full_h  # real resolution, even if decoded smaller
        rec.update(width=w, height=h, camera=camera, has_camera_exif=has_cam)
        rec["thumb"] = _thumb_b64(im)
        rec["phash"] = int(str(imagehash.phash(im)), 16)
        rec["dhash"] = int(str(imagehash.dhash(im)), 16)

        small = _resize_max(im, C.ANALYSIS_MAX_SIDE)
        rgb = np.asarray(small)
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        rec["sharpness"] = round(sharpness(gray), 1)
        faces = detect_faces(gray)
        rec["faces"] = faces

        ext = os.path.splitext(ff.path)[1].lower()
        ss_conf, ss_reason = screenshot_score(rec["name"], ext, w, h, has_cam)

        ocr = None
        rec["ocr"] = "skipped"
        if use_ocr and ss_conf < 0.55:
            if text_likelihood(gray) >= 3:
                ocr = run_ocr(im)
                rec["ocr"] = "ok" if ocr is not None else "unavailable"
            else:
                rec["ocr"] = "no text detected"
        rec["ocr_words"] = ocr["words"] if ocr else 0

        text_heavy = bool(ocr) and (
            ocr["words"] >= C.TEXT_HEAVY_MIN_WORDS or
            (ocr["area"] >= C.TEXT_HEAVY_MIN_AREA and ocr["words"] >= 8)
        )
        kw = receipt_keyword_hits(ocr["text"]) if ocr else 0

        if ss_conf >= 0.55:
            cat, conf, why = "screenshot", ss_conf, ss_reason
        elif text_heavy and kw >= C.RECEIPT_MIN_KEYWORD_HITS:
            cat, conf, why = "receipt", 0.75, f"dense text with {kw} bill keyword(s)"
        elif text_heavy:
            cat, conf, why = "note", 0.7, f"dense text ({ocr['words']} words)"
        else:
            cs = colour_stats(np.asarray(_resize_max(im, 160)))
            caption = caption_ocr(im) if use_ocr and not has_cam else 0
            soft = guess_soft_category(w, h, cs, ocr, has_cam, caption)
            if soft[0] == "meme":
                cat, conf, why = soft
            elif faces:
                cat, conf, why = "people", 0.8, f"{faces} face(s) detected"
            else:
                cat, conf, why = soft
        rec.update(category=cat, confidence=conf, reason=why)

        lowq = []
        if min(w, h) <= C.TINY_IMAGE_SIDE:
            lowq.append(f"very small ({w}×{h})")
        if cat not in ("screenshot", "note", "receipt") and rec["sharpness"] < C.BLUR_THRESHOLD:
            lowq.append(f"blurry (sharpness {rec['sharpness']:.0f})")
        rec["low_quality"] = lowq
    except Exception as e:  # unreadable / corrupt image
        rec.update(error=f"{type(e).__name__}: {e}", category="random", confidence=0.0,
                   reason="could not be read", low_quality=["unreadable file"], thumb=None,
                   width=0, height=0, sharpness=0.0, faces=0, ocr="skipped", ocr_words=0)
    return rec


def _same_aspect(a, b):
    """Hashes ignore aspect ratio, so a stretched/cropped edit (e.g. a meme made
    from a photo) could match its source; require the same shape too."""
    ra = a["width"] / max(1, a["height"])
    rb = b["width"] / max(1, b["height"])
    return abs(ra - rb) / max(ra, rb) <= C.DUP_ASPECT_TOLERANCE


def _keeper_key(r):
    # best copy: highest resolution, sharpest, largest file, then oldest.
    return (r.get("width", 0) * r.get("height", 0), r.get("sharpness", 0.0), r["size"], -r["mtime"])


def group_duplicates(records):
    """Assign dup_group / is_keeper on image records (list of dicts, in place).

    Exact byte duplicates and perceptual near-duplicates (pHash AND dHash
    within threshold) are merged into connected groups across ALL folders.
    Returns the number of groups.
    """
    idx = [i for i, r in enumerate(records) if not r.get("error")]
    uf = UnionFind(len(records))
    by_sha = {}
    for i in idx:
        by_sha.setdefault(records[i]["sha256"], []).append(i)
    for members in by_sha.values():
        for m in members[1:]:
            uf.union(members[0], m)

    tree = BKTree()
    for i in idx:
        tree.add(records[i]["phash"], i)
    for i in idx:
        for j in tree.query(records[i]["phash"], C.PHASH_MAX_DISTANCE):
            if (j > i and hamming(records[i]["dhash"], records[j]["dhash"]) <= C.DHASH_MAX_DISTANCE
                    and _same_aspect(records[i], records[j])):
                uf.union(i, j)

    n = 0
    for r in records:
        r["dup_group"] = None
        r["is_keeper"] = False
    for g in sorted(uf.groups(), key=lambda g: min(g)):
        n += 1
        members = [records[i] for i in g]
        keeper = max(members, key=_keeper_key)
        exact = len({m["sha256"] for m in members}) == 1
        for m in members:
            m["dup_group"] = n
            m["dup_exact"] = exact
            m["is_keeper"] = m is keeper
        # the whole group is shown in the keeper's category
        for m in members:
            if m is not keeper:
                m["category_detected"] = m["category"]
                m["category"] = keeper["category"]
    return n
