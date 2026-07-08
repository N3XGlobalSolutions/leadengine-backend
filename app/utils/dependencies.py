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


# Roles that have cross-tenant READ access
CROSS_TENANT_READ_ROLES = [
    UserRole.PLATFORM_ADMIN,
    UserRole.PLATFORM_OPERATOR,
    UserRole.SALES_TEAM,  # read-only across tenants
]

# Roles that have cross-tenant WRITE access (SALES_TEAM intentionally excluded)
CROSS_TENANT_WRITE_ROLES = [
    UserRole.PLATFORM_ADMIN,
    UserRole.PLATFORM_OPERATOR,
]

# Backwards-compatible alias (some modules import this name)
PLATFORM_LEVEL_ROLES = CROSS_TENANT_READ_ROLES


async def validate_tenant_access(
    tenant_id: str,
    current_user: UserResponse = Depends(get_current_user),
) -> bool:
    """Validate that a user has READ access to the tenant.

    Cross-tenant read is allowed for platform admins/operators and the sales
    team. Everyone else is confined to their own tenant.
    """
    if current_user.role in CROSS_TENANT_READ_ROLES:
        return True

    if tenant_id and current_user.tenant_id != tenant_id:
        raise HTTPException(
            status_code=403,
            detail="Not enough permissions to access this tenant",
        )

    return True


async def validate_tenant_write_access(
    tenant_id: str,
    current_user: UserResponse = Depends(get_current_user),
) -> bool:
    """Validate that a user has WRITE access to the tenant.

    Stricter than read access: the sales team (read-only) is NOT permitted to
    mutate other tenants' data. Only platform admins/operators, or a user acting
    within their own tenant, may write.
    """
    if current_user.role in CROSS_TENANT_WRITE_ROLES:
        return True

    if tenant_id and current_user.tenant_id != tenant_id:
        raise HTTPException(
            status_code=403,
            detail="Not enough permissions to modify this tenant's data",
        )

    return True


async def get_tenant_from_header(
    x_tenant_id: Optional[str] = Header(None, alias="X-Tenant-ID"),
) -> Optional[str]:
    """Extract tenant ID from header."""
    return x_tenant_id
