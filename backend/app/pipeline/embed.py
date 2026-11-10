"""Embeddings. Default: dependency-free hashed token+char-n-gram vectors (lexical-fuzzy, deterministic).
Set EMBEDDING_MODEL to a sentence-transformers model name for true semantic embeddings."""
import hashlib
import math
import os
import re
from ..config import settings

_st = None


def _st_model():
    global _st
    name = os.environ.get("EMBEDDING_MODEL")
    if not name:
        return None
    if _st is None:
        from sentence_transformers import SentenceTransformer
        _st = SentenceTransformer(name)
    return _st


def _h(tok: str) -> int:
    return int.from_bytes(hashlib.md5(tok.encode()).digest()[:8], "big")


def embed(text: str) -> list[float]:
    m = _st_model()
    if m is not None:
        return [float(x) for x in m.encode(text[:2000], normalize_embeddings=True)]
    dim = settings.embedding_dim
    v = [0.0] * dim
    toks = re.findall(r"[a-z0-9]+", text.lower())
    feats = [(t, 1.0) for t in toks]
    for t in toks:
        t2 = f"^{t}$"
        feats += [(t2[i:i + 3], 0.3) for i in range(len(t2) - 2)]
    for f, w in feats:
        h = _h(f)
        v[h % dim] += w * (1 if (h >> 40) & 1 else -1)
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def cosine(a, b) -> float:
    if len(a) != len(b):
        return 0.0
    return sum(x * y for x, y in zip(a, b))


def chunks_for(data: dict, source: str) -> list[str]:
    summary = " ".join(str(data.get(k) or "") for k in
                       ("invoice_number", "supplier_name", "customer_name", "invoice_date", "currency", "total"))
    items = " ".join(str(li.get("description") or "") for li in data.get("line_items") or [])
    out = [f"{summary} {items}".strip()]
    for i in range(0, min(len(source), 2400), 800):
        out.append(source[i:i + 800])
    return [c for c in out if c.strip()]
