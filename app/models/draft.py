"""Draft models for autosave functionality.

Supports server-side draft persistence for client creation and client settings editing.
Drafts are isolated per authenticated user and automatically expire after 7 days.
"""

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class DraftCreate(BaseModel):
    """Payload for creating a new draft."""

    draft_type: str = "client_creation"
    draft_data: Dict[str, Any] = Field(
        default_factory=dict,
        description="Form data to persist. Sensitive fields (e.g. whatsapp_access_token) are stripped before storage.",
    )


class DraftUpdate(BaseModel):
    """Payload for updating an existing draft."""

    draft_data: Dict[str, Any] = Field(
        ...,
        description="Updated form data. Sensitive fields are stripped before storage.",
    )


class DraftResponse(BaseModel):
    """Draft response returned to the client."""

    draft_id: str
    user_id: str
    draft_type: str
    draft_data: Dict[str, Any]
    created_at: str
    updated_at: str
    expires_at: int  # Unix epoch seconds (used for DynamoDB TTL)
