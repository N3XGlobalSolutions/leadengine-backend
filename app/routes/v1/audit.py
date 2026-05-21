"""Audit log routes."""

from typing import List, Optional

from fastapi import APIRouter, Depends, Query, status

from app.models.audit import AuditLog
from app.models.user import UserResponse, UserRole
from app.services import audit_service
from app.utils.dependencies import get_current_user, require_role

router = APIRouter(prefix="/api/audit-logs", tags=["Audit Logs"])


@router.get(
    "",
    response_model=List[AuditLog],
    dependencies=[Depends(require_role([UserRole.PLATFORM_ADMIN]))],
)
async def list_audit_logs(
    skip: int = 0,
    limit: int = Query(100, le=1000),
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    current_user: UserResponse = Depends(get_current_user),
):
    """List all system audit logs (platform admin only)."""
    return audit_service.list_audit_logs(
        skip=skip, limit=limit, start_date=start_date, end_date=end_date
    )
