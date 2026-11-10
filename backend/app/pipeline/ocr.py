"""Image preprocessing + Tesseract OCR."""
import io
import cv2
import numpy as np
import pytesseract
from PIL import Image, ImageOps
from ..config import settings
from .errors import CorruptFileError

MAX_SIDE = 4500


def pil_to_gray(img: Image.Image) -> np.ndarray:
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA", "P"):
        bg = Image.new("RGB", img.size, "white")
        rgba = img.convert("RGBA")
        bg.paste(rgba, mask=rgba.split()[3])
        img = bg
    return np.array(img.convert("L"))


def _deskew(gray: np.ndarray) -> np.ndarray:
    inv = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    coords = cv2.findNonZero(inv)
    if coords is None or len(coords) < 200:
        return gray
    angle = cv2.minAreaRect(coords)[-1]
    angle = angle - 90 if angle > 45 else angle
    if abs(angle) < 0.3 or abs(angle) > 15:
        return gray
    h, w = gray.shape
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    return cv2.warpAffine(gray, m, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)


def preprocess(gray: np.ndarray) -> np.ndarray:
    h, w = gray.shape
    if max(h, w) < 2400:                                   # Tesseract works best near ~300 dpi -> upscale (max 2.5x)
        s = min(2400 / max(h, w), 2.5)
        gray = cv2.resize(gray, None, fx=s, fy=s, interpolation=cv2.INTER_CUBIC)
    elif max(h, w) > MAX_SIDE:
        s = MAX_SIDE / max(h, w)
        gray = cv2.resize(gray, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    gray = cv2.fastNlMeansDenoising(gray, None, 10, 7, 21)
    gray = _deskew(gray)
    return cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]


def ocr_array(gray: np.ndarray) -> tuple[str, float]:
    """Preprocess and OCR; returns (text, mean word confidence 0..1)."""
    proc = preprocess(gray)
    cfg = "--psm 6"
    data = pytesseract.image_to_data(proc, lang=settings.ocr_lang, config=cfg,
                                     output_type=pytesseract.Output.DICT, timeout=120)
    lines, confs, cur, key = [], [], [], None
    for i, word in enumerate(data["text"]):
        word = word.strip()
        if not word:
            continue
        k = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        if key is not None and k != key:
            lines.append(" ".join(cur)); cur = []
        key = k
        cur.append(word)
        c = float(data["conf"][i])
        if c >= 0:
            confs.append(c)
    if cur:
        lines.append(" ".join(cur))
    return "\n".join(lines), (float(np.mean(confs)) / 100 if confs else 0.0)


def ocr_png_bytes(png: bytes) -> tuple[str, float]:
    try:
        img = Image.open(io.BytesIO(png))
        return ocr_array(pil_to_gray(img))
    except pytesseract.TesseractError as e:
        raise CorruptFileError(f"OCR failed: {e}")
