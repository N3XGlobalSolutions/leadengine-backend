from fastapi import APIRouter, Depends, Query
from app.models.user import UserResponse
from app.utils.dependencies import get_current_user, validate_tenant_access
from app.services import analytics_service

router = APIRouter(prefix="/api/analytics", tags=["Analytics"])


@router.get("/dashboard")
async def get_dashboard_metrics(
    tenant_id: str, current_user: UserResponse = Depends(get_current_user)
):
    """
    Get aggregated dashboard metrics for the specified tenant.
    Requires Tenant Admin or Platform Admin role.
    """
    await validate_tenant_access(tenant_id, current_user)

    stats = analytics_service.get_tenant_stats(tenant_id)
    return {"status": "success", "data": stats}


@router.get("/daily")
async def get_daily_metrics(
    tenant_id: str,
    days: int = Query(7, ge=1, le=90, description="Number of days to retrieve (1-90)"),
    current_user: UserResponse = Depends(get_current_user),
):
    """
    Get daily conversation metrics for the specified tenant.
    Returns conversation volume for the last N days.
    """
    await validate_tenant_access(tenant_id, current_user)

    metrics = analytics_service.get_daily_metrics(tenant_id, days)
    return {"status": "success", "data": metrics}


@router.get("/platform")
async def get_platform_metrics(current_user: UserResponse = Depends(get_current_user)):
    """
    Get aggregated metrics for the entire platform (AOB Level).
    Requires Platform Admin role.
    """
    if current_user.role not in ["platform_admin", "sales_team"]:
        from fastapi import HTTPException, status

        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient permissions to view platform stats",
        )

    stats = analytics_service.get_platform_stats()
    return {"status": "success", "data": stats}


@router.get("/platform-daily")
async def get_platform_daily_metrics(
    days: int = Query(7, ge=1, le=30, description="Number of days (1-30)"),
    current_user: UserResponse = Depends(get_current_user),
):
    """
    Get aggregated daily conversation and lead metrics across ALL tenants.
    Requires Platform Admin or Sales Team role.
    """
    if current_user.role not in ["platform_admin", "sales_team"]:
        from fastapi import HTTPException, status
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient permissions to view platform-wide stats",
        )

    from datetime import datetime, timedelta, timezone
    from app.core.dynamodb import get_item
    from app.services.tenant.tenant_service_dynamodb import tenant_service_dynamodb

    # Fetch all tenant IDs
    tenants = tenant_service_dynamodb.list_tenant_summaries(skip=0, limit=200)
    tenant_ids = [t.id for t in tenants]
    # Build date range
    today = datetime.now(timezone.utc)
    date_range = [
        (today - timedelta(days=days - i - 1)).strftime("%Y-%m-%d")
        for i in range(days)
    ]

    # Sum metrics across all tenants for each date
    result = []
    for date_str in date_range:
        total_conversations = 0
        total_leads = 0
        total_messages = 0

        for tenant_id in tenant_ids:
            item = get_item(
                pk=f"STATS#{tenant_id}",
                sk=f"DAILY#{date_str}"
            )
            if item:
                total_conversations += int(item.get("conversations", 0))
                total_leads += int(item.get("leads", 0))
                total_messages += int(item.get("messages", 0))

        # Format date for display
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        display = dt.strftime("%b %d") if days > 1 else "Today"

        result.append({
            "date": date_str,
            "display": display,
            "conversations": total_conversations,
            "leads": total_leads,
            "messages": total_messages,
        })

    # Compute today's totals as a summary header
    today_str = today.strftime("%Y-%m-%d")
    today_data = next((r for r in result if r["date"] == today_str), None)

    return {
        "status": "success",
        "data": result,
        "summary": {
            "today_conversations": today_data["conversations"] if today_data else 0,
            "today_leads": today_data["leads"] if today_data else 0,
            "period_conversations": sum(r["conversations"] for r in result),
            "period_leads": sum(r["leads"] for r in result),
        }
    }

