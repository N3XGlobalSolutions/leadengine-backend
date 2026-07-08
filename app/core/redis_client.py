import redis
from app.core.config import get_settings

settings = get_settings()


def _build_redis_client():
    """Build a resilient Redis client backed by a connection pool.

    Returns None when REDIS_URL is not configured so that the application can
    boot and degrade gracefully instead of raising at import time.
    """
    if not settings.REDIS_URL:
        return None

    pool = redis.ConnectionPool.from_url(
        settings.REDIS_URL,
        socket_timeout=2,
        socket_connect_timeout=2,
        retry_on_timeout=True,
        health_check_interval=30,
        decode_responses=True,
    )
    return redis.Redis(connection_pool=pool)


redis_client = _build_redis_client()
