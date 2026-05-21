"""Document metadata models."""

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class DocumentMetadata(BaseModel):
    """Metadata for ingested documents."""

    source: str
    tenant_id: str
    document_type: str  # "docx", "pdf", "url"
    uploaded_at: datetime = Field(default_factory=datetime.now(timezone.utc))
    chunk_count: int = 0
    extra_info: Optional[Dict[str, Any]] = None
