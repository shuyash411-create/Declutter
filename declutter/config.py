"""Central place for file types, thresholds and category definitions.

Every heuristic threshold lives here so it can be tuned without touching the
analysis code.
"""

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
DOC_EXTS = {".pdf", ".docx", ".xlsx"}

# Directories never descended into while scanning.
SKIP_DIR_NAMES = {
    "$recycle.bin", "system volume information", "__pycache__", "node_modules",
    ".git", ".svn", ".trash", ".trashes", ".spotlight-v100", ".fseventsd",
    "declutter organized",  # our own output folder (see actions.DEFAULT_OUTPUT_DIRNAME)
}

# ---------------------------------------------------------------- photos ----
ANALYSIS_MAX_SIDE = 1024      # images are downscaled to this before analysis
THUMB_MAX_SIDE = 220          # thumbnail size embedded in the report
THUMB_QUALITY = 70

# Perceptual duplicate grouping: two images are near-duplicates when BOTH the
# pHash and the dHash hamming distances are within these limits (64-bit hashes).
PHASH_MAX_DISTANCE = 8
DHASH_MAX_DISTANCE = 12
DUP_ASPECT_TOLERANCE = 0.03   # near-duplicates must also share the aspect ratio

# Sharpness: variance of the Laplacian, computed per tile on a 1024px image;
# the 90th percentile tile is used so a sharp subject on a plain background
# is not flagged. Below this value the photo is "low quality (blurry)".
BLUR_THRESHOLD = 60.0
TINY_IMAGE_SIDE = 120         # min(width, height) at or below this -> low quality

# Face detection (Haar cascade)
FACE_MIN_SIZE_FRACTION = 0.06  # smallest face = 6% of the shorter image side
FACE_MIN_NEIGHBORS = 6

# OCR / text density
OCR_MAX_SIDE = 1600
OCR_MIN_CONFIDENCE = 55        # tesseract word confidence (0-100)
TEXT_HEAVY_MIN_WORDS = 25      # "high text density" = many words ...
TEXT_HEAVY_MIN_AREA = 0.06     # ... or text boxes covering >= 6% of the image
RECEIPT_KEYWORDS = [
    "total", "subtotal", "sub total", "invoice", "receipt", "bill", "amount",
    "tax", "gst", "vat", "cgst", "sgst", "cash", "change", "qty", "price",
    "date", "paid", "payment", "card", "balance", "due", "discount",
    "₹", "$", "€", "£", "rs.", "inr", "usd",
]
RECEIPT_MIN_KEYWORD_HITS = 2

# Common screenshot aspect ratios (long side / short side) and exact screen
# resolutions. Photos from cameras almost always are 4:3, 3:2 or 16:9 *with*
# camera EXIF data, which screenshots lack.
SCREENSHOT_PHONE_RATIOS = [19.5 / 9, 20 / 9, 19 / 9, 18 / 9, 18.5 / 9, 21 / 9, 16 / 9]
SCREENSHOT_OTHER_RATIOS = [16 / 9, 16 / 10, 3 / 2, 4 / 3]
SCREENSHOT_RATIO_TOLERANCE = 0.015
COMMON_SCREEN_WIDTHS = {
    640, 720, 750, 768, 800, 828, 1080, 1125, 1170, 1179, 1242, 1284, 1290, 1440,
    1536, 1600, 1668, 1920, 2048, 2160, 2560, 2732, 2880, 3024, 3840, 1366, 1280,
    1680, 2388, 1640, 1620, 1206, 1320,
}
SCREENSHOT_NAME_PATTERNS = [
    r"screen[\s_\-]?shot", r"^scr_", r"^screen_", r"^capture", r"snip",
    r"^screenrecord", r"^ss_", r"skärmbild", r"bildschirmfoto", r"captura",
    r"^Скриншот", r"^スクリーンショット",
]
CAMERA_NAME_PATTERNS = [r"^img_\d", r"^dsc[_n]?\d", r"^pxl_\d", r"^dcim", r"^p\d{7}", r"^\d{8}_\d{6}", r"^photo_"]

# ------------------------------------------------------------- documents ----
DOC_MAX_PDF_PAGES = 40         # text extraction limit per PDF
DOC_MAX_XLSX_CELLS = 20000
DOC_MAX_CHARS = 200_000
DOC_SIMHASH_MAX_DISTANCE = 12  # candidate near-duplicate text (then Jaccard-confirmed)
DOC_JACCARD_MIN = 0.85         # confirmed near-duplicate (word 5-shingles)
DOC_MIN_WORDS_FOR_TEXT_DUP = 8  # fewer words -> fall back to byte hash only
DOC_OCR_SCANNED_PAGES = 2       # OCR embedded images of image-only PDFs

# ------------------------------------------------------------ categories ----
# key -> (report label, destination folder for "Export move list")
PHOTO_CATEGORIES = {
    "people":     ("People / selfies", "Photos/People"),
    "screenshot": ("Screenshots", "Photos/Screenshots"),
    "receipt":    ("Receipts", "Photos/Receipts"),
    "note":       ("Notes & document photos", "Photos/Notes"),
    "meme":       ("Memes (low confidence)", "Photos/Memes"),
    "scenery":    ("Scenery (low confidence)", "Photos/Scenery"),
    "food":       ("Food (low confidence)", "Photos/Food"),
    "random":     ("Random / uncategorized (low confidence)", "Photos/Random"),
}
DOC_CATEGORIES = {
    "invoice":     ("Invoices & bills", "Documents/Invoices"),
    "id":          ("IDs & certificates", "Documents/IDs and Certificates"),
    "resume":      ("Resumes / CVs", "Documents/Resumes"),
    "notes":       ("Notes", "Documents/Notes"),
    "other":       ("Other documents", "Documents/Other"),
}
LOW_CONFIDENCE_PHOTO_CATEGORIES = {"meme", "scenery", "food", "random"}
