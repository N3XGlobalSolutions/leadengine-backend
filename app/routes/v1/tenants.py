"""Tenant management routes."""
import json
from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import RedirectResponse

from app.models.TenantSummary import TenantSummary
from app.models.TenantSummaryPaginationResponse import TenantSummaryPaginationResponse
from app.models.tenant import Tenant, TenantCreate, TenantUpdate, is_valid_chat_product_image_id
from app.models.user import UserResponse, UserRole
from app.services import tenant_service, audit_service
from app.services.chat.product_images import (
    CHAT_PRODUCT_IMAGE_MAX_BYTES,
    ChatProductImageError,
    get_chat_product_image_url,
    store_chat_product_image,
)
from app.models.audit import AuditLogCreate
from app.core.logging_config import get_logger
from app.utils.dependencies import (
    get_current_user,
    require_role,
    validate_tenant_access,
)

router = APIRouter(prefix="/api/tenants", tags=["Tenants"])
logger = get_logger(__name__)


@router.post(
    "",
    response_model=Tenant,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_role([UserRole.PLATFORM_ADMIN, UserRole.PLATFORM_OPERATOR]))],
)
async def create_tenant(
    tenant_data: TenantCreate,
    current_user: UserResponse = Depends(get_current_user)
):
    """Create a new tenant (admin only)."""
    try:
        # Set the creator's name from the authenticated user
        tenant_data.created_by = current_user.full_name
        new_tenant = tenant_service.create_tenant(tenant_data)
        
        try:
            audit_service.log_activity(
                AuditLogCreate(
                    action="TENANT_CREATED",
                    user_id=current_user.user_id,
                    user_name=current_user.full_name,
                    user_role=current_user.role,
                    tenant_id=new_tenant.tenant_id,
                    description=f"Created tenant {new_tenant.name}"
                )
            )
        except Exception as e:
            print(f"Audit log failed: {e}")
            
        return new_tenant
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e)
        )

@router.get(
    "/tenants-summary",
    response_model=TenantSummaryPaginationResponse,
)
async def get_paginated_tenants_summary(
    limit: int = Query(24, le=100),
    cursor: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    plan: Optional[str] = Query(None),
    sort: Optional[str] = Query(None),
):
    return tenant_service.tenant_summaries(
        limit=limit,
        cursor=cursor,
        search=search,
        status=status,
        plan=plan,
        sort=sort,
    )

@router.get(
    "/tenants/summary",
    response_model=TenantSummaryPaginationResponse,
    dependencies=[Depends(require_role([UserRole.PLATFORM_ADMIN, UserRole.PLATFORM_OPERATOR, UserRole.SALES_TEAM]))],
)
async def get_tenant_summaries(
    limit: int = Query(24, le=100),
    cursor: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    plan: Optional[str] = Query(None),
    sort: Optional[str] = Query(None),
):
    return tenant_service.tenant_summaries(
        limit=limit,
        cursor=cursor,
        search=search,
        status=status,
        plan=plan,
        sort=sort,
    )

@router.get("/{tenant_id}", response_model=Tenant)
async def get_tenant(
    tenant_id: str,
    current_user: UserResponse = Depends(get_current_user),
):
    """Get a tenant by ID."""
    # Validate tenant access
    await validate_tenant_access(tenant_id, current_user)

    tenant = tenant_service.get_tenant(tenant_id)
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found"
        )
    return tenant


@router.get(
    "",
    response_model=List[Tenant],
    dependencies=[Depends(require_role([UserRole.PLATFORM_ADMIN, UserRole.PLATFORM_OPERATOR, UserRole.SALES_TEAM]))],
)
async def list_tenants(
    skip: int = 0,
    limit: int = Query(5000, le=5000),
):
    """List all tenants (admin only)."""
    return tenant_service.list_tenants(skip=skip, limit=limit)


@router.put("/{tenant_id}", response_model=Tenant)
async def update_tenant(
    tenant_id: str,
    update_data: TenantUpdate,
    current_user: UserResponse = Depends(get_current_user),
):
    """Update a tenant."""
    # Validate tenant access
    await validate_tenant_access(tenant_id, current_user)

    # Only platform admins, operators, and client admins of the tenant can update
    if current_user.role not in [
        UserRole.PLATFORM_ADMIN,
        UserRole.PLATFORM_OPERATOR,
        UserRole.CLIENT_ADMIN,
    ]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions"
        )

    tenant = tenant_service.update_tenant(tenant_id, update_data)
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found"
        )
        
    try:
        audit_service.log_activity(
            AuditLogCreate(
                action="TENANT_UPDATED",
                user_id=current_user.user_id,
                user_name=current_user.full_name,
                user_role=current_user.role,
                tenant_id=tenant_id,
                description=f"Updated tenant {tenant.name}"
            )
        )
    except Exception as e:
        print(f"Audit log failed: {e}")
        
    return tenant


@router.delete(
    "/{tenant_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_role([UserRole.PLATFORM_ADMIN]))],
)
async def delete_tenant(
    tenant_id: str,
    current_user: UserResponse = Depends(get_current_user)
):
    """Delete (deactivate) a tenant (admin only)."""
    success = tenant_service.delete_tenant(tenant_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found"
        )
        
    try:
        audit_service.log_activity(
            AuditLogCreate(
                action="TENANT_DELETED",
                user_id=current_user.user_id,
                user_name=current_user.full_name,
                user_role=current_user.role,
                tenant_id=tenant_id,
                description=f"Deleted tenant {tenant_id}"
            )
        )
    except Exception as e:
        print(f"Audit log failed: {e}")


@router.get(
    "/{tenant_id}/raw",
    response_model=dict,
    dependencies=[Depends(require_role([UserRole.PLATFORM_ADMIN]))],
)
async def get_tenant_raw(tenant_id: str):
    """Get raw DynamoDB item for a tenant (including soft-deleted). Platform admin only.

    Use this to diagnose why a tenant is missing from the admin dashboard.
    """
    raw = tenant_service.get_raw_tenant(tenant_id)
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No DynamoDB item found for tenant {tenant_id}",
        )
    # Serialize Decimal values so JSON can encode them
    import decimal
    def _serialize(obj):
        if isinstance(obj, decimal.Decimal):
            return float(obj)
        if isinstance(obj, dict):
            return {k: _serialize(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_serialize(i) for i in obj]
        return obj
    return _serialize(raw)


@router.post(
    "/{tenant_id}/restore",
    response_model=Tenant,
    dependencies=[Depends(require_role([UserRole.PLATFORM_ADMIN]))],
)
async def restore_tenant(
    tenant_id: str,
    current_user: UserResponse = Depends(get_current_user),
):
    """Restore a soft-deleted tenant (make it visible in the admin dashboard again). Platform admin only."""
    restored = tenant_service.restore_tenant(tenant_id)
    if not restored:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tenant {tenant_id} not found in DynamoDB",
        )

    tenant = tenant_service.get_tenant(tenant_id)
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Tenant restored in DB but failed to read back — check server logs",
        )

    try:
        audit_service.log_activity(
            AuditLogCreate(
                action="TENANT_RESTORED",
                user_id=current_user.user_id,
                user_name=current_user.full_name,
                user_role=current_user.role,
                tenant_id=tenant_id,
                description=f"Restored soft-deleted tenant {tenant.name}",
            )
        )
    except Exception as e:
        print(f"Audit log failed: {e}")

    return tenant


@router.post("/{tenant_id}/generate-api-key", response_model=dict)
async def generate_api_key(
    tenant_id: str,
    current_user: UserResponse = Depends(get_current_user),
):
    """Generate a new API key for tenant's chat widget."""
    # Validate tenant access
    await validate_tenant_access(tenant_id, current_user)

    # Only platform admins, operators, and client admins can generate API keys
    if current_user.role not in [
        UserRole.PLATFORM_ADMIN,
        UserRole.PLATFORM_OPERATOR,
        UserRole.CLIENT_ADMIN,
    ]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions"
        )

    api_key = tenant_service.generate_api_key(tenant_id)
    
    try:
        audit_service.log_activity(
            AuditLogCreate(
                action="API_KEY_GENERATED",
                user_id=current_user.user_id,
                user_name=current_user.full_name,
                user_role=current_user.role,
                tenant_id=tenant_id,
                description=f"Generated new API key for tenant {tenant_id}"
            )
        )
    except Exception as e:
        print(f"Audit log failed: {e}")
        
    return {"api_key": api_key, "tenant_id": tenant_id}


@router.get("/{tenant_id}/welcome")
async def get_welcome_message(tenant_id: str):
    """
    Get welcome message and branding for a tenant (public endpoint for widget).

    This endpoint is used by the chat widget to fetch the tenant-specific
    welcome message and complete branding configuration for UI initialization.
    """
    tenant = tenant_service.get_tenant(tenant_id)
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found"
        )

    # Build comprehensive branding response
    branding_data = {
        # Subscription & Identity
        "plan": tenant.config.plan.value,
        "url_slug": tenant.config.url_slug,
        # Bot Branding
        "company_name": tenant.config.company_name,
        "bot_name": tenant.config.bot_name,
        "avatar_url": tenant.config.avatar_url,
        # Colors
        "primary_color": tenant.config.primary_color,
        "secondary_color": tenant.config.secondary_color,
        # UI Customization
        "headline_prefix": tenant.config.headline_prefix,
        "highlighted_headline": tenant.config.highlighted_headline,
        "sub_headline": tenant.config.sub_headline,
        "key_features": tenant.config.key_features,
        # Qualification
        "qualification_questions": [
            {
                "id": q.id,
                "question": q.instruction, # Legacy support
                "label": q.label,
                "instruction": q.instruction,
                "collects": q.collects,
                "quick_replies": [
                    {"id": r.id, "text": r.text, "value": r.value or r.text}
                    for r in q.quick_replies
                ],
                "required": q.required,
            }
            for q in tenant.config.qualification_questions
        ],
        # Additional settings
        "language": tenant.config.language,
        "tone": tenant.config.tone,
    }

    return {
        "tenant_id": tenant.tenant_id,
        "tenant_name": tenant.name,
        "welcome_message": tenant.config.welcome_message,
        "branding": branding_data,
    }


# =============================================
# Chat Products (website chat product cards)
# =============================================


@router.post("/{tenant_id}/chat-products/images")
async def upload_chat_product_image(
    tenant_id: str,
    file: UploadFile = File(...),
    current_user: UserResponse = Depends(get_current_user),
):
    """Upload an image for a chat product card (PNG, JPEG or WebP, max 2 MB).

    Returns {"image_id": "<32hex>.<ext>"}; save it on the product via PUT /{tenant_id}.
    """
    # Same access rules as PUT /{tenant_id}
    await validate_tenant_access(tenant_id, current_user)

    if current_user.role not in [
        UserRole.PLATFORM_ADMIN,
        UserRole.PLATFORM_OPERATOR,
        UserRole.CLIENT_ADMIN,
    ]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions"
        )

    tenant = tenant_service.get_tenant(tenant_id)
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found"
        )

    # Read one byte past the limit so oversized files are detected without reading them fully
    content = await file.read(CHAT_PRODUCT_IMAGE_MAX_BYTES + 1)

    from app.services.storage.storage_service import storage_service

    try:
        image_id = store_chat_product_image(
            storage_service, tenant_id, content, file.content_type
        )
    except ChatProductImageError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error(f"Chat product image upload failed for tenant {tenant_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to store image",
        )

    logger.info(
        f"Chat product image uploaded for tenant {tenant_id}: {image_id} ({len(content)} bytes)"
    )
    return {"image_id": image_id}


@router.get("/{tenant_id}/chat-products/images/{image_id}")
def get_chat_product_image(tenant_id: str, image_id: str):
    """
    Serve a chat product image (public endpoint — chat visitors load it in <img>).

    Redirects (302) to a 1-hour presigned S3 URL. Sync handler so the blocking
    DynamoDB/S3 calls run in the threadpool.
    """
    if not is_valid_chat_product_image_id(image_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Image not found"
        )

    tenant = tenant_service.get_tenant_cached(tenant_id)
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found"
        )

    from app.services.storage.storage_service import storage_service

    try:
        url = get_chat_product_image_url(storage_service, tenant_id, image_id)
    except Exception as e:
        logger.error(f"Chat product image lookup failed for tenant {tenant_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Image temporarily unavailable",
        )

    if not url:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Image not found"
        )

    return RedirectResponse(
        url=url,
        status_code=status.HTTP_302_FOUND,
        headers={"Cache-Control": "public, max-age=600"},
    )
