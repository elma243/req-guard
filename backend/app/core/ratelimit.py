"""In-memory sliding-window rate limiter for the auth endpoints.

Counts live in this process and are lost on restart, which suits a single backend worker. Several workers would
each count on their own. Lockout and OTP attempts are stored in the database, so those survive either way.
Keys never depend on whether an account exists, so being limited reveals nothing about an email.
"""

import hmac
import math
import threading
from collections import deque
from time import monotonic

from fastapi import HTTPException, Request, status

from app.core.config import settings

# Old keys are swept once this many are held, so random emails cannot grow memory without end
MAX_KEYS = 10_000


class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, tuple[float, deque[float]]] = {}
        self._lock = threading.Lock()  # sync endpoints run in a thread pool

    def hit(self, key: str, limit: int, window: float) -> int:
        """Count one attempt. Returns 0 when allowed, otherwise the seconds to wait (not counted as a hit)."""
        now = monotonic()
        with self._lock:
            if len(self._hits) >= MAX_KEYS:
                self._sweep(now)
            _, hits = self._hits.setdefault(key, (window, deque()))
            while hits and hits[0] <= now - window:
                hits.popleft()
            if len(hits) >= limit:
                return max(1, math.ceil(hits[0] + window - now))
            hits.append(now)
            return 0

    def _sweep(self, now: float) -> None:
        self._hits = {
            key: entry for key, entry in self._hits.items() if entry[1] and entry[1][-1] > now - entry[0]
        }

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


limiter = RateLimiter()


def enforce(key: str, limit: int, window: float, message: str = "Too many attempts.") -> None:
    """Count one attempt, or answer 429 with Retry-After."""
    wait = limiter.hit(key, limit, window)
    if wait:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"{message} Try again in {wait} seconds.",
            headers={"Retry-After": str(wait)},
        )


def client_ip(request: Request) -> str:
    """The browser's address. FastAPI only sees the Next.js server, which passes the real one on.

    The header is believed only with the shared secret; anyone calling the API directly could set it to anything.
    """
    forwarded = request.headers.get("x-client-ip", "").strip()
    sent = request.headers.get("x-proxy-secret", "")
    secret = settings.proxy_shared_secret
    if secret and forwarded and hmac.compare_digest(sent.encode(), secret.encode()):
        return forwarded
    return request.client.host if request.client else "unknown"


def limit_auth(request: Request, action: str, email: str) -> None:
    """Allow an auth action AUTH_RATE_LIMIT_PER_MINUTE times per minute, per IP and per email."""
    per_minute = settings.auth_rate_limit_per_minute
    enforce(f"{action}:ip:{client_ip(request)}", per_minute, 60)
    enforce(f"{action}:email:{email}", per_minute, 60)
