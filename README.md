# AI-Powered Document Intelligence System

Upload invoices/receipts as **PDF, JPG, PNG, TIFF, XLSX, XLS, CSV** -> text / OCR / spreadsheet parsing -> structured JSON
(invoice no., date, supplier, customer, subtotal, tax, total, currency, line items) -> validation & duplicate / cross-source
checks -> searchable, correctable, reviewable through a REST API and a React UI.

| Task | Where |
|---|---|
| 1 Document processing | `backend/app/pipeline/{detect,readers,ocr}.py` |
| 2 AI extraction (no invented values) | `extract_llm.py`, `extract_rules.py`, `grounding.py`, `segment.py` |
| 3 Validation | `validate.py` (12 rule codes, see `docs/DATABASE_SCHEMA.md`) |
| 4 Backend API + background jobs | `app/main.py`, `app/worker.py`, `app/pipeline/process.py` |
| 5 Database & search | `app/models.py`, `/search` (keyword + semantic + hybrid) |
| 6 Frontend | `frontend/` (React + Vite) |
| 7 Tests & evaluation | `backend/tests/`, `backend/eval/`, `docs/EVALUATION_REPORT.md`, `docs/TEST_RESULTS.txt` |

## Quick start (Docker)
```bash
cp .env.example .env            # set API_KEY and GEMINI_API_KEY
docker compose up --build       # UI http://localhost:3000  |  API docs http://localhost:8000/docs
docker compose up --scale worker=4 -d     # more workers
```
The UI uses the API key baked in at build time (`API_KEY` in `.env`) and lets you change it in the header box.
> Docker/PostgreSQL files were written but **not executed in the environment where this project was built** (no Docker there); the code itself was run and tested against SQLite. Please run `docker compose up` once and report any problem.

## Quick start (local, no Docker)
```bash
# prerequisites: Python 3.11+, Tesseract OCR (apt install tesseract-ocr / brew install tesseract), Node 18+
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
API_KEY=dev-key uvicorn app.main:app --reload          # SQLite + embedded worker threads, http://localhost:8000/docs
cd ../frontend && npm install && npm run dev            # http://localhost:5173  (API key default: dev-key)
```
LLM extraction: `export GEMINI_API_KEY=...` (or put it in `backend/.env`). Model via `GEMINI_MODEL` (default `gemini-2.5-flash`). Anthropic is still supported via `ANTHROPIC_API_KEY`; `LLM_PROVIDER=auto` prefers Gemini. On any LLM failure the system falls back to the rule-based extractor and logs a processing event.

## API (all routes need header `X-API-Key`)
| Method | Path | Purpose |
|---|---|---|
| POST | `/documents` | multi-file upload -> per-file `{accepted, id}` or rejection reason (202) |
| GET | `/documents?status=&q=&supplier=&date_from=&date_to=&needs_review=` | list/filter with progress |
| GET | `/documents/{id}` | status, stage, progress, processing history |
| GET | `/documents/{id}/result` | invoices: data, original AI data, confidence, validation issues, corrections |
| GET | `/documents/{id}/text` | extracted text per page/sheet (+ OCR confidence) |
| PATCH | `/documents/{id}/result` | manual correction (re-validates, audit trail) |
| POST | `/documents/{id}/reprocess` | re-queue failed job (`force=true` for any) |
| GET | `/search?q=&mode=keyword\|semantic\|hybrid&supplier=&currency=&date_from=&date_to=&needs_review=` | search invoices |
| GET | `/stats`, `/health`, `DELETE /documents/{id}` | |

Example: `curl -H "X-API-Key: dev-key" -F files=@invoice.pdf -F files=@sheet.xlsx localhost:8000/documents`

## Tests & evaluation
```bash
cd backend
pytest -q                                                     # 57 tests (OCR, spreadsheets, extraction, validation, API, failures)
python -m eval.generate_dataset --out eval/dataset            # labeled synthetic set (already included, seed 7)
python -m eval.run_eval --dataset eval/dataset --report ../docs/EVALUATION_REPORT.md --workers 4
python -m eval.bench_large --pages 1000                       # 1000-page PDF stress test
```
`run_eval` drives the real HTTP API + background workers and computes every number from the run. See **`docs/EVALUATION_REPORT.md`**.


## Known limitations
* Semantic search uses a built-in hashed n-gram embedder (offline, fuzzy-lexical). For true semantics set `EMBEDDING_MODEL=<sentence-transformers model>`; for large scale move vectors to pgvector (notes in `docs/DATABASE_SCHEMA.md`). Search scans vectors in Python.
* Rule-based extractor is layout-dependent (English labels); the LLM path is the intended production extractor.
* Single API key (no per-user accounts/RBAC), no encryption at rest, no antivirus scan, no TLS (use a reverse proxy).
* Handwriting, rotated-90 pages, non-Latin OCR (set `OCR_LANG`) are not specially handled.
* PDF<->Excel cross-verification matches on normalised (supplier, invoice number) and compares total/date; it does not yet compare line-item level or fuzzy-match renamed suppliers.
* Migrations: tables are created with `create_all`; add Alembic for production schema evolution.


```
