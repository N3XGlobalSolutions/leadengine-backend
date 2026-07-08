"""Application rate limiter.

Backed by Redis when REDIS_URL is configured so limits are enforced correctly
across multiple app instances / Lambda invocations. Falls back to in-memory
storage only for local development (single process).

The limiter is wired into the app in ``main.py`` via ``app.state.limiter`` and
``SlowAPIMiddleware``; individual routes may additionally apply tighter limits
with the ``@limiter.limit(...)`` decorator.
"""

from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.requests import Request

from app.core.config import get_settings

settings = get_settings()


def _rate_limit_key(request: Request) -> str:
    """Key requests by API/widget key or tenant when available, else client IP.

    Behind a proxy/ALB many users share a NAT IP, so keying purely by IP either
    throttles everyone together or nobody. Prefer a per-tenant/per-key bucket.
    """
    api_key = request.headers.get("X-API-Key") or request.headers.get("X-Widget-Key")
    if api_key:
        return f"key:{api_key}"
    tenant_id = request.headers.get("X-Tenant-ID")
    if tenant_id:
        return f"tenant:{tenant_id}:{get_remote_address(request)}"
    return get_remote_address(request)


# Use Redis storage in any environment where REDIS_URL is set (required for
# multi-instance correctness); memory:// is a dev-only fallback.
_storage_uri = settings.REDIS_URL or "memory://"

limiter = Limiter(
    key_func=_rate_limit_key,
    storage_uri=_storage_uri,
    default_limits=[settings.RATE_LIMIT_DEFAULT],
    headers_enabled=True,  # Emit RateLimit-* response headers
)
