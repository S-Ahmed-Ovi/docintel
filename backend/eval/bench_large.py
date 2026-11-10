"""Stress test: one N-page PDF containing N distinct invoices (default 1000 pages).
    python -m eval.bench_large --pages 1000"""
import argparse, os, random, sys, tempfile, time
ap = argparse.ArgumentParser(); ap.add_argument("--pages", type=int, default=1000); a = ap.parse_args()
tmp = tempfile.mkdtemp()
os.environ.update(DATABASE_URL=f"sqlite:///{tmp}/b.db", STORAGE_DIR=tmp, API_KEY="k", ANTHROPIC_API_KEY="", GEMINI_API_KEY="")
sys.path.insert(0, os.getcwd())
from faker import Faker
from reportlab.pdfgen import canvas
from eval.generate_dataset import make_truth, render_pdf
from app.db import init_db, SessionLocal
from app.models import Document, Invoice
from app.pipeline.process import process_document
import resource

fk, rng = Faker(), random.Random(1)
path = f"{tmp}/big.pdf"; c = None; truths = []
for i in range(a.pages):
    t = make_truth(rng, fk); t["invoice_number"] = f"INV-{i:05d}"; truths.append(t)
    c = render_pdf(t, path, "ABCD"[i % 4] if False else "A", c=c, finish=False); c.showPage()
c.save()
init_db()
with SessionLocal() as db:
    d = Document(filename="big.pdf", stored_path=path, kind="pdf", mime="application/pdf", size_bytes=os.path.getsize(path), sha256="x", status="queued")
    db.add(d); db.commit(); did = d.id
t0 = time.perf_counter(); process_document(did); dt = time.perf_counter() - t0
with SessionLocal() as db:
    d = db.get(Document, did); invs = db.query(Invoice).filter(Invoice.document_id == did).all()
    ok = sum(1 for i in invs if i.data.get("total") == truths[int(i.data["invoice_number"][4:])]["total"]) if invs else 0
    print(f"pages={a.pages} status={d.status} invoices={len(invs)} totals_correct={ok} time={dt:.1f}s ({dt/a.pages*1000:.0f} ms/page) "
          f"peak_rss={resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024:.0f}MB pdf={os.path.getsize(path)/1e6:.1f}MB")
