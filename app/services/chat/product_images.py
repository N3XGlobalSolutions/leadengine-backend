"""Chat Products — image validation and S3 storage helpers for chat product cards.

Images are stored in S3 at chat-products/{tenant_id}/{image_id}, where
image_id = "<32 lowercase hex>.<png|jpg|webp>". The storage object is passed in
(StorageService in the app, a mock in unit tests).
"""

import io
import logging
import uuid
from typing import Any, Optional

from botocore.exceptions import ClientError

from app.models.tenant import is_valid_chat_product_image_id

# Plain stdlib logger (app.core.logging_config.get_logger is a thin wrapper around it);
# avoids loading app settings so this module stays importable in unit tests.
logger = logging.getLogger(__name__)

CHAT_PRODUCT_IMAGE_MAX_BYTES = 2 * 1024 * 1024  # 2 MB
CHAT_PRODUCT_IMAGE_URL_EXPIRY_SECONDS = 3600  # presigned GET URL lifetime (1 h)

# Allowed upload content types -> stored file extension
CHAT_PRODUCT_IMAGE_TYPES = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
}
_EXTENSION_CONTENT_TYPES = {ext: content_type for content_type, ext in CHAT_PRODUCT_IMAGE_TYPES.items()}

# S3 HeadObject codes meaning "no such object" (403 when the role lacks s3:ListBucket)
_MISSING_OBJECT_ERROR_CODES = {"404", "NoSuchKey", "NotFound", "403", "AccessDenied", "Forbidden"}


class ChatProductImageError(ValueError):
    """Invalid chat product image upload (maps to HTTP 400)."""


def sniff_image_extension(data: bytes) -> Optional[str]:
    """Detect png / jpg / webp from the file's magic bytes."""
    if not data:
        return None
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def validate_chat_product_image(content: bytes, content_type: Optional[str]) -> str:
    """Validate an upload (content type, size, magic bytes) and return its file extension.

    Raises ChatProductImageError with a user-facing message when invalid.
    """
    declared_type = (content_type or "").split(";", 1)[0].strip().lower()
    extension = CHAT_PRODUCT_IMAGE_TYPES.get(declared_type)
    if not extension:
        raise ChatProductImageError("Unsupported image type. Allowed: PNG, JPEG, WebP")
    if not content:
        raise ChatProductImageError("File is empty")
    if len(content) > CHAT_PRODUCT_IMAGE_MAX_BYTES:
        raise ChatProductImageError(
            f"Image too large. Maximum size: {CHAT_PRODUCT_IMAGE_MAX_BYTES // (1024 * 1024)} MB"
        )
    if sniff_image_extension(content) != extension:
        raise ChatProductImageError("File content is not a valid PNG, JPEG or WebP image")
    return extension


def chat_product_image_key(tenant_id: str, image_id: str) -> str:
    """S3 key for a chat product image."""
    return f"chat-products/{tenant_id}/{image_id}"


def store_chat_product_image(storage: Any, tenant_id: str, content: bytes, content_type: Optional[str]) -> str:
    """Validate and upload a chat product image; returns the new image_id.

    Raises ChatProductImageError for invalid uploads; storage errors propagate.
    """
    extension = validate_chat_product_image(content, content_type)
    image_id = f"{uuid.uuid4().hex}.{extension}"
    storage.upload_file(
        io.BytesIO(content),
        chat_product_image_key(tenant_id, image_id),
        content_type=_EXTENSION_CONTENT_TYPES[extension],
    )
    return image_id


def get_chat_product_image_url(storage: Any, tenant_id: str, image_id: str) -> Optional[str]:
    """Presigned GET URL (1 h) for an existing chat product image.

    Returns None when the image_id is invalid or the object doesn't exist.
    Raises for storage misconfiguration or unexpected S3 errors.
    """
    if not is_valid_chat_product_image_id(image_id):
        return None

    bucket_name = getattr(storage, "bucket_name", None)
    if not bucket_name:
        raise RuntimeError("S3 bucket is not configured")

    key = chat_product_image_key(tenant_id, image_id)
    try:
        storage.s3_client.head_object(Bucket=bucket_name, Key=key)
    except ClientError as e:
        error_code = str(e.response.get("Error", {}).get("Code", ""))
        if error_code in _MISSING_OBJECT_ERROR_CODES:
            logger.info(f"Chat product image not found: {key} ({error_code})")
            return None
        raise

    url = storage.generate_presigned_url(key, expiration=CHAT_PRODUCT_IMAGE_URL_EXPIRY_SECONDS)
    return url or None
