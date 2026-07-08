"""User management routes."""

from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, status, Body

from app.core.logging_config import get_logger
from app.models.user import UserCreate, UserResponse, UserRole, UserUpdate
from app.services import user_service, audit_service
from app.models.audit import AuditLogCreate
from app.utils.dependencies import get_current_user, require_role

logger = get_logger(__name__)

router = APIRouter(prefix="/api/users", tags=["Users"])

# Roles that grant cross-tenant / platform-level authority. Only a PLATFORM_ADMIN
# may create or assign these, preventing a PLATFORM_OPERATOR from escalating to admin.
PLATFORM_LEVEL_ROLES = {UserRole.PLATFORM_ADMIN, UserRole.PLATFORM_OPERATOR}


def _assert_can_assign_role(actor: UserResponse, target_role: UserRole) -> None:
    """Reject attempts to grant a higher privilege than the actor may delegate."""
    if target_role in PLATFORM_LEVEL_ROLES and actor.role != UserRole.PLATFORM_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only platform admins can assign platform-level roles",
        )


@router.post(
    "",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_role([UserRole.PLATFORM_ADMIN, UserRole.PLATFORM_OPERATOR]))],
)
async def create_user(
    user_data: UserCreate,
    current_user: UserResponse = Depends(get_current_user)
):
    """Create a new user (admin only)."""
    # Prevent privilege escalation: an operator cannot mint platform-level users.
    _assert_can_assign_role(current_user, user_data.role)

    try:
        new_user = user_service.create_user(user_data)

        try:
            audit_service.log_activity(
                AuditLogCreate(
                    action="USER_CREATED",
                    user_id=current_user.user_id,
                    user_name=current_user.full_name,
                    user_role=current_user.role,
                    tenant_id=current_user.tenant_id,
                    description=f"Created user {new_user.email} with role {new_user.role}"
                )
            )
        except Exception:
            logger.warning("Audit log failed", exc_info=True)

        return new_user
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except HTTPException:
        raise
    except Exception:
        logger.error("User creation failed", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="User creation failed. Please try again later.",
        )


@router.get("/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: str, current_user: UserResponse = Depends(get_current_user)
):
    """Get a user by ID."""
    # Users can view their own profile, admins and operators can view anyone
    if current_user.user_id != user_id and current_user.role not in [
        UserRole.PLATFORM_ADMIN, UserRole.PLATFORM_OPERATOR
    ]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Access denied"
        )

    user = user_service.get_user(user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )
    return user


@router.get("", response_model=List[UserResponse])
async def list_users(
    skip: int = 0,
    limit: int = Query(1000, le=1000),
    current_user: UserResponse = Depends(get_current_user),
):
    """List users (filtered by tenant for non-admins)."""
    # Admins and operators can see all users, others only users in their tenant
    tenant_id = None
    if current_user.role not in [UserRole.PLATFORM_ADMIN, UserRole.PLATFORM_OPERATOR]:
        tenant_id = current_user.tenant_id

    return user_service.list_users(tenant_id=tenant_id, skip=skip, limit=limit)


@router.put("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: str,
    update_data: UserUpdate,
    current_user: UserResponse = Depends(get_current_user),
):
    """Update a user."""
    # Users can update their own profile (except role), admins/operators can update anyone
    if current_user.user_id != user_id and current_user.role not in [
        UserRole.PLATFORM_ADMIN, UserRole.PLATFORM_OPERATOR
    ]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Access denied"
        )

    # Non-admins/operators cannot change roles at all
    if update_data.role is not None:
        if current_user.role not in [UserRole.PLATFORM_ADMIN, UserRole.PLATFORM_OPERATOR]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Cannot change roles"
            )
        # And an operator cannot escalate anyone to a platform-level role.
        _assert_can_assign_role(current_user, update_data.role)

    user = user_service.update_user(user_id, update_data)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )
        
    try:
        audit_service.log_activity(
            AuditLogCreate(
                action="USER_UPDATED",
                user_id=current_user.user_id,
                user_name=current_user.full_name,
                user_role=current_user.role,
                tenant_id=current_user.tenant_id,
                description=f"Updated user {user.email}"
            )
        )
    except Exception:
        logger.warning("Audit log failed", exc_info=True)
        
    return user


@router.post(
    "/{user_id}/reset-password",
    dependencies=[Depends(require_role([UserRole.PLATFORM_ADMIN]))],
)
async def reset_user_password(
    user_id: str, 
    new_password: str = Body(..., media_type="text/plain"),
    current_user: UserResponse = Depends(get_current_user)
):
    """Reset a user's password (admin only)."""
    success = user_service.reset_user_password(user_id, new_password)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )
        
    try:
        audit_service.log_activity(
            AuditLogCreate(
                action="PASSWORD_RESET",
                user_id=current_user.user_id,
                user_name=current_user.full_name,
                user_role=current_user.role,
                tenant_id=current_user.tenant_id,
                description=f"Reset password for user {user_id}"
            )
        )
    except Exception:
        logger.warning("Audit log failed", exc_info=True)
        
    return {"message": "Password reset successfully"}


@router.delete(
    "/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_role([UserRole.PLATFORM_ADMIN]))],
)
async def delete_user(
    user_id: str,
    current_user: UserResponse = Depends(get_current_user)
):
    """Delete a user (admin only)."""
    success = user_service.delete_user(user_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )
        
    try:
        audit_service.log_activity(
            AuditLogCreate(
                action="USER_DELETED",
                user_id=current_user.user_id,
                user_name=current_user.full_name,
                user_role=current_user.role,
                tenant_id=current_user.tenant_id,
                description=f"Deleted user {user_id}"
            )
        )
    except Exception:
        logger.warning("Audit log failed", exc_info=True)
