import hmac
import re
import time
from collections import defaultdict, deque
from threading import Lock

from fastapi import Header, HTTPException, Request

from .config import settings

_hits: dict[str, deque] = defaultdict(deque)
_lock = Lock()


def require_key(request: Request, x_api_key: str = Header(default="")):
    if not hmac.compare_digest(x_api_key.encode(), settings.api_key.encode()):
        raise HTTPException(401, "Invalid or missing API key")
    ident = x_api_key + "|" + (request.client.host if request.client else "")
    now = time.time()
    with _lock:
        q = _hits[ident]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= settings.rate_limit_per_min:
            raise HTTPException(429, "Rate limit exceeded")
        q.append(now)


def safe_filename(name: str) -> str:
    name = re.sub(r"[\x00-\x1f/\\]", "", name or "upload")
    name = re.sub(r"[^\w.\- ()]", "_", name).strip(". ") or "upload"
    return name[:200]
