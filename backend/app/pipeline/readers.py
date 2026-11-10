"""Turn a stored file into a list of PageText (text per page / sheet)."""
import csv
import io
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Optional

import chardet
import pymupdf as fitz  # PyMuPDF
import pandas as pd
from PIL import Image, ImageSequence, UnidentifiedImageError

from ..config import settings
from .errors import CorruptFileError, UnsupportedFileError
from .ocr import ocr_array, ocr_png_bytes, pil_to_gray

Progress = Callable[[int, int], None]


@dataclass
class PageText:
    page_no: int
    text: str
    method: str                      # text|ocr|sheet
    label: Optional[str] = None
    confidence: Optional[float] = None
    rows: Optional[list] = field(default=None, repr=False)   # raw sheet rows


def _page_text(page: "fitz.Page") -> str:
    """Layout-preserving text: group words into visual lines."""
    words = page.get_text("words")
    if not words:
        return ""
    words.sort(key=lambda w: (round(w[3], 0), w[0]))
    lines, cur, cur_y = [], [], None
    for w in sorted(words, key=lambda w: ((w[1] + w[3]) / 2, w[0])):
        yc = (w[1] + w[3]) / 2
        if cur_y is None or abs(yc - cur_y) <= 3.5:
            cur.append(w)
            cur_y = yc if cur_y is None else (cur_y + yc) / 2
        else:
            lines.append(cur); cur, cur_y = [w], yc
    if cur:
        lines.append(cur)
    return "\n".join(" ".join(x[4] for x in sorted(l, key=lambda x: x[0])) for l in lines)


def read_pdf(path: str, progress: Optional[Progress] = None) -> list[PageText]:
    try:
        doc = fitz.open(path)
    except Exception as e:
        raise CorruptFileError(f"Cannot open PDF: {e}")
    try:
        if doc.needs_pass:
            raise CorruptFileError("PDF is password protected")
        n = doc.page_count
        if n == 0:
            raise CorruptFileError("PDF has no pages")
        if n > settings.max_pdf_pages:
            raise UnsupportedFileError(f"PDF has {n} pages (limit {settings.max_pdf_pages})")
        results: dict[int, PageText] = {}
        to_ocr: list[int] = []
        for i in range(n):                                   # pass 1: digital text
            try:
                t = _page_text(doc[i])
            except Exception:
                t = ""
            if len(t.strip()) >= settings.min_page_chars:
                results[i] = PageText(i + 1, t, "text")
                if progress: progress(len(results), n)
            else:
                to_ocr.append(i)
        done = len(results)
        B = 16                                               # pass 2: OCR in small batches
        with ThreadPoolExecutor(max_workers=4) as pool:
            for s in range(0, len(to_ocr), B):
                batch = to_ocr[s:s + B]
                pngs = []
                for i in batch:
                    try:
                        pix = doc[i].get_pixmap(dpi=settings.ocr_dpi)
                        pngs.append(pix.tobytes("png"))
                    except Exception:
                        pngs.append(None)
                for i, res in zip(batch, pool.map(lambda b: ocr_png_bytes(b) if b else ("", 0.0), pngs)):
                    results[i] = PageText(i + 1, res[0], "ocr", confidence=res[1])
                    done += 1
                    if progress: progress(done, n)
        return [results[i] for i in range(n)]
    finally:
        doc.close()


def read_image(path: str, progress: Optional[Progress] = None) -> list[PageText]:
    try:
        img = Image.open(path)
        img.load()
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as e:
        raise CorruptFileError(f"Cannot decode image: {e}")
    frames = [f.copy() for f in ImageSequence.Iterator(img)]
    out = []
    for i, fr in enumerate(frames):
        text, conf = ocr_array(pil_to_gray(fr))
        out.append(PageText(i + 1, text, "ocr", confidence=conf))
        if progress: progress(i + 1, len(frames))
    return out


def _decode_csv(path: str) -> str:
    raw = open(path, "rb").read()
    if not raw.strip():
        raise CorruptFileError("CSV file is empty")
    for bom, enc in ((b"\xff\xfe", "utf-16"), (b"\xfe\xff", "utf-16"), (b"\xef\xbb\xbf", "utf-8-sig")):
        if raw.startswith(bom):
            return raw.decode(enc)
    guess = chardet.detect(raw[:100000]).get("encoding") or "utf-8"
    for enc in ("utf-8", guess, "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    raise CorruptFileError("Cannot decode CSV")


def _rows_to_text(rows: list[list[str]]) -> str:
    return "\n".join(" | ".join(c for c in r if c != "") for r in rows if any(c != "" for c in r))


def _clean_rows(df: pd.DataFrame) -> list[list[str]]:
    df = df.dropna(how="all").dropna(axis=1, how="all").fillna("")
    return [[str(c).strip() for c in row] for row in df.values.tolist()]


def read_spreadsheet(path: str, filename: str, progress: Optional[Progress] = None) -> list[PageText]:
    ext = os.path.splitext(filename)[1].lower()
    sheets: dict[str, pd.DataFrame]
    try:
        if ext == ".csv":
            text = _decode_csv(path)
            try:
                dialect = csv.Sniffer().sniff(text[:5000], delimiters=",;\t|")
                sep = dialect.delimiter
            except csv.Error:
                sep = ","
            df = pd.read_csv(io.StringIO(text), sep=sep, header=None, dtype=str,
                             keep_default_na=False, engine="python", on_bad_lines="skip")
            sheets = {"csv": df}
        elif ext == ".xlsx":
            sheets = pd.read_excel(path, sheet_name=None, header=None, dtype=str, engine="openpyxl")
        elif ext == ".xls":
            sheets = pd.read_excel(path, sheet_name=None, header=None, dtype=str, engine="xlrd")
        else:
            raise UnsupportedFileError(f"Unsupported spreadsheet type {ext}")
    except (CorruptFileError, UnsupportedFileError):
        raise
    except pd.errors.EmptyDataError:
        raise CorruptFileError("Spreadsheet contains no data")
    except Exception as e:
        raise CorruptFileError(f"Cannot parse spreadsheet: {e}")
    out = []
    for i, (name, df) in enumerate(sheets.items(), 1):
        rows = _clean_rows(df)
        if not rows:
            continue
        out.append(PageText(i, _rows_to_text(rows), "sheet", label=str(name), rows=rows))
        if progress: progress(i, len(sheets))
    if not out:
        raise CorruptFileError("Spreadsheet contains no data")
    return out


def read_any(kind: str, path: str, filename: str, progress: Optional[Progress] = None) -> list[PageText]:
    if kind == "pdf":
        return read_pdf(path, progress)
    if kind == "image":
        return read_image(path, progress)
    if kind == "spreadsheet":
        return read_spreadsheet(path, filename, progress)
    raise UnsupportedFileError("Unsupported file type")
