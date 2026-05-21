"""Lead models for lead management."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class LeadStatus(str, Enum):
    """Lead status enumeration."""

    NEW = "new"  # Incomplete data (missing phone)
    QUALIFIED = "qualified"  # Meets LQC criteria
    DISQUALIFIED = "disqualified"  # Failed LQC criteria


class Lead(BaseModel):
    """Complete lead model."""

    lead_id: str
    tenant_id: str
    conversation_id: str
    name: Optional[str] = None
    phone: Optional[str] = None
    message: Optional[str] = None
    status: LeadStatus = LeadStatus.NEW
    created_at: datetime = Field(default_factory=datetime.now(timezone.utc))
    metadata: Optional[Dict[str, Any]] = None


class LeadCreate(BaseModel):
    """Model for creating a new lead."""

    tenant_id: str
    conversation_id: str
    name: Optional[str] = None
    phone: Optional[str] = None
    message: Optional[str] = None
    status: Optional[LeadStatus] = None  # Allow specifying status on creation
    metadata: Optional[Dict[str, Any]] = None

