"""
CloudGuardian AI - shared rate limiting + multitenancy (gap #6)
-----------------------------------------------------------------
A dependency-free token-bucket limiter installed as a FastAPI middleware.
Keys on (tenant, client IP) so one noisy tenant can't starve another, and the
resolved tenant is echoed back on every response via `X-Tenant-ID` (or read
off `request.state.tenant` by handlers).

Defaults are generous (300 req/min burst 60) so normal monitoring/UI traffic
is unaffected. Tune per service via env:
    RATE_LIMIT_ENABLED  (default "true")
    RATE_LIMIT_RPM      (default 300)
    RATE_LIMIT_BURST    (default 60)
    DEFAULT_TENANT      (default "default")

Every platform service runs the identical copy of this module. Re-sync after
editing:
    cp services/shared/rate_limit.py services/{service}/rate_limit.py
"""

import os
import threading
import time

from fastapi import Request
from fastapi.responses import JSONResponse

RATE_LIMIT_ENABLED = os.getenv("RATE_LIMIT_ENABLED", "true").lower() == "true"
RATE_LIMIT_RPM = int(os.getenv("RATE_LIMIT_RPM", 300))
RATE_LIMIT_BURST = int(os.getenv("RATE_LIMIT_BURST", 60))
DEFAULT_TENANT = os.getenv("DEFAULT_TENANT", "default").strip() or "default"

# A zero/negative RPM is a degenerate config: the token bucket would never
# refill and retry_after computes int((..)/0.0) -> ZeroDivisionError on every
# request. Treat it as "limiter disabled" instead of crashing the API.
if RATE_LIMIT_RPM <= 0:
    RATE_LIMIT_ENABLED = False

# Health/metadata and the OIDC handshake stay unthrottled so probing and the
# IdP redirect are never blocked.
EXEMPT_PATHS = {"/health", "/metrics", "/auth/oidc/login", "/auth/oidc/callback"}


class RateLimiter:
    """Token bucket: fills at rate_per_sec up to `capacity` (burst allowance).
    consume() returns (allowed, retry_after_seconds)."""

    def __init__(self, rate_per_sec: float, capacity: float):
        self.rate_per_sec = float(rate_per_sec)
        self.capacity = float(capacity)
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def consume(self, key: str, cost: float = 1.0) -> tuple[bool, int]:
        now = time.monotonic()
        with self._lock:
            if len(self._buckets) > 10_000:
                cutoff = now - 300.0
                self._buckets = {k: v for k, v in self._buckets.items() if v[1] >= cutoff}
            tokens, last = self._buckets.get(key, (self.capacity, now))
            tokens = min(self.capacity, tokens + (now - last) * self.rate_per_sec)
            if tokens >= cost:
                self._buckets[key] = (tokens - cost, now)
                return True, 0
            self._buckets[key] = (tokens, now)
            retry = max(1, int((cost - tokens) / self.rate_per_sec))
            return False, retry


def _rate_limit_middleware_factory(limiter: RateLimiter):
    async def rate_limit_middleware(request: Request, call_next):
        if request.method == "OPTIONS" or request.url.path in EXEMPT_PATHS:
            return await call_next(request)
        tenant = (request.headers.get("x-tenant-id") or DEFAULT_TENANT).strip() or DEFAULT_TENANT
        request.state.tenant = tenant
        client = request.client.host if request.client else "unknown"
        allowed, retry_after = limiter.consume(f"{tenant}:{client}")
        if not allowed:
            return JSONResponse(
                status_code=429,
                content={
                    "detail": "rate limit exceeded",
                    "tenant": tenant,
                    "retry_after_seconds": retry_after,
                },
                headers={"Retry-After": str(retry_after)},
            )
        response = await call_next(request)
        response.headers["x-tenant-id"] = tenant
        return response

    return rate_limit_middleware


def install_rate_limit(app):
    """Attach the tenant-aware rate-limit middleware. Safe no-op if disabled."""
    if not RATE_LIMIT_ENABLED:
        return app

    limiter = RateLimiter(RATE_LIMIT_RPM / 60.0, float(RATE_LIMIT_BURST))
    app.middleware("http")(_rate_limit_middleware_factory(limiter))
    return app
