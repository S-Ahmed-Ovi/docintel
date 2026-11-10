"""Orchestrates processing of one document: read -> segment -> extract -> ground -> validate -> embed."""
import logging
import os
import time
import traceback
from datetime import timedelta

from ..config import settings
from ..db import session_scope
from ..models import Document, Embedding, Invoice, Page, ProcessingEvent, now
from . import extract_llm, extract_rules
from .embed import chunks_for, embed
from .errors import ProcessingError
from .grounding import ground
from .readers import read_any
from .segment import segment_pages
from .validate import validate_invoice

log = logging.getLogger("process")


def _event(db, doc, stage, msg, level="info", ms=None):
    db.add(ProcessingEvent(document_id=doc.id, stage=stage, level=level, message=msg, duration_ms=ms))


def _set(db, doc, **kw):
    for k, v in kw.items():
        setattr(doc, k, v)
    db.commit()


def extract_segment(text: str, events: list):
    """LLM first (if configured), rule-based fallback. Returns (data, conf, extractor_name)."""
    if extract_llm.available():
        try:
            d, c = extract_llm.extract_with_llm(text)
            return d, c, "llm"
        except Exception as e:  # noqa
            events.append(("extract", "warning", f"LLM extraction failed ({type(e).__name__}); used rule-based fallback"))
    d, c = extract_rules.extract_from_text(text)
    return d, c, "rules"


def process_document(doc_id: int) -> None:
    t0 = time.perf_counter()
    with session_scope() as db:
        doc = db.get(Document, doc_id)
        if not doc:
            return
        try:
            _run(db, doc, t0)
        except ProcessingError as e:
            db.rollback()
            _fail(db, doc, e, t0, retry=e.retryable)
        except Exception as e:  # unknown -> treat as retryable once
            db.rollback()
            log.error("unexpected: %s", traceback.format_exc())
            _fail(db, doc, e, t0, retry=True)


def _fail(db, doc, e, t0, retry):
    doc = db.get(Document, doc.id)
    ms = int((time.perf_counter() - t0) * 1000)
    msg = f"{type(e).__name__}: {e}"
    _event(db, doc, doc.stage or "process", msg, "error", ms)
    if retry and doc.attempts < settings.max_attempts:
        delay = 2 ** doc.attempts
        _set(db, doc, status="queued", error=msg, run_after=now() + timedelta(seconds=delay))
        _event(db, doc, "retry", f"attempt {doc.attempts} failed; retry in {delay}s")
    else:
        _set(db, doc, status="failed", error=msg, finished_at=now(), processing_ms=ms, progress=1.0)
    db.commit()


def _run(db, doc: Document, t0: float):
    doc.attempts = (doc.attempts or 0) + 1
    _set(db, doc, status="processing", stage="reading", started_at=doc.started_at or now(), error=None)
    # clear derived data from earlier attempts
    db.query(Invoice).filter(Invoice.document_id == doc.id).delete()
    db.query(Page).filter(Page.document_id == doc.id).delete()
    db.commit()

    last = [0.0]

    def progress(done, total):
        if time.time() - last[0] > 0.5 or done == total:
            last[0] = time.time()
            doc.pages_done, doc.pages_total = done, total
            doc.progress = 0.7 * done / max(total, 1)
            db.commit()

    t = time.perf_counter()
    pages = read_any(doc.kind, doc.stored_path, doc.filename, progress)
    for p in pages:
        db.add(Page(document_id=doc.id, page_no=p.page_no, label=p.label, method=p.method,
                    text=p.text, ocr_confidence=p.confidence))
    methods = {m: sum(1 for p in pages if p.method == m) for m in {p.method for p in pages}}
    _event(db, doc, "reading", f"{len(pages)} page(s)/sheet(s) read: {methods}", ms=int((time.perf_counter() - t) * 1000))
    _set(db, doc, pages_total=len(pages), pages_done=len(pages), stage="extracting", progress=0.7)
    if not any(p.text.strip() for p in pages):
        from .errors import CorruptFileError
        raise CorruptFileError("No text could be extracted (blank or unreadable document)")

    t = time.perf_counter()
    results = []   # (segment_no, p_start, p_end, text, data, conf, extractor, extra_issues, structured)
    ev = []
    if doc.kind == "spreadsheet":
        n = 0
        for p in pages:
            for d, c in extract_rules.extract_from_sheet(p.rows or []):
                results.append((n, p.page_no, p.page_no, p.text if len(p.text) < 20000 else p.text[:20000], d, c, "sheet", [], True))
                n += 1
        if not results:   # unknown layout -> treat the sheet text like a document
            for s in segment_pages(pages):
                d, c, name = extract_segment(s.text, ev)
                results.append((s.no, s.page_start, s.page_end, s.text, d, c, name, [], False))
    else:
        for s in segment_pages(pages):
            d, c, name = extract_segment(s.text, ev)
            results.append((s.no, s.page_start, s.page_end, s.text, d, c, name, [], False))
    for stage, lvl, msg in ev:
        _event(db, doc, stage, msg, lvl)
    _event(db, doc, "extracting", f"{len(results)} invoice record(s) extracted", ms=int((time.perf_counter() - t) * 1000))

    doc_needs_review = False
    t = time.perf_counter()
    for i, (no, ps, pe, text, d, c, name, _, structured) in enumerate(results):
        g_issues = ground(d, c, text, structured=structured)
        inv = Invoice(document_id=doc.id, segment_no=no, page_start=ps, page_end=pe, source_text=text[:60000],
                      data=d, ai_data=dict(d), confidence=c, extractor=name)
        db.add(inv)
        db.flush()
        inv.status = validate_invoice(db, inv, g_issues)
        doc_needs_review |= inv.status == "needs_review"
        for ch in chunks_for(d, text):
            db.add(Embedding(document_id=doc.id, invoice_id=inv.id, chunk=ch, vector=embed(ch)))
        doc.progress = 0.7 + 0.3 * (i + 1) / len(results)
        db.commit()
    # second pass: catches duplicates that were processed concurrently by another worker (race window)
    db.commit()
    doc_needs_review = False
    for inv in db.query(Invoice).filter(Invoice.document_id == doc.id).all():
        inv.status = _revalidate(db, inv)
        doc_needs_review |= inv.status == "needs_review"
    db.commit()
    _event(db, doc, "validating", f"validated {len(results)} record(s)", ms=int((time.perf_counter() - t) * 1000))
    ms = int((time.perf_counter() - t0) * 1000)
    status = "needs_review" if (doc_needs_review or not results) else "completed"
    _set(db, doc, status=status, stage="done", progress=1.0, finished_at=now(), processing_ms=ms)
    _event(db, doc, "done", f"status={status}", ms=ms)
    db.commit()


def _revalidate(db, inv):
    """Re-run validation but keep the grounding warnings recorded in the first pass."""
    from ..models import ValidationIssue
    keep = [{"code": i.code, "severity": i.severity, "field": i.field, "message": i.message}
            for i in inv.issues if i.code == "UNGROUNDED_FIELD"]
    return validate_invoice(db, inv, keep)
