"""File-type detection by magic bytes (never trust extension/MIME from the client)."""
import os

SPREADSHEET_EXT = {".xlsx", ".xls", ".csv"}


def detect_kind(path: str, filename: str = "") -> tuple[str, str]:
    """Returns (kind, mime). kind in pdf|image|spreadsheet|unknown."""
    with open(path, "rb") as f:
        head = f.read(16)
    ext = os.path.splitext(filename or path)[1].lower()
    if head.startswith(b"%PDF"):
        return "pdf", "application/pdf"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image", "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image", "image/jpeg"
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return "image", "image/tiff"
    if head.startswith(b"PK\x03\x04") and ext == ".xlsx":
        return "spreadsheet", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    if head.startswith(b"\xd0\xcf\x11\xe0") and ext == ".xls":
        return "spreadsheet", "application/vnd.ms-excel"
    if ext == ".csv" and _looks_like_text(path):
        return "spreadsheet", "text/csv"
    return "unknown", "application/octet-stream"


def _looks_like_text(path: str) -> bool:
    with open(path, "rb") as f:
        chunk = f.read(8192)
    if chunk.startswith((b"\xff\xfe", b"\xfe\xff")):   # UTF-16 BOM
        return True
    return b"\x00" not in chunk
