import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Optional

ISO_CURRENCIES = {"USD", "EUR", "GBP", "BDT", "INR", "JPY", "CNY", "AUD", "CAD", "CHF", "SGD", "AED",
                  "SAR", "MYR", "THB", "KRW", "NZD", "SEK", "NOK", "DKK", "ZAR", "BRL", "MXN", "PKR", "HKD"}
SYMBOLS = {"$": "USD", "€": "EUR", "£": "GBP", "৳": "BDT", "₹": "INR", "¥": "JPY"}
LEGAL = {"inc", "llc", "ltd", "limited", "co", "corp", "corporation", "gmbh", "plc", "pvt", "company", "sa", "ag"}


def norm_text(s) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).strip()


def norm_invoice_no(s) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(s or "").upper())


def norm_supplier(s) -> str:
    return " ".join(w for w in norm_text(s).split() if w not in LEGAL)


def parse_amount(s) -> Optional[Decimal]:
    """Parses '1,234.56', '1.234,56', '(12.00)', '$ 12', '12,50' -> Decimal; None if not numeric."""
    if s is None:
        return None
    if isinstance(s, (int, float, Decimal)):
        return Decimal(str(s))
    t = str(s).strip()
    neg = t.startswith("(") and t.endswith(")") or t.startswith("-") or t.endswith("-")
    t = re.sub(r"[^\d.,]", "", t)
    if not t or not re.search(r"\d", t):
        return None
    if "," in t and "." in t:
        if t.rfind(",") > t.rfind("."):
            t = t.replace(".", "").replace(",", ".")
        else:
            t = t.replace(",", "")
    elif "," in t:
        t = t.replace(",", ".") if re.search(r",\d{1,2}$", t) and t.count(",") == 1 else t.replace(",", "")
    elif t.count(".") > 1:
        t = t.replace(".", "")
    try:
        v = Decimal(t)
    except InvalidOperation:
        return None
    return -v if neg else v


_MONTHS = "jan feb mar apr may jun jul aug sep oct nov dec".split()
DATE_PATTERNS = [
    (re.compile(r"\b(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\b"), "ymd"),
    (re.compile(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})\b"), "dmy_or_mdy"),
    (re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?[\s\-]+([A-Za-z]{3,9})\.?,?[\s\-]+(\d{4})\b"), "d_mon_y"),
    (re.compile(r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b"), "mon_d_y"),
]


def _mon(s):
    s = s[:3].lower()
    return _MONTHS.index(s) + 1 if s in _MONTHS else None


def parse_dates(text: str) -> list[tuple[str, bool, int]]:
    """All dates in text -> (iso, ambiguous, position)."""
    out = []
    for rx, kind in DATE_PATTERNS:
        for m in rx.finditer(text):
            a, b, c = m.groups()
            amb = False
            try:
                if kind == "ymd":
                    y, mo, d = int(a), int(b), int(c)
                elif kind == "dmy_or_mdy":
                    x, z, y = int(a), int(b), int(c)
                    if x > 12: d, mo = x, z
                    elif z > 12: mo, d = x, z
                    else: d, mo, amb = x, z, x != z
                elif kind == "d_mon_y":
                    d, mo, y = int(a), _mon(b), int(c)
                else:
                    mo, d, y = _mon(a), int(b), int(c)
                if mo is None:
                    continue
                out.append((date(y, mo, d).isoformat(), amb, m.start()))
            except ValueError:
                continue
    return sorted(out, key=lambda x: x[2])


def to_iso_date(v) -> Optional[str]:
    if v is None or v == "":
        return None
    if isinstance(v, (date, datetime)):
        return v.date().isoformat() if isinstance(v, datetime) else v.isoformat()
    s = str(v).strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}( 00:00:00)?", s):
        try:
            return date.fromisoformat(s[:10]).isoformat()
        except ValueError:
            return None
    ds = parse_dates(s)
    return ds[0][0] if ds else None


def to_currency(v) -> Optional[str]:
    if not v:
        return None
    s = str(v).strip()
    if s.upper() in ISO_CURRENCIES:
        return s.upper()
    return SYMBOLS.get(s) or ("BDT" if s.lower() in ("tk", "taka") else None)


def money(v) -> Optional[float]:
    d = parse_amount(v)
    return float(round(d, 2)) if d is not None else None
