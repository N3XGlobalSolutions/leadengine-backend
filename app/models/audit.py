"""Audit log models."""

from datetime import datetime, timezone
from typing import Optional
from pydantic import BaseModel, Field

class AuditLog(BaseModel):
    """Audit log model for tracking system-wide activity."""
    
    log_id: str
    action: str
    user_id: str
    user_name: str
    user_role: str
    tenant_id: Optional[str] = None
    description: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    ip_address: Optional[str] = None

class AuditLogCreate(BaseModel):
    """Payload for creating a new audit log."""
    
    action: str
    user_id: str
    user_name: str
    user_role: str
    tenant_id: Optional[str] = None
    description: str
    ip_address: Optional[str] = None
