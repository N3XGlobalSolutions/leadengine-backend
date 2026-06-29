"""Outbound campaign REST API routes."""

import asyncio
import csv
import io
import json
from typing import List, Optional

import boto3
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status, BackgroundTasks
from starlette.responses import StreamingResponse

from app.core.config import get_settings
from app.core.logging_config import get_logger
from app.models.outbound.campaign import (
    Campaign,
    CampaignCreate,
    CampaignUpdate,
    CampaignStatus,
    CampaignSummaryStats,
)
from app.models.outbound.outbound_lead import (
    OutboundLead,
    OutboundLeadUpdate,
    UploadSummary,
    MessageStatus,
)
from app.models.outbound.audit import (
    CampaignAuditAction,
    CampaignAuditCreate,
    CampaignAuditLog,
)
from app.models.user import UserResponse
from app.services.outbound.campaign_service import campaign_service
from app.services.outbound.outbound_lead_service import outbound_lead_service
from app.services.outbound.campaign_audit_service import campaign_audit_service
from app.services.outbound.campaign_executor_service import campaign_executor_service
from app.utils.dependencies import get_current_user, validate_tenant_access

logger = get_logger(__name__)
settings = get_settings()

router = APIRouter(prefix="/api/outbound", tags=["Outbound Campaigns"])

# --- Allowed file extensions & max size ---
ALLOWED_EXTENSIONS = {"csv", "xlsx", "xls", "txt"}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB


# ═══════════════════════════════════════════════════════════════════════
# Dashboard
# ═══════════════════════════════════════════════════════════════════════

@router.get("/{tenant_id}/dashboard", response_model=CampaignSummaryStats)
async def get_outbound_dashboard(
    tenant_id: str,
    current_user: UserResponse = Depends(get_current_user),
):
    """Get outbound dashboard summary statistics for a tenant."""
    await validate_tenant_access(tenant_id, current_user)
    return campaign_service.get_dashboard_stats(tenant_id)


# ═══════════════════════════════════════════════════════════════════════
# Campaign CRUD
# ═══════════════════════════════════════════════════════════════════════

@router.post("/{tenant_id}/campaigns", response_model=Campaign, status_code=status.HTTP_201_CREATED)
async def create_campaign(
    tenant_id: str,
    data: CampaignCreate,
    current_user: UserResponse = Depends(get_current_user),
):
    """Create a new outbound campaign in DRAFT status."""
    await validate_tenant_access(tenant_id, current_user)

    campaign = campaign_service.create_campaign(
        tenant_id=tenant_id,
        data=data,
        user_id=current_user.user_id,
        user_name=current_user.full_name,
    )

    # Audit log
    campaign_audit_service.log_action(CampaignAuditCreate(
        campaign_id=campaign.campaign_id,
        tenant_id=tenant_id,
        action=CampaignAuditAction.CAMPAIGN_CREATED,
        user_id=current_user.user_id,
        user_name=current_user.full_name,
        description=f"Campaign '{data.name}' created",
    ))

    return campaign


@router.get("/{tenant_id}/campaigns", response_model=List[Campaign])
async def list_campaigns(
    tenant_id: str,
    status_filter: Optional[CampaignStatus] = Query(None, alias="status"),
    limit: int = Query(100, ge=1, le=500),
    current_user: UserResponse = Depends(get_current_user),
):
    """List all outbound campaigns for a tenant."""
    await validate_tenant_access(tenant_id, current_user)
    return campaign_service.list_campaigns(tenant_id, status=status_filter, limit=limit)


@router.get("/{tenant_id}/campaigns/{campaign_id}", response_model=Campaign)
async def get_campaign(
    tenant_id: str,
    campaign_id: str,
    current_user: UserResponse = Depends(get_current_user),
):
    """Get a single campaign by ID."""
    await validate_tenant_access(tenant_id, current_user)
    campaign = campaign_service.get_campaign(campaign_id)

    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if campaign.tenant_id != tenant_id:
        raise HTTPException(status_code=403, detail="Campaign does not belong to this tenant")

    return campaign


@router.put("/{tenant_id}/campaigns/{campaign_id}", response_model=Campaign)
async def update_campaign(
    tenant_id: str,
    campaign_id: str,
    data: CampaignUpdate,
    current_user: UserResponse = Depends(get_current_user),
):
    """Update a campaign (only when DRAFT or READY)."""
    await validate_tenant_access(tenant_id, current_user)

    campaign = campaign_service.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if campaign.tenant_id != tenant_id:
        raise HTTPException(status_code=403, detail="Campaign does not belong to this tenant")

    try:
        updated = campaign_service.update_campaign(campaign_id, data)
        campaign_audit_service.log_action(CampaignAuditCreate(
            campaign_id=campaign_id,
            tenant_id=tenant_id,
            action=CampaignAuditAction.CAMPAIGN_UPDATED,
            user_id=current_user.user_id,
            user_name=current_user.full_name,
            description="Campaign settings updated",
        ))
        return updated
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{tenant_id}/campaigns/{campaign_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_campaign(
    tenant_id: str,
    campaign_id: str,
    current_user: UserResponse = Depends(get_current_user),
):
    """Delete a campaign (only DRAFT or ARCHIVED)."""
    await validate_tenant_access(tenant_id, current_user)

    campaign = campaign_service.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if campaign.tenant_id != tenant_id:
        raise HTTPException(status_code=403, detail="Campaign does not belong to this tenant")

    try:
        campaign_service.delete_campaign(campaign_id)
        campaign_audit_service.log_action(CampaignAuditCreate(
            campaign_id=campaign_id,
            tenant_id=tenant_id,
            action=CampaignAuditAction.CAMPAIGN_DELETED,
            user_id=current_user.user_id,
            user_name=current_user.full_name,
            description=f"Campaign '{campaign.name}' deleted",
        ))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ═══════════════════════════════════════════════════════════════════════
# Lead Upload & Management
# ═══════════════════════════════════════════════════════════════════════

@router.post("/{tenant_id}/campaigns/{campaign_id}/upload")
async def upload_leads(
    tenant_id: str,
    campaign_id: str,
    file: UploadFile = File(...),
    current_user: UserResponse = Depends(get_current_user),
):
    """Upload a lead file (CSV, XLSX, TXT) for a campaign."""
    await validate_tenant_access(tenant_id, current_user)

    campaign = campaign_service.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if campaign.tenant_id != tenant_id:
        raise HTTPException(status_code=403, detail="Campaign does not belong to this tenant")
    if campaign.status not in (CampaignStatus.DRAFT, CampaignStatus.READY):
        raise HTTPException(status_code=400, detail="Can only upload leads for DRAFT or READY campaigns")

    # Validate file
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided")

    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: .{ext}. Allowed: {', '.join(ALLOWED_EXTENSIONS)}",
        )

    # Read file content
    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail=f"File too large. Maximum size: {MAX_FILE_SIZE // (1024*1024)}MB")
    if len(content) == 0:
        raise HTTPException(status_code=400, detail="File is empty")

    # Store file in S3
    s3_key = f"outbound/{tenant_id}/{campaign_id}/{file.filename}"
    try:
        s3_client = boto3.client("s3", region_name=settings.AWS_REGION)
        s3_client.put_object(
            Bucket=settings.EFFECTIVE_S3_BUCKET,
            Key=s3_key,
            Body=content,
            ContentType=file.content_type or "application/octet-stream",
        )
    except Exception as e:
        logger.error(f"S3 upload failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to store file")

    # Process the file
    try:
        summary, invalid_leads, duplicate_leads = outbound_lead_service.process_upload(
            campaign_id=campaign_id,
            tenant_id=tenant_id,
            filename=file.filename,
            file_content=content,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ImportError as e:
        raise HTTPException(status_code=500, detail=str(e))

    # Update campaign metadata
    summary.s3_file_key = s3_key
    lead_count = outbound_lead_service.count_leads(campaign_id)
    campaign_service.update_lead_count(campaign_id, lead_count)

    # Store invalid/duplicate CSVs in S3 for download
    if invalid_leads:
        invalid_csv = outbound_lead_service.generate_invalid_csv(invalid_leads)
        s3_client.put_object(
            Bucket=settings.EFFECTIVE_S3_BUCKET,
            Key=f"outbound/{tenant_id}/{campaign_id}/invalid_leads.csv",
            Body=invalid_csv.encode("utf-8"),
            ContentType="text/csv",
        )

    if duplicate_leads:
        dup_csv = outbound_lead_service.generate_duplicate_csv(duplicate_leads)
        s3_client.put_object(
            Bucket=settings.EFFECTIVE_S3_BUCKET,
            Key=f"outbound/{tenant_id}/{campaign_id}/duplicate_leads.csv",
            Body=dup_csv.encode("utf-8"),
            ContentType="text/csv",
        )

    # Transition to READY if leads were uploaded and template exists
    campaign = campaign_service.get_campaign(campaign_id)
    if campaign and campaign.message_template and lead_count > 0 and campaign.status == CampaignStatus.DRAFT:
        campaign_service.update_campaign_status(campaign_id, CampaignStatus.READY)

    # Audit log
    campaign_audit_service.log_action(CampaignAuditCreate(
        campaign_id=campaign_id,
        tenant_id=tenant_id,
        action=CampaignAuditAction.LEADS_IMPORTED,
        user_id=current_user.user_id,
        user_name=current_user.full_name,
        description=f"Uploaded {file.filename}: {summary.valid} valid, {summary.invalid} invalid, {summary.duplicates} duplicates",
        metadata={
            "filename": file.filename,
            "total_uploaded": summary.total_uploaded,
            "valid": summary.valid,
            "invalid": summary.invalid,
            "duplicates": summary.duplicates,
        },
    ))

    return {
        "summary": summary.model_dump(),
        "invalid_count": len(invalid_leads),
        "duplicate_count": len(duplicate_leads),
    }


@router.get("/{tenant_id}/campaigns/{campaign_id}/leads", response_model=List[OutboundLead])
async def list_campaign_leads(
    tenant_id: str,
    campaign_id: str,
    status_filter: Optional[str] = Query(None, alias="status"),
    search: Optional[str] = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    current_user: UserResponse = Depends(get_current_user),
):
    """List leads for a campaign with optional filtering and search."""
    await validate_tenant_access(tenant_id, current_user)

    campaign = campaign_service.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if campaign.tenant_id != tenant_id:
        raise HTTPException(status_code=403, detail="Campaign does not belong to this tenant")

    return outbound_lead_service.list_leads(
        campaign_id=campaign_id,
        status=status_filter,
        search=search,
        skip=skip,
        limit=limit,
    )


@router.put("/{tenant_id}/campaigns/{campaign_id}/leads/{lead_id}", response_model=OutboundLead)
async def update_lead(
    tenant_id: str,
    campaign_id: str,
    lead_id: str,
    data: OutboundLeadUpdate,
    current_user: UserResponse = Depends(get_current_user),
):
    """Edit a single lead."""
    await validate_tenant_access(tenant_id, current_user)

    try:
        lead = outbound_lead_service.update_lead(campaign_id, lead_id, data)
        if not lead:
            raise HTTPException(status_code=404, detail="Lead not found")

        campaign_audit_service.log_action(CampaignAuditCreate(
            campaign_id=campaign_id,
            tenant_id=tenant_id,
            action=CampaignAuditAction.LEAD_EDITED,
            user_id=current_user.user_id,
            user_name=current_user.full_name,
            description=f"Lead {lead_id} edited",
        ))
        return lead
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{tenant_id}/campaigns/{campaign_id}/leads/{lead_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_lead(
    tenant_id: str,
    campaign_id: str,
    lead_id: str,
    current_user: UserResponse = Depends(get_current_user),
):
    """Delete a single lead."""
    await validate_tenant_access(tenant_id, current_user)
    outbound_lead_service.delete_lead(campaign_id, lead_id)

    # Update lead count
    count = outbound_lead_service.count_leads(campaign_id)
    campaign_service.update_lead_count(campaign_id, count)


@router.post("/{tenant_id}/campaigns/{campaign_id}/leads/bulk-delete")
async def bulk_delete_leads(
    tenant_id: str,
    campaign_id: str,
    lead_ids: List[str],
    current_user: UserResponse = Depends(get_current_user),
):
    """Bulk delete leads from a campaign."""
    await validate_tenant_access(tenant_id, current_user)
    deleted = outbound_lead_service.bulk_delete_leads(campaign_id, lead_ids)

    # Update lead count
    count = outbound_lead_service.count_leads(campaign_id)
    campaign_service.update_lead_count(campaign_id, count)

    campaign_audit_service.log_action(CampaignAuditCreate(
        campaign_id=campaign_id,
        tenant_id=tenant_id,
        action=CampaignAuditAction.LEADS_DELETED,
        user_id=current_user.user_id,
        user_name=current_user.full_name,
        description=f"Bulk deleted {deleted} leads",
    ))

    return {"deleted": deleted}


@router.get("/{tenant_id}/campaigns/{campaign_id}/invalid-leads")
async def download_invalid_leads(
    tenant_id: str,
    campaign_id: str,
    current_user: UserResponse = Depends(get_current_user),
):
    """Download invalid leads CSV from S3."""
    await validate_tenant_access(tenant_id, current_user)

    s3_key = f"outbound/{tenant_id}/{campaign_id}/invalid_leads.csv"
    try:
        s3_client = boto3.client("s3", region_name=settings.AWS_REGION)
        response = s3_client.get_object(Bucket=settings.EFFECTIVE_S3_BUCKET, Key=s3_key)
        content = response["Body"].read().decode("utf-8")

        return StreamingResponse(
            io.StringIO(content),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=invalid_leads.csv"},
        )
    except Exception:
        raise HTTPException(status_code=404, detail="Invalid leads file not found")


@router.get("/{tenant_id}/campaigns/{campaign_id}/duplicate-leads")
async def download_duplicate_leads(
    tenant_id: str,
    campaign_id: str,
    current_user: UserResponse = Depends(get_current_user),
):
    """Download duplicate leads CSV from S3."""
    await validate_tenant_access(tenant_id, current_user)

    s3_key = f"outbound/{tenant_id}/{campaign_id}/duplicate_leads.csv"
    try:
        s3_client = boto3.client("s3", region_name=settings.AWS_REGION)
        response = s3_client.get_object(Bucket=settings.EFFECTIVE_S3_BUCKET, Key=s3_key)
        content = response["Body"].read().decode("utf-8")

        return StreamingResponse(
            io.StringIO(content),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=duplicate_leads.csv"},
        )
    except Exception:
        raise HTTPException(status_code=404, detail="Duplicate leads file not found")


# ═══════════════════════════════════════════════════════════════════════
# Campaign Actions (Start / Pause / Resume / Archive)
# ═══════════════════════════════════════════════════════════════════════

@router.post("/{tenant_id}/campaigns/{campaign_id}/start", response_model=Campaign)
async def start_campaign(
    tenant_id: str,
    campaign_id: str,
    background_tasks: BackgroundTasks,
    current_user: UserResponse = Depends(get_current_user),
):
    """Start a campaign. Validates no other RUNNING campaign exists for this tenant."""
    await validate_tenant_access(tenant_id, current_user)

    campaign = campaign_service.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if campaign.tenant_id != tenant_id:
        raise HTTPException(status_code=403, detail="Campaign does not belong to this tenant")

    # Check for running campaign
    if campaign_service.has_running_campaign(tenant_id):
        raise HTTPException(
            status_code=409,
            detail="Another campaign is already running for this client. "
            "Please pause or wait for it to complete before starting a new one.",
        )

    # Validate campaign has leads and template
    if campaign.total_leads == 0:
        raise HTTPException(status_code=400, detail="Campaign has no leads. Upload leads first.")
    if not campaign.message_template or not campaign.message_template.strip():
        raise HTTPException(status_code=400, detail="Campaign has no message template. Set a template first.")

    try:
        updated = campaign_service.update_campaign_status(campaign_id, CampaignStatus.RUNNING)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    campaign_audit_service.log_action(CampaignAuditCreate(
        campaign_id=campaign_id,
        tenant_id=tenant_id,
        action=CampaignAuditAction.CAMPAIGN_STARTED,
        user_id=current_user.user_id,
        user_name=current_user.full_name,
        description=f"Campaign '{campaign.name}' started",
    ))

    # Execute campaign in background
    background_tasks.add_task(_run_campaign_async, campaign_id)

    return updated


def _run_campaign_async(campaign_id: str):
    """Wrapper to run async campaign executor in a sync background task."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(campaign_executor_service.execute_campaign(campaign_id))
    finally:
        loop.close()


@router.post("/{tenant_id}/campaigns/{campaign_id}/pause", response_model=Campaign)
async def pause_campaign(
    tenant_id: str,
    campaign_id: str,
    current_user: UserResponse = Depends(get_current_user),
):
    """Pause a running campaign."""
    await validate_tenant_access(tenant_id, current_user)

    campaign = campaign_service.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if campaign.tenant_id != tenant_id:
        raise HTTPException(status_code=403, detail="Campaign does not belong to this tenant")

    try:
        updated = campaign_service.update_campaign_status(campaign_id, CampaignStatus.PAUSED)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    campaign_audit_service.log_action(CampaignAuditCreate(
        campaign_id=campaign_id,
        tenant_id=tenant_id,
        action=CampaignAuditAction.CAMPAIGN_PAUSED,
        user_id=current_user.user_id,
        user_name=current_user.full_name,
        description=f"Campaign '{campaign.name}' paused",
    ))

    return updated


@router.post("/{tenant_id}/campaigns/{campaign_id}/resume", response_model=Campaign)
async def resume_campaign(
    tenant_id: str,
    campaign_id: str,
    background_tasks: BackgroundTasks,
    current_user: UserResponse = Depends(get_current_user),
):
    """Resume a paused campaign."""
    await validate_tenant_access(tenant_id, current_user)

    campaign = campaign_service.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if campaign.tenant_id != tenant_id:
        raise HTTPException(status_code=403, detail="Campaign does not belong to this tenant")

    # Check for running campaign
    if campaign_service.has_running_campaign(tenant_id):
        raise HTTPException(
            status_code=409,
            detail="Another campaign is already running. Pause it first.",
        )

    try:
        updated = campaign_service.update_campaign_status(campaign_id, CampaignStatus.RUNNING)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    campaign_audit_service.log_action(CampaignAuditCreate(
        campaign_id=campaign_id,
        tenant_id=tenant_id,
        action=CampaignAuditAction.CAMPAIGN_RESUMED,
        user_id=current_user.user_id,
        user_name=current_user.full_name,
        description=f"Campaign '{campaign.name}' resumed",
    ))

    # Resume execution in background
    background_tasks.add_task(_run_campaign_async, campaign_id)

    return updated


@router.post("/{tenant_id}/campaigns/{campaign_id}/archive", response_model=Campaign)
async def archive_campaign(
    tenant_id: str,
    campaign_id: str,
    current_user: UserResponse = Depends(get_current_user),
):
    """Archive a campaign."""
    await validate_tenant_access(tenant_id, current_user)

    campaign = campaign_service.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if campaign.tenant_id != tenant_id:
        raise HTTPException(status_code=403, detail="Campaign does not belong to this tenant")

    try:
        updated = campaign_service.update_campaign_status(campaign_id, CampaignStatus.ARCHIVED)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    campaign_audit_service.log_action(CampaignAuditCreate(
        campaign_id=campaign_id,
        tenant_id=tenant_id,
        action=CampaignAuditAction.CAMPAIGN_ARCHIVED,
        user_id=current_user.user_id,
        user_name=current_user.full_name,
        description=f"Campaign '{campaign.name}' archived",
    ))

    return updated


# ═══════════════════════════════════════════════════════════════════════
# Campaign Monitoring & History
# ═══════════════════════════════════════════════════════════════════════

@router.get("/{tenant_id}/campaigns/{campaign_id}/stats")
async def get_campaign_stats(
    tenant_id: str,
    campaign_id: str,
    current_user: UserResponse = Depends(get_current_user),
):
    """Get real-time campaign statistics."""
    await validate_tenant_access(tenant_id, current_user)

    campaign = campaign_service.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if campaign.tenant_id != tenant_id:
        raise HTTPException(status_code=403, detail="Campaign does not belong to this tenant")

    total = campaign.total_leads
    sent = campaign.sent_count
    failed = campaign.failed_count
    progress = (sent + failed) / total * 100 if total > 0 else 0

    return {
        "campaign_id": campaign_id,
        "status": campaign.status.value,
        "total_leads": total,
        "sent": sent,
        "failed": failed,
        "delivered": campaign.delivered_count,
        "response_count": campaign.response_count,
        "pending": max(0, total - sent - failed),
        "progress_percent": round(progress, 2),
    }


@router.get("/{tenant_id}/campaigns/{campaign_id}/history", response_model=List[CampaignAuditLog])
async def get_campaign_history(
    tenant_id: str,
    campaign_id: str,
    limit: int = Query(100, ge=1, le=500),
    current_user: UserResponse = Depends(get_current_user),
):
    """Get campaign audit history."""
    await validate_tenant_access(tenant_id, current_user)

    campaign = campaign_service.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if campaign.tenant_id != tenant_id:
        raise HTTPException(status_code=403, detail="Campaign does not belong to this tenant")

    return campaign_audit_service.get_campaign_history(campaign_id, limit=limit)
