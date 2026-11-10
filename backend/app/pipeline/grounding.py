"""Anti-hallucination: every extracted value must be traceable to the source text, else it is nulled."""
import difflib
import re
from .normalize import ISO_CURRENCIES, SYMBOLS, norm_invoice_no, norm_text, parse_amount, parse_dates
from .extract_rules import AMT


def _numbers(text: str) -> list[float]:
    vals = []
    for m in re.finditer(AMT, text):
        v = parse_amount(m.group(0))
        if v is not None:
            vals.append(float(v))
    return vals


def _name_in(text_norm: str, lines_norm: list[str], name: str) -> bool:
    n = norm_text(name)
    if not n:
        return False
    if n in text_norm:
        return True
    toks = n.split()
    if len(toks) >= 2 and all(t in text_norm.split() for t in toks):
        return True
    return any(difflib.SequenceMatcher(None, n, l).ratio() >= 0.88 for l in lines_norm if abs(len(l) - len(n)) < 12)


def ground(data: dict, conf: dict, text: str, structured: bool = False) -> list[dict]:
    """Mutates data/conf; returns issue dicts for ungrounded fields. Spreadsheet values are trusted (structured)."""
    issues = []
    if structured:
        return issues
    nums = _numbers(text)
    tn = norm_text(text)
    lines_n = [norm_text(l) for l in text.splitlines() if l.strip()]
    inv_text = re.sub(r"[^A-Z0-9]", "", text.upper())

    def drop(f, why):
        data[f] = None
        conf[f] = 0
        issues.append({"code": "UNGROUNDED_FIELD", "severity": "warning", "field": f,
                       "message": f"Extracted {f} not found in source text ({why}); value discarded."})

    v = data.get("invoice_number")
    if v and norm_invoice_no(v) not in inv_text:
        drop("invoice_number", "no match")
    for f in ("subtotal", "tax", "total"):
        v = data.get(f)
        if v is not None:
            try:
                ok = any(abs(n - abs(float(v))) < 0.005 for n in nums)
            except (TypeError, ValueError):
                ok = False
            if not ok:
                drop(f, "no matching number")
    v = data.get("invoice_date")
    if v and v not in {d[0] for d in parse_dates(text)}:
        drop("invoice_date", "no matching date")
    for f in ("supplier_name", "customer_name"):
        v = data.get(f)
        if v and not _name_in(tn, lines_n, v):
            drop(f, "name not in text")
    v = data.get("currency")
    if v:
        sym = [s for s, c in SYMBOLS.items() if c == v]
        if not (re.search(rf"\b{v}\b", text) or any(s in text for s in sym) or (v == "BDT" and re.search(r"\bTk", text))):
            drop("currency", "code/symbol absent")
    kept = []
    for li in data.get("line_items") or []:
        d = li.get("description")
        if d and not _name_in(tn, lines_n, d):
            issues.append({"code": "UNGROUNDED_FIELD", "severity": "warning", "field": "line_items",
                           "message": f"Line item '{d}' not found in text; discarded."})
            continue
        for k in ("quantity", "unit_price", "line_total"):
            x = li.get(k)
            if x is not None and not any(abs(n - abs(float(x))) < 0.005 for n in nums):
                li[k] = None
        kept.append(li)
    data["line_items"] = kept
    return issues
