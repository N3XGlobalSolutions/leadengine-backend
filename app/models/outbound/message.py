"""Outbound message tracking models."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class OutboundMessageStatus(str, Enum):
    """Message delivery tracking status."""

    PENDING = "PENDING"
    QUEUED = "QUEUED"
    SENDING = "SENDING"
    SENT = "SENT"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"


class OutboundMessage(BaseModel):
    """Tracks a single message sent to a lead."""

    message_id: str
    campaign_id: str
    lead_id: str
    tenant_id: str
    phone: str
    rendered_message: str
    status: OutboundMessageStatus = OutboundMessageStatus.PENDING
    provider: str = "telegram"  # telegram for now, whatsapp later
    provider_message_id: Optional[str] = None
    error_message: Optional[str] = None
    retry_count: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    sent_at: Optional[datetime] = None
    delivered_at: Optional[datetime] = None
