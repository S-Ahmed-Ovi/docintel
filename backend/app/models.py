import datetime as dt
from sqlalchemy import (JSON, Column, DateTime, Float, ForeignKey, Index, Integer, String, Text)
from sqlalchemy.orm import relationship
from .db import Base


def now():
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


class Document(Base):
    """One uploaded file = one job."""
    __tablename__ = "documents"
    id = Column(Integer, primary_key=True)
    filename = Column(String(512), nullable=False)
    stored_path = Column(String(1024), nullable=False)
    mime = Column(String(100))
    kind = Column(String(20))                       # pdf|image|spreadsheet|unknown
    size_bytes = Column(Integer)
    sha256 = Column(String(64), index=True)
    status = Column(String(20), default="queued", index=True)  # queued|processing|completed|needs_review|failed
    stage = Column(String(40))
    progress = Column(Float, default=0.0)
    pages_total = Column(Integer, default=0)
    pages_done = Column(Integer, default=0)
    attempts = Column(Integer, default=0)
    error = Column(Text)
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)
    started_at = Column(DateTime)
    finished_at = Column(DateTime)
    processing_ms = Column(Integer)
    run_after = Column(DateTime)
    pages = relationship("Page", cascade="all, delete-orphan", back_populates="document")
    invoices = relationship("Invoice", cascade="all, delete-orphan", back_populates="document")
    events = relationship("ProcessingEvent", cascade="all, delete-orphan", back_populates="document")


class Page(Base):
    """Per page (or per sheet) extracted text."""
    __tablename__ = "document_pages"
    id = Column(Integer, primary_key=True)
    document_id = Column(Integer, ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    page_no = Column(Integer)
    label = Column(String(255))                     # sheet name for spreadsheets
    method = Column(String(20))                     # text|ocr|sheet
    text = Column(Text)
    ocr_confidence = Column(Float)
    document = relationship("Document", back_populates="pages")


class Invoice(Base):
    """One structured extraction (a document can contain many invoices)."""
    __tablename__ = "invoices"
    id = Column(Integer, primary_key=True)
    document_id = Column(Integer, ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    segment_no = Column(Integer, default=0)
    page_start = Column(Integer)
    page_end = Column(Integer)
    source_text = Column(Text)
    data = Column(JSON)                             # current values (AI or manually corrected)
    ai_data = Column(JSON)                          # untouched original extraction
    confidence = Column(JSON)                       # per-field confidence
    extractor = Column(String(30))
    status = Column(String(20), default="ok", index=True)  # ok|needs_review|corrected
    invoice_number_norm = Column(String(100), index=True)
    supplier_norm = Column(String(255), index=True)
    total = Column(Float)
    currency = Column(String(8))
    invoice_date = Column(String(10), index=True)
    created_at = Column(DateTime, default=now)
    document = relationship("Document", back_populates="invoices")
    issues = relationship("ValidationIssue", cascade="all, delete-orphan", back_populates="invoice")
    corrections = relationship("Correction", cascade="all, delete-orphan", back_populates="invoice")
    embeddings = relationship("Embedding", cascade="all, delete-orphan", back_populates="invoice")


class ValidationIssue(Base):
    __tablename__ = "validation_issues"
    id = Column(Integer, primary_key=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id", ondelete="CASCADE"), index=True)
    code = Column(String(50), index=True)
    severity = Column(String(10))                   # error|warning
    field = Column(String(50))
    message = Column(Text)
    invoice = relationship("Invoice", back_populates="issues")


class Correction(Base):
    """Audit trail of manual edits."""
    __tablename__ = "corrections"
    id = Column(Integer, primary_key=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id", ondelete="CASCADE"), index=True)
    field = Column(String(50))
    old_value = Column(JSON)
    new_value = Column(JSON)
    corrected_by = Column(String(100), default="api-user")
    created_at = Column(DateTime, default=now)
    invoice = relationship("Invoice", back_populates="corrections")


class ProcessingEvent(Base):
    __tablename__ = "processing_events"
    id = Column(Integer, primary_key=True)
    document_id = Column(Integer, ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    stage = Column(String(40))
    level = Column(String(10), default="info")
    message = Column(Text)
    duration_ms = Column(Integer)
    created_at = Column(DateTime, default=now)
    document = relationship("Document", back_populates="events")


class Embedding(Base):
    """Vector chunks. On Postgres the JSON column can be swapped for pgvector `vector(N)` (see docs)."""
    __tablename__ = "embeddings"
    id = Column(Integer, primary_key=True)
    document_id = Column(Integer, ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id", ondelete="CASCADE"), index=True)
    chunk = Column(Text)
    vector = Column(JSON)
    invoice = relationship("Invoice", back_populates="embeddings")


Index("ix_invoice_dup", Invoice.supplier_norm, Invoice.invoice_number_norm)
