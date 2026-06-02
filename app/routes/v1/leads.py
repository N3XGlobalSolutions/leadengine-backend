"""Lead management routes."""
import csv
import io
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from starlette.responses import StreamingResponse


from app.models.lead import Lead, LeadCreate, LeadStatus
from app.models.user import UserResponse
from app.services import conversation_service, lead_service
from app.utils.dependencies import get_current_user, validate_tenant_access

router = APIRouter(prefix="/api/leads", tags=["Leads"])


@router.post("", response_model=Lead, status_code=status.HTTP_201_CREATED)
async def create_lead(lead_data: LeadCreate):
    """Capture a lead from a conversation (public endpoint for widget)."""
    try:
        # Check if lead already exists for this conversation (prevent duplicates)
        existing_lead = lead_service.get_conversation_lead(lead_data.conversation_id)
        if existing_lead:
            return existing_lead  # Return existing lead instead of creating duplicate

        # Create new lead
        lead = lead_service.create_lead(lead_data)
        # Mark conversation as having captured lead
        conversation_service.mark_lead_captured(lead_data.conversation_id)
        return lead
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e)
        )


@router.get("/{lead_id}", response_model=Lead)
async def get_lead(
    lead_id: str, current_user: UserResponse = Depends(get_current_user)
):
    """Get a lead by ID (requires auth)."""
    lead = lead_service.get_lead(lead_id)
    if not lead:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found"
        )

    # Validate user has access to this lead's tenant
    await validate_tenant_access(lead.tenant_id, current_user)
    return lead


@router.patch("/{lead_id}/status", response_model=Lead)
async def update_lead_status(
    lead_id: str,
    new_status: LeadStatus,
    current_user: UserResponse = Depends(get_current_user),
):
    """Update a lead's status (requires auth and tenant access)."""
    # Get the lead first
    lead = lead_service.get_lead(lead_id)
    if not lead:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found"
        )

    # Validate user has access to this lead's tenant
    await validate_tenant_access(lead.tenant_id, current_user)

    # Update the status
    updated_lead = lead_service.update_lead_status(lead_id, new_status)
    if not updated_lead:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update lead status",
        )

    return updated_lead


@router.get("/tenants/{tenant_id}/all", response_model=List[Lead])
async def get_tenant_leads(
    tenant_id: str,
    status: Optional[LeadStatus] = None,
    skip: int = 0,
    limit: int = 10000,
    current_user: UserResponse = Depends(get_current_user),
):
    """Get all leads for a tenant, optionally filtered by status (requires auth and tenant access)."""
    try:
        await validate_tenant_access(tenant_id, current_user)
        return lead_service.list_tenant_leads(
            tenant_id, status=status, skip=skip, limit=limit
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e)
        )

@router.get("/tenants/{tenant_id}/export")
async def export_tenant_leads(
    tenant_id: str,
    status: Optional[LeadStatus] = None,
    current_user: UserResponse = Depends(get_current_user),
):
    """Export tenant leads as CSV."""

    try:

        await validate_tenant_access(tenant_id, current_user)

        leads = lead_service.export_leads(
            tenant_id=tenant_id,
            status=status,
        )

        output = io.StringIO(newline="")

        writer = csv.writer(output)

        # CSV Header
        writer.writerow([
            "Name",
            "Phone",
            "Status",
            "Created At",
        ])

        # CSV Rows
        for lead in leads:
            writer.writerow([
                lead.name,
                lead.phone,
                lead.status.value,
                lead.created_at.isoformat(),
            ])

        output.seek(0)

        return StreamingResponse(
            output,
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition":
                "attachment; filename=leads.csv"
            }
        )

    except HTTPException:
        raise

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )