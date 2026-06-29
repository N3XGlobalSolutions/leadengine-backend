"""Encryption utilities for protecting sensitive credentials."""

from cryptography.fernet import Fernet
from app.core.config import get_settings
from app.core.logging_config import get_logger

logger = get_logger(__name__)
settings = get_settings()

# Initialize Fernet key
_key = getattr(settings, "ENCRYPTION_KEY", None)

if not _key:
    import os
    _key = os.getenv("ENCRYPTION_KEY")

if not _key:
    logger.warning("ENCRYPTION_KEY not set in configuration! Generating a temporary fallback key.")
    _key = Fernet.generate_key().decode()

try:
    fernet = Fernet(_key.encode() if isinstance(_key, str) else _key)
except Exception as e:
    logger.error(f"Invalid ENCRYPTION_KEY: {e}. Generating a temporary fallback key.")
    fallback_key = Fernet.generate_key()
    fernet = Fernet(fallback_key)


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
