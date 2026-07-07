"""Admin REST API routes for Outbound AI Sales Agent configuration and monitoring."""

from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.models.user import UserResponse
from app.models.outbound.outbound_entities import (
    OutboundAgentSettings,
    OutboundCalendarSettings,
    OutboundProduct,
    OutboundConversation,
    OutboundAIMessage,
    OutboundConversationAnalytics,
    OutboundMemory,
)
from app.services.outbound.outbound_db_service import outbound_db_service
from app.services.outbound.outbound_product_service import outbound_product_service
from app.services.outbound.providers.google_calendar_provider import get_calendar_provider
from app.utils.dependencies import get_current_user, validate_tenant_access
from app.core.logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/outbound/ai", tags=["Outbound AI"])


# ═══════════════════════════════════════════════════════════════════════
# 1. AI Settings
# ═══════════════════════════════════════════════════════════════════════

@router.get("/{tenant_id}/settings/ai", response_model=OutboundAgentSettings)
async def get_ai_settings(
    tenant_id: str,
    current_user: UserResponse = Depends(get_current_user)
):
    """Retrieve outbound agent voice, tone, and active capability settings."""
    await validate_tenant_access(tenant_id, current_user)
    return outbound_db_service.get_agent_settings(tenant_id)


@router.post("/{tenant_id}/settings/ai", response_model=OutboundAgentSettings)
async def update_ai_settings(
    tenant_id: str,
    data: Dict[str, Any],
    current_user: UserResponse = Depends(get_current_user)
):
    """Save outbound agent settings configurations."""
    await validate_tenant_access(tenant_id, current_user)
    
    settings = outbound_db_service.get_agent_settings(tenant_id)
    # Update Pydantic fields
    dict_repr = settings.model_dump()
    for k, v in data.items():
        if k in dict_repr:
            dict_repr[k] = v
            
    updated = OutboundAgentSettings(**dict_repr)
    return outbound_db_service.save_agent_settings(updated)


# ═══════════════════════════════════════════════════════════════════════
# 2. Calendar Settings
# ═══════════════════════════════════════════════════════════════════════

@router.get("/{tenant_id}/settings/calendar", response_model=OutboundCalendarSettings)
async def get_calendar_settings(
    tenant_id: str,
    current_user: UserResponse = Depends(get_current_user)
):
    """Retrieve Google Calendar integration credentials and parameters."""
    await validate_tenant_access(tenant_id, current_user)
    return outbound_db_service.get_calendar_settings(tenant_id)


@router.post("/{tenant_id}/settings/calendar", response_model=OutboundCalendarSettings)
async def update_calendar_settings(
    tenant_id: str,
    data: Dict[str, Any],
    current_user: UserResponse = Depends(get_current_user)
):
    """Update calendar parameters and save encrypted tokens."""
    await validate_tenant_access(tenant_id, current_user)
    
    settings = outbound_db_service.get_calendar_settings(tenant_id)
    dict_repr = settings.model_dump()
    for k, v in data.items():
        if k in dict_repr:
            dict_repr[k] = v
            
    updated = OutboundCalendarSettings(**dict_repr)
    return outbound_db_service.save_calendar_settings(updated)


@router.post("/{tenant_id}/settings/calendar/test")
async def test_calendar_connection(
    tenant_id: str,
    current_user: UserResponse = Depends(get_current_user)
):
    """Validate client credentials connection to Google Calendar API."""
    await validate_tenant_access(tenant_id, current_user)
    
    settings = outbound_db_service.get_calendar_settings(tenant_id)
    provider = get_calendar_provider(settings)
    connected = provider.test_connection(settings)
    
    return {"connected": connected}


# ═══════════════════════════════════════════════════════════════════════
# 3. Product CRUD
# ═══════════════════════════════════════════════════════════════════════

@router.get("/{tenant_id}/products", response_model=List[OutboundProduct])
async def list_products(
    tenant_id: str,
    current_user: UserResponse = Depends(get_current_user)
):
    """List all products in the tenant catalog."""
    await validate_tenant_access(tenant_id, current_user)
    return outbound_product_service.list_products(tenant_id)


@router.post("/{tenant_id}/products", response_model=OutboundProduct, status_code=status.HTTP_201_CREATED)
async def create_product(
    tenant_id: str,
    data: Dict[str, Any],
    current_user: UserResponse = Depends(get_current_user)
):
    """Insert a new product into the catalog."""
    await validate_tenant_access(tenant_id, current_user)
    return outbound_product_service.create_product(tenant_id, data)


@router.get("/{tenant_id}/products/{product_id}", response_model=OutboundProduct)
async def get_product(
    tenant_id: str,
    product_id: str,
    current_user: UserResponse = Depends(get_current_user)
):
    """Fetch product details by ID."""
    await validate_tenant_access(tenant_id, current_user)
    product = outbound_product_service.get_product(product_id)
    if not product or product.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Product not found")
    return product


@router.put("/{tenant_id}/products/{product_id}", response_model=OutboundProduct)
async def update_product(
    tenant_id: str,
    product_id: str,
    data: Dict[str, Any],
    current_user: UserResponse = Depends(get_current_user)
):
    """Update properties of an existing catalog product."""
    await validate_tenant_access(tenant_id, current_user)
    product = outbound_product_service.update_product(product_id, data)
    if not product or product.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Product not found")
    return product


@router.delete("/{tenant_id}/products/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_product(
    tenant_id: str,
    product_id: str,
    current_user: UserResponse = Depends(get_current_user)
):
    """Delete a product by ID from the catalog."""
    await validate_tenant_access(tenant_id, current_user)
    product = outbound_product_service.get_product(product_id)
    if not product or product.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Product not found")
    outbound_product_service.delete_product(product_id)


# ═══════════════════════════════════════════════════════════════════════
# 4. Conversations Monitoring
# ═══════════════════════════════════════════════════════════════════════

@router.get("/{tenant_id}/conversations", response_model=List[OutboundConversation])
async def list_conversations(
    tenant_id: str,
    current_user: UserResponse = Depends(get_current_user)
):
    """List all Outbound AI conversations for a tenant."""
    await validate_tenant_access(tenant_id, current_user)
    return outbound_db_service.list_tenant_conversations(tenant_id)


@router.get("/{tenant_id}/conversations/{conversation_id}", response_model=OutboundConversation)
async def get_conversation(
    tenant_id: str,
    conversation_id: str,
    current_user: UserResponse = Depends(get_current_user)
):
    """Fetch metadata details of a specific conversation."""
    await validate_tenant_access(tenant_id, current_user)
    conv = outbound_db_service.get_conversation(conversation_id)
    if not conv or conv.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conv


@router.get("/{tenant_id}/conversations/{conversation_id}/messages", response_model=List[OutboundAIMessage])
async def get_conversation_messages(
    tenant_id: str,
    conversation_id: str,
    limit: int = Query(50, ge=1, le=100),
    current_user: UserResponse = Depends(get_current_user)
):
    """Load chronologically ordered chat messages of a conversation thread."""
    await validate_tenant_access(tenant_id, current_user)
    conv = outbound_db_service.get_conversation(conversation_id)
    if not conv or conv.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return outbound_db_service.get_conversation_messages(conversation_id, limit=limit)


@router.get("/{tenant_id}/conversations/{conversation_id}/memory", response_model=OutboundMemory)
async def get_conversation_memory(
    tenant_id: str,
    conversation_id: str,
    current_user: UserResponse = Depends(get_current_user)
):
    """Retrieve details of the active conversation's memory and enterprise metrics."""
    await validate_tenant_access(tenant_id, current_user)
    conv = outbound_db_service.get_conversation(conversation_id)
    if not conv or conv.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Conversation not found")
    memory = outbound_db_service.get_memory(conversation_id)
    if not memory:
        raise HTTPException(status_code=404, detail="Conversation memory not found")
    return memory


# ═══════════════════════════════════════════════════════════════════════
# 5. Analytics
# ═══════════════════════════════════════════════════════════════════════

@router.get("/{tenant_id}/analytics", response_model=OutboundConversationAnalytics)
async def get_analytics(
    tenant_id: str,
    current_user: UserResponse = Depends(get_current_user)
):
    """Retrieve aggregated performance and token usage stats for tenant."""
    await validate_tenant_access(tenant_id, current_user)
    return outbound_db_service.get_analytics(tenant_id)
