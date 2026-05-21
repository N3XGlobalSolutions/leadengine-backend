"""User models for authentication and authorization."""

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, EmailStr, Field


class UserRole(str, Enum):
    """User roles for role-based access control."""

    PLATFORM_ADMIN = "platform_admin"
    PLATFORM_OPERATOR = "platform_operator"
    CLIENT_ADMIN = "client_admin"
    SALES_TEAM = "sales_team"


class UserBase(BaseModel):
    """Base user model with common fields."""

    email: EmailStr
    full_name: str
    role: UserRole = UserRole.CLIENT_ADMIN
    tenant_id: Optional[str] = None  # None for platform_admin


class UserCreate(UserBase):
    """Model for creating a new user."""

    password: str = Field(..., min_length=8)


class UserUpdate(BaseModel):
    """Model for updating user information."""

    email: Optional[EmailStr] = None
    full_name: Optional[str] = None
    role: Optional[UserRole] = None
    is_active: Optional[bool] = None


class User(UserBase):
    """Complete user model with all fields."""

    user_id: str
    hashed_password: str
    created_at: datetime = Field(default_factory=datetime.now(timezone.utc))
    is_active: bool = True
    is_deleted: bool = False  # Soft delete flag
    deleted_at: Optional[datetime] = None  # Soft delete timestamp
    last_login: Optional[datetime] = None


class UserResponse(UserBase):
    """User response model (without password)."""

    user_id: str
    created_at: datetime
    is_active: bool
    last_login: Optional[datetime] = None


class UserLogin(BaseModel):
    """Model for user login request."""

    email: EmailStr
    password: str


class Token(BaseModel):
    """JWT token response."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class TokenData(BaseModel):
    """Data stored in JWT token."""

    user_id: Optional[str] = None
    email: Optional[str] = None
    role: Optional[str] = None
    tenant_id: Optional[str] = None
