"""FastAPI dependencies for authentication and authorization."""

from typing import Optional

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.models.user import UserResponse, UserRole
from app.services import user_service
from app.utils.security import decode_token

security = HTTPBearer()


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> UserResponse:
    """Get current authenticated user from JWT token."""
    token = credentials.credentials

    # Decode token
    payload = decode_token(token)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Check token type
    if payload.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token type",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Get user from database
    user_id: str = payload.get("sub")
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
        )

    user = user_service.get_user(user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Inactive user",
        )

    return user


async def get_current_active_user(
    current_user: UserResponse = Depends(get_current_user),
) -> UserResponse:
    """Get current active user."""
    return current_user


def require_role(allowed_roles: list[UserRole]):
    """Dependency to require specific roles."""

    async def role_checker(
        current_user: UserResponse = Depends(get_current_user),
    ) -> UserResponse:
        if current_user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return current_user

    return role_checker


# Roles that have platform-level (cross-tenant) access
PLATFORM_LEVEL_ROLES = [UserRole.PLATFORM_ADMIN, UserRole.PLATFORM_OPERATOR, UserRole.SALES_TEAM]


async def validate_tenant_access(
    tenant_id: str,
    current_user: UserResponse = Depends(get_current_user),
) -> bool:
    """Validate that user has access to the tenant."""
    # Platform admins and operators can access any tenant
    if current_user.role in [UserRole.PLATFORM_ADMIN, UserRole.PLATFORM_OPERATOR]:
        return True

    # Sales team can read any tenant
    if current_user.role == UserRole.SALES_TEAM:
        return True

    # Verify the user has access to the requested tenant ID
    if tenant_id and current_user.tenant_id != tenant_id:
        if current_user.role not in PLATFORM_LEVEL_ROLES:
            raise HTTPException(
                status_code=403,
                detail="Not enough permissions to access this tenant"
            )

    return True


async def get_tenant_from_header(
    x_tenant_id: Optional[str] = Header(None, alias="X-Tenant-ID"),
) -> Optional[str]:
    """Extract tenant ID from header."""
    return x_tenant_id
