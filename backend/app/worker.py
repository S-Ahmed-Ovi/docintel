"""Database-backed job queue. Workers claim rows atomically, so you can run N worker processes/containers:
    python -m app.worker
Postgres/SQLite both supported (claim = conditional UPDATE, race-free)."""
import logging
import threading
import time
from datetime import timedelta

from sqlalchemy import or_, update

from .config import settings
from .db import SessionLocal, init_db
from .models import Document, now
from .pipeline.process import process_document

log = logging.getLogger("worker")
STALE_MINUTES = 15


def claim_next() -> int | None:
    db = SessionLocal()
    try:
        cand = (db.query(Document.id).filter(Document.status == "queued",
                                             or_(Document.run_after.is_(None), Document.run_after <= now()))
                .order_by(Document.created_at).limit(5).all())
        for (doc_id,) in cand:
            res = db.execute(update(Document).where(Document.id == doc_id, Document.status == "queued")
                             .values(status="processing", stage="starting"))
            db.commit()
            if res.rowcount == 1:
                return doc_id
        return None
    finally:
        db.close()


def requeue_stale():
    """Recover jobs whose worker died mid-processing."""
    db = SessionLocal()
    try:
        cutoff = now() - timedelta(minutes=STALE_MINUTES)
        n = db.execute(update(Document).where(Document.status == "processing", Document.updated_at < cutoff)
                       .values(status="queued")).rowcount
        db.commit()
        if n:
            log.warning("requeued %d stale jobs", n)
    finally:
        db.close()


def run_forever(stop: threading.Event | None = None, poll: float = 0.5):
    stop = stop or threading.Event()
    last_gc = 0.0
    while not stop.is_set():
        if time.time() - last_gc > 60:
            requeue_stale(); last_gc = time.time()
        doc_id = claim_next()
        if doc_id is None:
            stop.wait(poll)
            continue
        try:
            process_document(doc_id)
        except Exception:
            log.exception("job %s crashed", doc_id)


def start_threads(n: int | None = None):
    stop = threading.Event()
    ts = [threading.Thread(target=run_forever, args=(stop,), daemon=True, name=f"worker-{i}")
          for i in range(n or settings.worker_threads)]
    for t in ts:
        t.start()
    return stop, ts


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    init_db()
    stop, ts = start_threads()
    log.info("worker started with %d threads", len(ts))
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        stop.set()
