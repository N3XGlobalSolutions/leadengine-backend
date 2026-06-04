"""Async database connection utilities."""

from typing import Optional

from qdrant_client import AsyncQdrantClient

from app.core.config import get_settings

settings = get_settings()


class AsyncDatabase:
    """Async database connection manager."""

    _qdrant_client: Optional[AsyncQdrantClient] = None
    _indexes_created = False

    @classmethod
    def get_qdrant_client(cls) -> AsyncQdrantClient:
        """Get async Qdrant client (singleton)."""
        if cls._qdrant_client is None:
            if settings.Qdrant_URL:
                api_key = getattr(settings, "QDRANT_API_KEY", None)
                cls._qdrant_client = AsyncQdrantClient(
                    url=settings.Qdrant_URL,
                    api_key=api_key,
                    timeout=30,
                    check_compatibility=False,  # Client 1.18.0 vs server 1.16.3 — safe to bypass
                )
            else:
                # Local Qdrant for development
                cls._qdrant_client = AsyncQdrantClient(path=settings.Qdrant_PATH, check_compatibility=False)
        return cls._qdrant_client

    @classmethod
    async def ensure_indexes(cls):
        """Create all required indexes if they don't exist."""
        if cls._indexes_created:
            return

        # No MongoDB indexes to create after removal.
        # This method will effectively become a no-op or can be removed if no other indexes are added.

        cls._indexes_created = True


def get_async_qdrant_client() -> AsyncQdrantClient:
    """Get async Qdrant client instance."""
    return AsyncDatabase.get_qdrant_client()


