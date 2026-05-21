"""Models package - exports all models."""

from app.models.conversation import Conversation, Message
from app.models.document import DocumentMetadata
from app.models.lead import Lead, LeadCreate, LeadStatus
from app.models.tenant import Tenant, TenantConfig, TenantCreate, TenantUpdate
from app.models.user import (
    Token,
    TokenData,
    User,
    UserCreate,
    UserLogin,
    UserResponse,
    UserRole,
    UserUpdate,
)

__all__ = [
    # User models
    "User",
    "UserCreate",
    "UserLogin",
    "UserResponse",
    "UserUpdate",
    "UserRole",
    "Token",
    "TokenData",
    # Tenant models
    "Tenant",
    "TenantConfig",
    "TenantCreate",
    "TenantUpdate",
    # Conversation models
    "Conversation",
    "Message",
    # Lead models
    "Lead",
    "LeadCreate",
    "LeadStatus",
    # Document models
    "DocumentMetadata",
]
