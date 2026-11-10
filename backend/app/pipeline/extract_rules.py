"""Deterministic baseline extractor (regex/heuristics) + spreadsheet extractor.
Used when no LLM key is configured, as a fallback, and as an evaluation baseline."""
import re
from collections import Counter
from decimal import Decimal
from .normalize import (ISO_CURRENCIES, SYMBOLS, money, norm_text, parse_amount, parse_dates, to_currency,
                        to_iso_date)

AMT = r"-?\(?\d{1,3}(?:[,\s]\d{3})+(?:[.,]\d{1,2})?\)?|-?\(?\d{1,3}(?:\.\d{3})+,\d{1,2}\)?|-?\(?\d+(?:[.,]\d{1,2})?\)?"
INV_RX = re.compile(r"(?i)(?=\b(?:invoice|receipt|bill|inv(?![-\w]))\b[ \t]*(?:no\.?|number|num\.?|#|id|ref)?[ \t]*[:#.\-]?[ \t]*\n?[ \t]*([A-Z0-9][A-Z0-9\-/_.]{2,30}))")
BARE_INV_RX = re.compile(r"(?i)\b(INV[-/_][A-Z0-9\-/_.]*\d[A-Z0-9\-/_.]*)")
EMPTY = {"invoice_number": None, "invoice_date": None, "supplier_name": None, "customer_name": None,
         "subtotal": None, "tax": None, "total": None, "currency": None, "line_items": []}


def find_invoice_number(text: str):
    for m in INV_RX.finditer(text):
        tok = m.group(1).rstrip(".-/")
        if not re.search(r"\d", tok) or re.fullmatch(r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}", tok):
            continue
        if re.fullmatch(r"\d{1,2}[-/.]\d{1,2}[-/.]\d{4}", tok):
            continue
        return tok
    m = BARE_INV_RX.search(text)
    return m.group(1).rstrip(".-/") if m else None


def _amounts(line: str):
    line = re.sub(r"\d+(?:\.\d+)?\s*%", " ", line)
    line = re.sub(r"(?<=\d)[,.]\s?(?=[,.]\d)", "", line)      # OCR noise like '6,.002.08' -> '6.002.08'→ handled below
    vals = []
    for m in re.finditer(AMT, line):
        v = parse_amount(m.group(0))
        if v is not None:
            vals.append(v)
    return vals


def _labelled_amount(lines, label_rx, pick_last_line=True):
    hits = []
    for i, ln in enumerate(lines):
        m = re.search(label_rx, ln)
        if not m:
            continue
        rest = ln[m.end():]
        vals = _amounts(rest)
        looks_header = re.search(r"(?i)description|qty|quantity|unit", ln)
        if not vals and ln.rstrip().endswith(":") and not looks_header and i + 1 < len(lines):
            vals = _amounts(lines[i + 1])[:1]
        if looks_header:
            continue
        if vals:
            hits.append(vals[-1])
    if not hits:
        return None
    return float(round(hits[-1] if pick_last_line else hits[0], 2))


def _value_after(lines, labels):
    rx = re.compile(r"(?i)^\W*(?:" + "|".join(labels) + r")\b\s*[:\-]?\s*(.*)$")
    for i, ln in enumerate(lines):
        m = rx.match(ln)
        if m:
            v = m.group(1).strip()
            if v:
                return v
            for nxt in lines[i + 1:i + 3]:
                if nxt.strip():
                    return nxt.strip()
    return None


def _clean_name(v):
    if not v:
        return None
    v = re.split(r"\s{2,}|\s(?:Invoice|Date|Tel|Phone|Email|Address)\b", v)[0]
    return v.strip(" :,-") or None


def _currency(text: str):
    codes = Counter(c for c in re.findall(r"\b([A-Z]{3})\b", text) if c in ISO_CURRENCIES)
    if codes:
        return codes.most_common(1)[0][0], 0.9
    for sym, code in SYMBOLS.items():
        if sym in text:
            return code, 0.6
    if re.search(r"\bTk\.?\b", text):
        return "BDT", 0.6
    return None, 0.0


def _line_items(lines):
    start = None
    for i, ln in enumerate(lines):
        l = ln.lower()
        if "description" in l and re.search(r"qty|quantity|price|amount|total", l):
            start = i + 1
            break
    items = []
    rx = re.compile(rf"^(?P<d>[A-Za-z].*?)\s+(?P<q>\d+(?:[.,]\d+)?)\s*(?:x\s*)?[$€£৳¥₹]?\s*(?P<u>{AMT})\s+[$€£৳¥₹]?\s*(?P<t>{AMT})$")
    stop = re.compile(r"(?i)^\W*(sub\s*-?total|total|tax|vat|gst|amount due|balance|grand)")
    for ln in lines[start or 0:]:
        if stop.match(ln):
            if start is not None or items:
                break
            continue
        ln2 = ln.strip()
        m = rx.match(ln2.replace("$", "").replace("€", "").replace("£", ""))
        if m:
            q, u, t = parse_amount(m["q"]), parse_amount(m["u"]), parse_amount(m["t"])
            if start is None:   # no table header: only accept lines that look like items and are arithmetically sane
                if ":" in m["d"] or re.search(r"(?i)\b(invoice|date|tax|total|currency|receipt|bill|page)\b", m["d"]):
                    continue
                if abs(q * u - t) > max(Decimal("0.02"), abs(t) * Decimal("0.01")):
                    continue
            items.append({"description": m["d"].strip(), "quantity": float(q), "unit_price": float(u),
                          "line_total": float(t)})
    return items


def extract_from_text(text: str) -> tuple[dict, dict]:
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    d = {**EMPTY, "line_items": []}
    conf = {}
    d["invoice_number"] = find_invoice_number(text)
    conf["invoice_number"] = 0.85 if d["invoice_number"] else 0
    # date: prefer one on a line with an invoice-date label
    iso, c = None, 0.0
    for ln in lines:
        if re.search(r"(?i)invoice date|date of issue|issued|date\b", ln) and not re.search(r"(?i)due|delivery", ln):
            ds = parse_dates(ln)
            if ds:
                iso, c = ds[0][0], 0.5 if ds[0][1] else 0.85
                break
    if not iso:
        ds = parse_dates(text)
        if ds:
            iso, c = ds[0][0], 0.4
    d["invoice_date"], conf["invoice_date"] = iso, c
    sup = _clean_name(_value_after(lines, ["from", "supplier", "vendor", "seller", "sold by", "issued by", "billed by"]))
    c = 0.8
    if not sup:
        for ln in lines[:6]:
            if re.search(r"[A-Za-z]{3}", ln) and not re.search(r"(?i)invoice|receipt|tax|date|page|bill", ln):
                sup = _clean_name(ln)
                # first-line guess: more trustworthy when it looks like a company name
                c = 0.65 if re.search(r"(?i)\b(inc|llc|ltd|plc|gmbh|corp|co|group|and sons|& sons)\b|-", ln) else 0.45
                break
    d["supplier_name"], conf["supplier_name"] = sup, (c if sup else 0)
    cus = _clean_name(_value_after(lines, ["bill to", "billed to", "customer", "client", "sold to", "buyer"]))
    d["customer_name"], conf["customer_name"] = cus, 0.8 if cus else 0
    d["subtotal"] = _labelled_amount(lines, r"(?i)sub\s*-?\s*total")
    d["tax"] = _labelled_amount(lines, r"(?i)\b(?:sales\s+tax|vat|gst|tax)\b")
    d["total"] = _labelled_amount(lines, r"(?i)(?:grand\s+total|total\s+due|amount\s+due|balance\s+due|total\s+amount|(?<!sub)(?<!sub )(?<!sub-)\btotal)\b")
    for k in ("subtotal", "tax", "total"):
        conf[k] = 0.8 if d[k] is not None else 0
    d["currency"], conf["currency"] = _currency(text)
    d["line_items"] = _line_items(lines)
    conf["line_items"] = 0.7 if d["line_items"] else 0
    return d, conf


# ---------------------------- spreadsheets ----------------------------
COLS = {
    "invoice_number": ["invoice number", "invoice no", "invoice #", "invoice id", "inv no", "inv #", "invoice"],
    "invoice_date": ["invoice date", "date"],
    "supplier_name": ["supplier name", "supplier", "vendor", "seller"],
    "customer_name": ["customer name", "customer", "client", "bill to", "buyer"],
    "subtotal": ["subtotal", "sub total", "net amount", "net"],
    "tax": ["tax amount", "tax", "vat", "gst"],
    "total": ["grand total", "total amount", "invoice total", "total"],
    "currency": ["currency", "ccy"],
    "description": ["description", "item", "item description", "product"],
    "quantity": ["quantity", "qty"],
    "unit_price": ["unit price", "price", "rate"],
    "line_total": ["line total", "line amount", "extended"],
}


def _map_header(row):
    mapping = {}
    for j, cell in enumerate(row):
        c = norm_text(cell)
        for field, names in COLS.items():
            if c in [norm_text(n) for n in names] and field not in mapping:
                mapping[field] = j
                break
    return mapping


def extract_from_sheet(rows: list[list[str]]) -> list[tuple[dict, dict]]:
    """Header-based table parsing. Groups rows by invoice number; returns [] if no table recognised."""
    hdr_i, mapping = None, {}
    for i, r in enumerate(rows[:25]):
        m = _map_header(r)
        if len(m) >= 3 and ("invoice_number" in m or "total" in m):
            hdr_i, mapping = i, m
            break
    if hdr_i is None:
        return []
    groups: dict[str, list] = {}
    order = []
    for r in rows[hdr_i + 1:]:
        def g(f, r=r):
            j = mapping.get(f)
            return r[j].strip() if j is not None and j < len(r) and r[j].strip() != "" else None
        key = g("invoice_number") or f"__row{len(order)}"
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(g)
    out = []
    for key in order:
        gs = groups[key]
        first = lambda f: next((x for x in (g(f) for g in gs) if x is not None), None)
        d = {**EMPTY, "line_items": []}
        d["invoice_number"] = None if key.startswith("__row") else key
        d["invoice_date"] = to_iso_date(first("invoice_date"))
        d["supplier_name"] = first("supplier_name")
        d["customer_name"] = first("customer_name")
        for k in ("subtotal", "tax", "total"):
            d[k] = money(first(k))
        d["currency"] = to_currency(first("currency"))
        if "description" in mapping:
            for g in gs:
                if g("description") is None:
                    continue
                d["line_items"].append({"description": g("description"), "quantity": money(g("quantity")),
                                        "unit_price": money(g("unit_price")), "line_total": money(g("line_total"))})
        conf = {k: (0.9 if d[k] not in (None, []) else 0) for k in d}
        out.append((d, conf))
    return out
