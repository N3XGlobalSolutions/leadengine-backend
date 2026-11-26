"""Outbound lead models for campaign leads."""

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class OutboundLeadStatus(str, Enum):
    """Lead validation status after file upload."""

    VALID = "VALID"
    INVALID = "INVALID"
    DUPLICATE = "DUPLICATE"


class MessageStatus(str, Enum):
    """Message delivery status for a lead."""

    PENDING = "PENDING"
    QUEUED = "QUEUED"
    SENDING = "SENDING"
    SENT = "SENT"
    DELIVERED = "DELIVERED"
    READ = "READ"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"  # not messaged (e.g. the number opted out)


class OutboundLead(BaseModel):
    """A lead within an outbound campaign."""

    lead_id: str
    campaign_id: str
    tenant_id: str
    name: Optional[str] = None
    phone: str
    raw_phone: Optional[str] = None
    status: OutboundLeadStatus = OutboundLeadStatus.VALID
    message_status: MessageStatus = MessageStatus.PENDING
    error_reason: Optional[str] = None
    retry_count: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    sent_at: Optional[datetime] = None
    delivered_at: Optional[datetime] = None
    read_at: Optional[datetime] = None
    replied_at: Optional[datetime] = None


class OutboundLeadCreate(BaseModel):
    """Internal model for creating an outbound lead."""

    name: Optional[str] = None
    phone: str
    raw_phone: Optional[str] = None
    status: OutboundLeadStatus = OutboundLeadStatus.VALID
    error_reason: Optional[str] = None


class OutboundLeadUpdate(BaseModel):
    """Payload for editing a lead."""

    name: Optional[str] = None
    phone: Optional[str] = None


class UploadSummary(BaseModel):
    """Summary returned after processing a lead file upload."""

    total_uploaded: int = 0
    valid: int = 0
    invalid: int = 0
    duplicates: int = 0
    detected_name_column: Optional[str] = None
    detected_phone_column: Optional[str] = None
    s3_file_key: Optional[str] = None
    original_filename: Optional[str] = None
