"""Outbound campaign models."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

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
    # "template" = approved WhatsApp template (reaches anyone); "text" = free-form text
    # (only delivered to people who messaged the business in the last 24 hours).
    message_type: str = "text"
    message_template: str = ""
    template_name: Optional[str] = None
    template_language: Optional[str] = None
    template_body: str = ""            # template BODY text with {{1}}.. placeholders (preview/context)
    template_params: List[str] = Field(default_factory=list)  # per {{n}}: "{{name}}", "{{phone}}" or static text
    batch_size: int = 10
    message_delay_seconds: int = 5
    batch_delay_seconds: int = 60
    total_leads: int = 0
    sent_count: int = 0
    failed_count: int = 0
    delivered_count: int = 0
    read_count: int = 0
    response_count: int = 0           # leads who replied at least once
    skipped_count: int = 0            # opted-out numbers that were not messaged
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
    message_type: str = Field(default="text", pattern="^(text|template)$")
    message_template: str = ""
    template_name: Optional[str] = None
    template_language: Optional[str] = None
    template_body: str = ""
    template_params: List[str] = Field(default_factory=list)
    batch_size: int = Field(default=10, ge=1, le=100)
    message_delay_seconds: int = Field(default=5, ge=1, le=60)
    batch_delay_seconds: int = Field(default=60, ge=10, le=600)


class CampaignUpdate(BaseModel):
    """Payload for updating a campaign."""

    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    message_type: Optional[str] = Field(default=None, pattern="^(text|template)$")
    message_template: Optional[str] = None
    template_name: Optional[str] = None
    template_language: Optional[str] = None
    template_body: Optional[str] = None
    template_params: Optional[List[str]] = None
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
    messages_delivered: int = 0
    messages_read: int = 0
    replies: int = 0
    skipped_opted_out: int = 0
    delivery_rate: float = 0.0   # delivered / sent
    read_rate: float = 0.0       # read / delivered
    response_rate: float = 0.0   # replied / sent
