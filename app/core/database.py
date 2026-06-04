"""Database connection utilities."""

from qdrant_client import QdrantClient

from app.core.config import get_settings

settings = get_settings()


class Database:
    """Database connection manager."""

    _qdrant_client = None
    _indexes_created = False

    @classmethod
    def get_qdrant_client(cls):
        """Get Qdrant client (singleton).

        Supports:
        - Remote Qdrant (URL + optional API key)
        - Local Qdrant (path-based storage)
        """
        if cls._qdrant_client is None:
            if settings.Qdrant_URL:
                # Remote Qdrant with optional API key authentication
                api_key = getattr(settings, "QDRANT_API_KEY", None)
                cls._qdrant_client = QdrantClient(
                    url=settings.Qdrant_URL,
                    api_key=api_key,
                    timeout=30,  # 30 second timeout for Lambda cold starts
                    check_compatibility=False,  # Client 1.18.0 vs server 1.16.3 — safe to bypass
                )
            else:
                # Local Qdrant (for development)
                cls._qdrant_client = QdrantClient(path=settings.Qdrant_PATH, check_compatibility=False)
        return cls._qdrant_client


def get_qdrant_client():
    """Get Qdrant client instance."""
    return Database.get_qdrant_client()

