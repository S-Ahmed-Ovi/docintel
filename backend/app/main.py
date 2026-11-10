import hashlib
import os
import uuid
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import Depends, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from .config import settings
from .db import get_db, init_db
from .models import Correction, Document, Embedding, Invoice, Page, ProcessingEvent, now
from .pipeline.detect import detect_kind
from .pipeline.embed import cosine, embed
from .pipeline.normalize import money, norm_invoice_no, to_currency, to_iso_date
from .pipeline.validate import validate_invoice
from .schemas import CorrectionIn
from .security import require_key, safe_filename


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    os.makedirs(settings.storage_dir, exist_ok=True)
    stop = None
    if os.environ.get("EMBEDDED_WORKER", "1") == "1" and not settings.run_inline:
        from .worker import start_threads
        stop, _ = start_threads()
    yield
    if stop:
        stop.set()


app = FastAPI(title="Document Intelligence API", version="1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in settings.cors_origins.split(",")],
                   allow_methods=["GET", "POST", "PATCH", "DELETE"], allow_headers=["X-API-Key", "Content-Type"])


@app.middleware("http")
async def security_headers(request, call_next):
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Cache-Control"] = "no-store"
    return resp


auth = [Depends(require_key)]


def _doc_dict(d: Document, detail=False, n_inv=None, n_issues=None):
    out = {"id": d.id, "filename": d.filename, "kind": d.kind, "mime": d.mime, "size_bytes": d.size_bytes,
           "status": d.status, "stage": d.stage, "progress": round(d.progress or 0, 3),
           "pages_total": d.pages_total, "pages_done": d.pages_done, "attempts": d.attempts, "error": d.error,
           "created_at": d.created_at, "finished_at": d.finished_at, "processing_ms": d.processing_ms}
    if n_inv is not None:
        out["invoice_count"] = n_inv
    if detail:
        out["events"] = [{"stage": e.stage, "level": e.level, "message": e.message, "duration_ms": e.duration_ms,
                          "at": e.created_at} for e in sorted(d.events, key=lambda e: e.id)]
    return out


def _inv_dict(i: Invoice):
    return {"id": i.id, "segment_no": i.segment_no, "page_start": i.page_start, "page_end": i.page_end,
            "status": i.status, "extractor": i.extractor, "data": i.data, "ai_data": i.ai_data,
            "confidence": i.confidence,
            "issues": [{"id": x.id, "code": x.code, "severity": x.severity, "field": x.field, "message": x.message}
                       for x in i.issues],
            "corrections": [{"field": c.field, "old": c.old_value, "new": c.new_value, "by": c.corrected_by,
                             "at": c.created_at} for c in sorted(i.corrections, key=lambda c: c.id)]}


@app.get("/health")
def health(db: Session = Depends(get_db)):
    db.query(func.count(Document.id)).scalar()
    return {"status": "ok"}


@app.post("/documents", dependencies=auth, status_code=202)
async def upload(files: list[UploadFile] = File(...), db: Session = Depends(get_db)):
    """Multi-file upload. Returns one entry per file (accepted -> job id, or rejected -> reason)."""
    results = []
    limit = settings.max_upload_mb * 1024 * 1024
    for f in files:
        fname = safe_filename(f.filename)
        path = os.path.join(settings.storage_dir, uuid.uuid4().hex)
        h, size = hashlib.sha256(), 0
        try:
            with open(path, "wb") as out:
                while chunk := await f.read(1024 * 1024):
                    size += len(chunk)
                    if size > limit:
                        raise ValueError(f"file exceeds {settings.max_upload_mb} MB limit")
                    h.update(chunk); out.write(chunk)
            if size == 0:
                raise ValueError("file is empty")
            kind, mime = detect_kind(path, fname)
            if kind == "unknown":
                raise ValueError("unsupported file type (allowed: PDF, JPG, PNG, TIFF, XLSX, XLS, CSV)")
        except ValueError as e:
            if os.path.exists(path):
                os.remove(path)
            results.append({"filename": fname, "accepted": False, "error": str(e)})
            continue
        doc = Document(filename=fname, stored_path=path, mime=mime, kind=kind, size_bytes=size,
                       sha256=h.hexdigest(), status="queued", stage="queued")
        db.add(doc)
        db.flush()
        db.add(ProcessingEvent(document_id=doc.id, stage="upload", message=f"accepted {kind} ({size} bytes)"))
        db.commit()
        results.append({"filename": fname, "accepted": True, "id": doc.id, "status": doc.status})
        if settings.run_inline:
            from .pipeline.process import process_document
            process_document(doc.id)
    return {"results": results}


@app.get("/documents", dependencies=auth)
def list_documents(status: Optional[str] = None, q: Optional[str] = None, supplier: Optional[str] = None,
                   date_from: Optional[str] = None, date_to: Optional[str] = None, needs_review: Optional[bool] = None,
                   limit: int = Query(50, le=200), offset: int = 0, db: Session = Depends(get_db)):
    query = db.query(Document)
    if status:
        query = query.filter(Document.status == status)
    if needs_review:
        query = query.filter(Document.status == "needs_review")
    if supplier or date_from or date_to:
        sub = db.query(Invoice.document_id)
        if supplier:
            sub = sub.filter(Invoice.supplier_norm.contains(supplier.lower()))
        if date_from:
            sub = sub.filter(Invoice.invoice_date >= date_from)
        if date_to:
            sub = sub.filter(Invoice.invoice_date <= date_to)
        query = query.filter(Document.id.in_(sub))
    if q:
        query = query.filter(or_(Document.filename.ilike(f"%{q}%"), Document.id.in_(
            db.query(Invoice.document_id).filter(Invoice.source_text.ilike(f"%{q}%")))))
    total = query.count()
    rows = query.order_by(Document.id.desc()).limit(limit).offset(offset).all()
    counts = dict(db.query(Invoice.document_id, func.count(Invoice.id)).filter(
        Invoice.document_id.in_([r.id for r in rows])).group_by(Invoice.document_id).all()) if rows else {}
    return {"total": total, "items": [_doc_dict(r, n_inv=counts.get(r.id, 0)) for r in rows]}


@app.get("/documents/{doc_id}", dependencies=auth)
def get_document(doc_id: int, db: Session = Depends(get_db)):
    d = db.get(Document, doc_id)
    if not d:
        raise HTTPException(404, "Document not found")
    return _doc_dict(d, detail=True)


@app.get("/documents/{doc_id}/result", dependencies=auth)
def get_result(doc_id: int, db: Session = Depends(get_db)):
    d = db.get(Document, doc_id)
    if not d:
        raise HTTPException(404, "Document not found")
    invs = db.query(Invoice).filter(Invoice.document_id == doc_id).order_by(Invoice.segment_no).all()
    return {"document": _doc_dict(d), "invoices": [_inv_dict(i) for i in invs]}


@app.get("/documents/{doc_id}/text", dependencies=auth)
def get_text(doc_id: int, db: Session = Depends(get_db)):
    pages = db.query(Page).filter(Page.document_id == doc_id).order_by(Page.page_no).limit(200).all()
    return {"pages": [{"page_no": p.page_no, "label": p.label, "method": p.method,
                       "ocr_confidence": p.ocr_confidence, "text": p.text} for p in pages]}


@app.patch("/documents/{doc_id}/result", dependencies=auth)
def correct(doc_id: int, body: CorrectionIn, db: Session = Depends(get_db)):
    q = db.query(Invoice).filter(Invoice.document_id == doc_id)
    inv = q.filter(Invoice.id == body.invoice_id).first() if body.invoice_id else q.order_by(Invoice.segment_no).first()
    if not inv:
        raise HTTPException(404, "Invoice not found for this document")
    patch = body.model_dump(exclude_unset=True, exclude={"invoice_id", "corrected_by"})
    if not patch:
        raise HTTPException(422, "No fields to correct")
    data = dict(inv.data or {})
    for field, new in patch.items():
        if field == "line_items":
            new = [li if isinstance(li, dict) else li for li in new]
        if data.get(field) != new:
            db.add(Correction(invoice_id=inv.id, field=field, old_value=data.get(field), new_value=new,
                              corrected_by=body.corrected_by))
            data[field] = new
        conf = dict(inv.confidence or {}); conf[field] = 1.0; inv.confidence = conf
    inv.data = data
    status = validate_invoice(db, inv)
    inv.status = "corrected" if status == "ok" else "needs_review"
    d = db.get(Document, doc_id)
    others = db.query(Invoice).filter(Invoice.document_id == doc_id, Invoice.id != inv.id).all()
    d.status = "needs_review" if (status != "ok" or any(o.status == "needs_review" for o in others)) else "completed"
    db.add(ProcessingEvent(document_id=doc_id, stage="correction", message=f"manual correction of {sorted(patch)}"))
    db.commit()
    return _inv_dict(inv)


@app.post("/documents/{doc_id}/reprocess", dependencies=auth, status_code=202)
def reprocess(doc_id: int, force: bool = False, db: Session = Depends(get_db)):
    d = db.get(Document, doc_id)
    if not d:
        raise HTTPException(404, "Document not found")
    if d.status != "failed" and not force:
        raise HTTPException(409, f"Only failed documents can be reprocessed (status={d.status}); use force=true")
    if not os.path.exists(d.stored_path):
        raise HTTPException(410, "Original file no longer on disk")
    d.status, d.attempts, d.error, d.run_after, d.progress, d.stage = "queued", 0, None, None, 0.0, "queued"
    db.add(ProcessingEvent(document_id=d.id, stage="reprocess", message="re-queued by user"))
    db.commit()
    if settings.run_inline:
        from .pipeline.process import process_document
        process_document(d.id)
    return {"id": d.id, "status": d.status}


@app.delete("/documents/{doc_id}", dependencies=auth)
def delete_doc(doc_id: int, db: Session = Depends(get_db)):
    d = db.get(Document, doc_id)
    if not d:
        raise HTTPException(404, "Document not found")
    try:
        os.remove(d.stored_path)
    except OSError:
        pass
    db.delete(d); db.commit()
    return {"deleted": doc_id}


@app.get("/search", dependencies=auth)
def search(q: str = Query(..., min_length=1, max_length=300), mode: str = Query("hybrid", pattern="^(keyword|semantic|hybrid)$"),
           supplier: Optional[str] = None, currency: Optional[str] = None, date_from: Optional[str] = None,
           date_to: Optional[str] = None, needs_review: Optional[bool] = None, limit: int = Query(20, le=100),
           db: Session = Depends(get_db)):
    def base():
        qq = db.query(Invoice)
        if supplier: qq = qq.filter(Invoice.supplier_norm.contains(supplier.lower()))
        if currency: qq = qq.filter(Invoice.currency == currency.upper())
        if date_from: qq = qq.filter(Invoice.invoice_date >= date_from)
        if date_to: qq = qq.filter(Invoice.invoice_date <= date_to)
        if needs_review: qq = qq.filter(Invoice.status == "needs_review")
        return qq
    ranks: dict[int, dict] = {}
    if mode in ("keyword", "hybrid"):
        terms = [t for t in q.split() if t][:8]
        qq = base()
        for t in terms:
            like = f"%{t}%"
            qq = qq.filter(or_(Invoice.source_text.ilike(like), Invoice.supplier_norm.ilike(like),
                               Invoice.invoice_number_norm.ilike(f"%{norm_invoice_no(t)}%" if norm_invoice_no(t) else like)))
        for r, inv in enumerate(qq.order_by(Invoice.id.desc()).limit(limit * 3).all()):
            ranks.setdefault(inv.id, {"inv": inv})["kw"] = r
    if mode in ("semantic", "hybrid"):
        qv = embed(q)
        allowed = {i for (i,) in base().with_entities(Invoice.id).all()}
        scored: dict[int, float] = {}
        for iid, vec in db.query(Embedding.invoice_id, Embedding.vector).yield_per(2000):
            if iid in allowed:
                sc = cosine(qv, vec)
                if sc > scored.get(iid, -1):
                    scored[iid] = sc
        top = sorted(scored.items(), key=lambda kv: -kv[1])[:limit * 3]
        for r, (iid, s) in enumerate(top):
            if s <= 0.05:
                continue
            e = ranks.setdefault(iid, {"inv": db.get(Invoice, iid)})
            e["sem"], e["score"] = r, s
    k = 60
    hits = []
    for iid, e in ranks.items():
        rrf = sum(1 / (k + e[m]) for m in ("kw", "sem") if m in e)
        hits.append((rrf, e))
    hits.sort(key=lambda x: -x[0])
    return {"query": q, "mode": mode, "results": [
        {"score": round(s, 5), "document_id": e["inv"].document_id, "invoice_id": e["inv"].id,
         "filename": e["inv"].document.filename, "status": e["inv"].status, "data": e["inv"].data,
         "matched_by": [m for m in ("kw", "sem") if m in e]} for s, e in hits[:limit]]}


@app.get("/stats", dependencies=auth)
def stats(db: Session = Depends(get_db)):
    by = dict(db.query(Document.status, func.count(Document.id)).group_by(Document.status).all())
    avg = db.query(func.avg(Document.processing_ms)).filter(Document.processing_ms.isnot(None)).scalar()
    return {"by_status": by, "total": sum(by.values()), "avg_processing_ms": round(avg or 0)}
