"""Encryption utilities for protecting sensitive credentials."""

import os

from cryptography.fernet import Fernet
from app.core.config import get_settings
from app.core.logging_config import get_logger

logger = get_logger(__name__)
settings = get_settings()

_is_dev = settings.APP_ENV == "development"

# Initialize Fernet key
_key = getattr(settings, "ENCRYPTION_KEY", None) or os.getenv("ENCRYPTION_KEY")

if not _key:
    if not _is_dev:
        # Fail fast outside development: a per-process ephemeral key would make
        # data encrypted by one instance undecryptable by another (silent data loss).
        raise RuntimeError(
            "ENCRYPTION_KEY is required when APP_ENV is not 'development'."
        )
    logger.warning("ENCRYPTION_KEY not set! Generating a temporary DEV-ONLY fallback key.")
    _key = Fernet.generate_key().decode()

try:
    fernet = Fernet(_key.encode() if isinstance(_key, str) else _key)
except Exception as e:
    if not _is_dev:
        raise RuntimeError(f"Invalid ENCRYPTION_KEY: {e}") from e
    logger.error(f"Invalid ENCRYPTION_KEY: {e}. Generating a temporary DEV-ONLY fallback key.")
    fernet = Fernet(Fernet.generate_key())


def encrypt_secret(value: str) -> str:
    """Encrypt a sensitive string value using Fernet."""
    if not value:
        return ""
    try:
        return fernet.encrypt(value.encode()).decode()
    except Exception as e:
        logger.error(f"Failed to encrypt secret: {e}")
        raise


def decrypt_secret(value: str) -> str:
    """Decrypt a Fernet-encrypted string value."""
    if not value:
        return ""
    try:
        return fernet.decrypt(value.encode() if isinstance(value, str) else value).decode()
    except Exception as e:
        # We must never log the token/secret or the raw error if it contains it
        logger.error("Failed to decrypt secret")
        raise


def is_encrypted(value: str) -> bool:
    """Check if a string is already encrypted with the current Fernet key."""
    if not value:
        return False
    try:
        decrypt_secret(value)
        return True
    except Exception:
        return False
