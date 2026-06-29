"""Campaign audit log models."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class CampaignAuditAction(str, Enum):
    """Enumeration of campaign audit actions."""

    CAMPAIGN_CREATED = "CAMPAIGN_CREATED"
    CAMPAIGN_UPDATED = "CAMPAIGN_UPDATED"
    LEADS_IMPORTED = "LEADS_IMPORTED"
    LEADS_DELETED = "LEADS_DELETED"
    LEAD_EDITED = "LEAD_EDITED"
    CAMPAIGN_STARTED = "CAMPAIGN_STARTED"
    CAMPAIGN_PAUSED = "CAMPAIGN_PAUSED"
    CAMPAIGN_RESUMED = "CAMPAIGN_RESUMED"
    CAMPAIGN_COMPLETED = "CAMPAIGN_COMPLETED"
    CAMPAIGN_FAILED = "CAMPAIGN_FAILED"
    CAMPAIGN_ARCHIVED = "CAMPAIGN_ARCHIVED"
    CAMPAIGN_DELETED = "CAMPAIGN_DELETED"


class CampaignAuditLog(BaseModel):
    """A single audit log entry for a campaign."""

    log_id: str
    campaign_id: str
    tenant_id: str
    action: CampaignAuditAction
    user_id: Optional[str] = None
    user_name: Optional[str] = None
    description: str = ""
    metadata: Optional[Dict[str, Any]] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class CampaignAuditCreate(BaseModel):
    """Payload for creating a campaign audit log entry."""

    campaign_id: str
    tenant_id: str
    action: CampaignAuditAction
    user_id: Optional[str] = None
    user_name: Optional[str] = None
    description: str = ""
    metadata: Optional[Dict[str, Any]] = None
