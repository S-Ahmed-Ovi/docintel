import os
from contextlib import contextmanager
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from .config import settings

def _normalize(url: str) -> str:
    """Force the psycopg2 driver (newer SQLAlchemy defaults 'postgresql://' to psycopg v3)."""
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg2://" + url[len(prefix):]
    return url


DB_URL = _normalize(settings.database_url)
IS_SQLITE = DB_URL.startswith("sqlite")

if IS_SQLITE:
    os.makedirs("./data", exist_ok=True)
    engine = create_engine(DB_URL, connect_args={"check_same_thread": False, "timeout": 30})
else:
    engine = create_engine(DB_URL, pool_pre_ping=True, pool_size=10)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
Base = declarative_base()


@contextmanager
def session_scope():
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def get_db():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


def init_db():
    """Create tables. On Postgres an advisory lock stops the API and workers racing on first start."""
    import time
    from sqlalchemy import text
    from . import models  # noqa
    if IS_SQLITE:
        Base.metadata.create_all(engine)
        return
    for attempt in range(10):                      # also waits for the DB if it is still starting
        try:
            with engine.begin() as conn:
                conn.execute(text("SELECT pg_advisory_xact_lock(727272)"))
                Base.metadata.create_all(conn)
            return
        except Exception:
            if attempt == 9:
                raise
            time.sleep(2)
