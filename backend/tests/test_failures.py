import os
from datetime import datetime
from app.config import settings
from app.db import SessionLocal
from app.models import Document
from app.pipeline import process
from app.pipeline.errors import TransientError
from app.worker import claim_next
from conftest import H, upload


def test_corrupt_files_fail_gracefully(client):
    for name, blob in [("t.pdf", b"%PDF-1.4 trunc"), ("g.png", b"\x89PNG\r\n\x1a\nzzzz"),
                       ("f.xlsx", b"PK\x03\x04junk"), ("e.csv", b"\n\n")]:
        r = upload(client, name, blob)
        d = client.get(f"/documents/{r['id']}", headers=H).json()
        assert d["status"] == "failed" and d["error"], name
        assert any(e["level"] == "error" for e in d["events"])


def test_blank_pdf_fails_with_reason(client, tmp_path):
    import pymupdf as fitz
    d = fitz.open(); d.new_page(); p = tmp_path / "b.pdf"; d.save(str(p))
    r = upload(client, "b.pdf", p.read_bytes())
    doc = client.get(f"/documents/{r['id']}", headers=H).json()
    assert doc["status"] == "failed" and "No text" in doc["error"]


def test_reprocess_failed_job(client, pdf_path, monkeypatch):
    def boom(*a, **k): raise TransientError("ocr backend down")
    monkeypatch.setattr(process, "read_any", boom)
    r = upload(client, "a.pdf", open(pdf_path, "rb").read())
    # transient: re-queued with backoff, until attempts are exhausted
    for _ in range(settings.max_attempts):
        d = client.get(f"/documents/{r['id']}", headers=H).json()
        if d["status"] == "failed":
            break
        assert d["status"] == "queued" and d["attempts"] >= 1
        with SessionLocal() as db:
            db.get(Document, r["id"]).run_after = None; db.commit()
        process.process_document(r["id"])
    assert client.get(f"/documents/{r['id']}", headers=H).json()["status"] == "failed"
    monkeypatch.undo()
    assert client.post(f"/documents/{r['id']}/reprocess", headers=H).status_code == 202
    d = client.get(f"/documents/{r['id']}", headers=H).json()
    assert d["status"] in ("completed", "needs_review") and d["attempts"] == 1


def test_reprocess_rejects_non_failed(client, pdf_path):
    r = upload(client, "a.pdf", open(pdf_path, "rb").read())
    assert client.post(f"/documents/{r['id']}/reprocess", headers=H).status_code == 409
    assert client.post("/documents/777/reprocess", headers=H).status_code == 404


def test_unexpected_exception_is_contained(client, pdf_path, monkeypatch):
    monkeypatch.setattr(process, "read_any", lambda *a, **k: 1 / 0)
    r = upload(client, "a.pdf", open(pdf_path, "rb").read())
    assert client.get(f"/documents/{r['id']}", headers=H).json()["status"] in ("queued", "failed")


def test_claim_is_atomic(client, pdf_path, monkeypatch):
    monkeypatch.setattr(settings, "run_inline", False)
    a = upload(client, "a.pdf", open(pdf_path, "rb").read())
    first, second = claim_next(), claim_next()
    assert first == a["id"] and second is None        # a job can be claimed only once
