"""Outbound campaign models."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class CampaignStatus(str, Enum):
    """Campaign lifecycle statuses."""

    DRAFT = "DRAFT"
    READY = "READY"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    ARCHIVED = "ARCHIVED"


# Valid status transitions
VALID_TRANSITIONS: Dict[CampaignStatus, list[CampaignStatus]] = {
    CampaignStatus.DRAFT: [CampaignStatus.READY, CampaignStatus.ARCHIVED],
    CampaignStatus.READY: [CampaignStatus.RUNNING, CampaignStatus.DRAFT, CampaignStatus.ARCHIVED],
    CampaignStatus.RUNNING: [CampaignStatus.PAUSED, CampaignStatus.COMPLETED, CampaignStatus.FAILED],
    CampaignStatus.PAUSED: [CampaignStatus.RUNNING, CampaignStatus.ARCHIVED],
    CampaignStatus.COMPLETED: [CampaignStatus.ARCHIVED],
    CampaignStatus.FAILED: [CampaignStatus.DRAFT, CampaignStatus.ARCHIVED],
    CampaignStatus.ARCHIVED: [],
}


class Campaign(BaseModel):
    """Complete campaign model."""

    campaign_id: str
    tenant_id: str
    name: str
    status: CampaignStatus = CampaignStatus.DRAFT
    message_template: str = ""
    batch_size: int = 10
    message_delay_seconds: int = 5
    batch_delay_seconds: int = 60
    total_leads: int = 0
    sent_count: int = 0
    failed_count: int = 0
    delivered_count: int = 0
    response_count: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    created_by: Optional[str] = None
    s3_file_key: Optional[str] = None
    original_filename: Optional[str] = None


class CampaignCreate(BaseModel):
    """Payload for creating a new campaign."""

    name: str = Field(..., min_length=1, max_length=200)
    message_template: str = ""
    batch_size: int = Field(default=10, ge=1, le=100)
    message_delay_seconds: int = Field(default=5, ge=1, le=60)
    batch_delay_seconds: int = Field(default=60, ge=10, le=600)


class CampaignUpdate(BaseModel):
    """Payload for updating a campaign."""

    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    message_template: Optional[str] = None
    batch_size: Optional[int] = Field(default=None, ge=1, le=100)
    message_delay_seconds: Optional[int] = Field(default=None, ge=1, le=60)
    batch_delay_seconds: Optional[int] = Field(default=None, ge=10, le=600)


class CampaignSummaryStats(BaseModel):
    """Dashboard-level summary statistics for outbound campaigns."""

    total_campaigns: int = 0
    running_campaigns: int = 0
    total_leads: int = 0
    messages_sent: int = 0
    messages_failed: int = 0
    response_rate: float = 0.0
