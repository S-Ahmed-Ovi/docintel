# Database schema (SQLAlchemy models in `backend/app/models.py`)

```mermaid
erDiagram
  documents ||--o{ document_pages : has
  documents ||--o{ invoices : yields
  documents ||--o{ processing_events : logs
  invoices  ||--o{ validation_issues : has
  invoices  ||--o{ corrections : audited_by
  invoices  ||--o{ embeddings : indexed_by
  documents { int id PK  string filename  string stored_path  string kind  string sha256  string status  string stage  float progress  int pages_total  int pages_done  int attempts  text error  datetime run_after  int processing_ms }
  document_pages { int id PK  int document_id FK  int page_no  string label  string method  text text  float ocr_confidence }
  invoices { int id PK  int document_id FK  int segment_no  int page_start  int page_end  json data  json ai_data  json confidence  string extractor  string status  string invoice_number_norm  string supplier_norm  float total  string currency  string invoice_date }
  validation_issues { int id PK  int invoice_id FK  string code  string severity  string field  text message }
  corrections { int id PK  int invoice_id FK  string field  json old_value  json new_value  string corrected_by  datetime created_at }
  processing_events { int id PK  int document_id FK  string stage  string level  text message  int duration_ms }
  embeddings { int id PK  int document_id FK  int invoice_id FK  text chunk  json vector }
```

Indexes: `documents(status, sha256)`, `invoices(document_id, status, invoice_date, invoice_number_norm, supplier_norm)`, composite `(supplier_norm, invoice_number_norm)` for duplicate detection, FKs on all child tables (`ON DELETE CASCADE`).

**Scaling notes:** on PostgreSQL, switch `embeddings.vector` to `pgvector` `vector(N)` with an HNSW index and replace the Python cosine loop in `/search` with `ORDER BY vector <=> :q`; add a `tsvector` GIN index on `invoices.source_text` for full-text keyword search (currently `ILIKE`).

**Validation codes:** MISSING_FIELD, INVALID_NUMBER, INVALID_DATE, INVALID_CURRENCY, LINE_MISMATCH (qty x price != line total), SUBTOTAL_MISMATCH (sum of lines != subtotal), TOTAL_MISMATCH (subtotal + tax != total), DUPLICATE_INVOICE, DUPLICATE_FILE, CROSS_SOURCE_MISMATCH (same invoice in another PDF/sheet with different values), UNGROUNDED_FIELD, LOW_CONFIDENCE.
