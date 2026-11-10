"""Validation rules. Pure function `validate_data` + DB-aware `validate_invoice` (duplicates / cross-source)."""
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy.orm import Session

from ..config import settings
from ..models import Document, Invoice, ValidationIssue
from .normalize import ISO_CURRENCIES, norm_invoice_no, norm_supplier

REQUIRED_ERR = ["invoice_number", "invoice_date", "supplier_name", "total"]
REQUIRED_WARN = ["customer_name", "currency", "subtotal", "tax"]


def _d(x) -> Optional[Decimal]:
    try:
        return None if x is None or x == "" else Decimal(str(x))
    except Exception:
        return None


def issue(code, severity, field, msg):
    return {"code": code, "severity": severity, "field": field, "message": msg}


def validate_data(data: dict, conf: Optional[dict] = None) -> list[dict]:
    tol = Decimal(str(settings.tolerance))
    out = []
    for f in REQUIRED_ERR:
        if data.get(f) in (None, ""):
            out.append(issue("MISSING_FIELD", "error", f, f"Required field '{f}' is missing."))
    for f in REQUIRED_WARN:
        if data.get(f) in (None, ""):
            out.append(issue("MISSING_FIELD", "warning", f, f"Field '{f}' is missing."))
    if not data.get("line_items"):
        out.append(issue("MISSING_FIELD", "warning", "line_items", "No line items extracted."))

    nums = {}
    for f in ("subtotal", "tax", "total"):
        raw = data.get(f)
        v = _d(raw)
        if raw not in (None, "") and v is None:
            out.append(issue("INVALID_NUMBER", "error", f, f"'{raw}' is not a valid number."))
        elif v is not None:
            if v < 0:
                out.append(issue("INVALID_NUMBER", "warning" if f == "tax" else "error", f, f"{f} is negative ({v})."))
            if abs(v) > Decimal("100000000"):
                out.append(issue("INVALID_NUMBER", "warning", f, f"{f} magnitude is implausible ({v})."))
            nums[f] = v
    if data.get("total") not in (None, "") and nums.get("total") == 0:
        out.append(issue("INVALID_NUMBER", "warning", "total", "Total is zero."))

    cur = data.get("currency")
    if cur and str(cur).upper() not in ISO_CURRENCIES:
        out.append(issue("INVALID_CURRENCY", "warning", "currency", f"Unrecognised currency '{cur}'."))
    dt = data.get("invoice_date")
    if dt:
        try:
            dd = date.fromisoformat(dt)
            if dd.year < 1990:
                out.append(issue("INVALID_DATE", "error", "invoice_date", f"Date {dt} is implausibly old."))
            elif dd > date.today() + timedelta(days=1):
                out.append(issue("INVALID_DATE", "warning", "invoice_date", f"Date {dt} is in the future."))
        except ValueError:
            out.append(issue("INVALID_DATE", "error", "invoice_date", f"'{dt}' is not a valid ISO date."))

    items = data.get("line_items") or []
    line_sum = Decimal(0)
    all_have_totals = bool(items)
    for i, li in enumerate(items, 1):
        q, u, t = _d(li.get("quantity")), _d(li.get("unit_price")), _d(li.get("line_total"))
        for name, val in (("quantity", q), ("unit_price", u), ("line_total", t)):
            if li.get(name) not in (None, "") and val is None:
                out.append(issue("INVALID_NUMBER", "error", "line_items", f"Line {i}: {name} is not numeric."))
        if q is not None and u is not None and t is not None and abs(q * u - t) > max(tol, abs(t) * Decimal("0.005")):
            out.append(issue("LINE_MISMATCH", "error", "line_items",
                             f"Line {i}: {q} x {u} = {q*u} but line total is {t}."))
        if t is None:
            all_have_totals = False
        else:
            line_sum += t
    sub, tax, tot = nums.get("subtotal"), nums.get("tax"), nums.get("total")
    if items and all_have_totals and sub is not None and abs(line_sum - sub) > tol:
        out.append(issue("SUBTOTAL_MISMATCH", "error", "subtotal", f"Line items sum to {line_sum} but subtotal is {sub}."))
    if sub is not None and tot is not None:
        expect = sub + (tax if tax is not None else Decimal(0))
        if abs(expect - tot) > tol:
            out.append(issue("TOTAL_MISMATCH", "error", "total",
                             f"Subtotal {sub} + tax {tax or 0} = {expect}, but total is {tot}."))
    elif items and all_have_totals and tot is not None and sub is None:
        expect = line_sum + (tax or Decimal(0))
        if abs(expect - tot) > tol:
            out.append(issue("TOTAL_MISMATCH", "error", "total",
                             f"Line items {line_sum} + tax {tax or 0} = {expect}, but total is {tot}."))
    if conf:
        low = [f for f in REQUIRED_ERR if data.get(f) not in (None, "") and 0 < (conf.get(f) or 0) < settings.review_confidence]
        for f in low:
            out.append(issue("LOW_CONFIDENCE", "warning", f, f"Low extraction confidence for '{f}' ({conf.get(f):.2f})."))
    return out


def validate_invoice(db: Session, inv: Invoice, extra_issues: Optional[list] = None) -> str:
    """Recompute issues for an invoice (replaces old ones). Returns 'ok' or 'needs_review'."""
    data = inv.data or {}
    inv.invoice_number_norm = norm_invoice_no(data.get("invoice_number")) or None
    inv.supplier_norm = norm_supplier(data.get("supplier_name")) or None
    inv.total, inv.currency, inv.invoice_date = _f(data.get("total")), data.get("currency"), data.get("invoice_date")
    issues = validate_data(data, inv.confidence) + (extra_issues or [])

    doc = db.get(Document, inv.document_id)
    if doc and doc.sha256:
        same = db.query(Document.id).filter(Document.sha256 == doc.sha256, Document.id != doc.id).first()
        if same:
            issues.append(issue("DUPLICATE_FILE", "warning", None, f"Identical file already uploaded (document {same[0]})."))
    if inv.invoice_number_norm:
        q = db.query(Invoice).filter(Invoice.invoice_number_norm == inv.invoice_number_norm, Invoice.id != inv.id)
        if inv.supplier_norm:
            q = q.filter(Invoice.supplier_norm == inv.supplier_norm)
        for other in q.limit(5).all():
            diffs = []
            if other.total is not None and inv.total is not None and abs(other.total - inv.total) > settings.tolerance:
                diffs.append(f"total {other.total} vs {inv.total}")
            if other.invoice_date and inv.invoice_date and other.invoice_date != inv.invoice_date:
                diffs.append(f"date {other.invoice_date} vs {inv.invoice_date}")
            if diffs:   # same invoice seen twice (e.g. PDF vs Excel) but values disagree
                issues.append(issue("CROSS_SOURCE_MISMATCH", "error", None,
                                    f"Invoice also in document {other.document_id} (invoice {other.id}) with different values: "
                                    + "; ".join(diffs)))
            else:
                issues.append(issue("DUPLICATE_INVOICE", "error", "invoice_number",
                                    f"Duplicate of invoice {other.id} (document {other.document_id})."))
    db.query(ValidationIssue).filter(ValidationIssue.invoice_id == inv.id).delete()
    for i in issues:
        db.add(ValidationIssue(invoice_id=inv.id, **i))
    needs = any(i["severity"] == "error" for i in issues) or any(i["code"] in ("LOW_CONFIDENCE", "UNGROUNDED_FIELD") for i in issues)
    return "needs_review" if needs else "ok"


def _f(x):
    d = _d(x)
    return float(d) if d is not None else None
