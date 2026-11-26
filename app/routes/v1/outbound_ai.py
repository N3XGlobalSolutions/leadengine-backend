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

MAX_AGENT_INSTRUCTIONS_CHARS = 6000


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

    # The instructions are sent with every WhatsApp reply, so cap their size.
    instructions = data.get("agent_instructions")
    if instructions is not None and len(str(instructions)) > MAX_AGENT_INSTRUCTIONS_CHARS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Prompt / Instructions must be at most {MAX_AGENT_INSTRUCTIONS_CHARS} characters.",
        )

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
    limit: int = Query(200, ge=1, le=500),
    current_user: UserResponse = Depends(get_current_user)
):
    """Load the most recent messages of a conversation thread, oldest first."""
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

@router.get("/{tenant_id}/pricing")
async def get_pricing(
    tenant_id: str,
    current_user: UserResponse = Depends(get_current_user)
):
    """Token prices (USD and INR) and exchange rate used for all cost figures."""
    await validate_tenant_access(tenant_id, current_user)
    from app.services.ai.outbound_llm_service import outbound_llm_service
    from app.services.outbound.pricing import pricing_info
    return pricing_info(outbound_llm_service.llm_model_id, outbound_llm_service.embedding_model_id)


@router.get("/{tenant_id}/analytics")
async def get_analytics(
    tenant_id: str,
    days: int = Query(30, ge=1, le=90),
    current_user: UserResponse = Depends(get_current_user)
):
    """Real AI usage, cost (USD and INR) and lead outcomes for the tenant's WhatsApp conversations."""
    await validate_tenant_access(tenant_id, current_user)
    return build_outbound_analytics(tenant_id, days)


def build_outbound_analytics(tenant_id: str, days: int = 30) -> Dict[str, Any]:
    from datetime import datetime, timedelta, timezone
    from app.services.ai.outbound_llm_service import outbound_llm_service
    from app.services.outbound.campaign_service import campaign_service
    from app.services.outbound.pricing import pricing_info, to_inr

    now = datetime.now(timezone.utc)
    conversations = outbound_db_service.list_tenant_conversations(tenant_id)
    campaign_names = {c.campaign_id: c.name for c in campaign_service.list_campaigns(tenant_id, limit=1000)}
    meetings = [m for m in outbound_db_service.list_tenant_meetings(tenant_id) if m.status != "CANCELLED"]

    def tracked_tokens(c) -> int:
        return c.tokens_input + c.tokens_output + c.tokens_cache_read + c.tokens_cache_write

    totals = {
        "conversations": len(conversations),
        "active_last_24h": sum(1 for c in conversations if (now - c.last_message_at).total_seconds() < 86400),
        "customer_messages": sum(c.customer_message_count for c in conversations),
        "ai_replies": sum(c.response_count for c in conversations),
        "llm_calls": sum(c.llm_calls for c in conversations),
        "tokens_input": sum(c.tokens_input for c in conversations),
        "tokens_output": sum(c.tokens_output for c in conversations),
        "tokens_cache_read": sum(c.tokens_cache_read for c in conversations),
        "tokens_cache_write": sum(c.tokens_cache_write for c in conversations),
        "tokens_embedding": sum(c.tokens_embedding for c in conversations),
        # Tokens recorded before per-type tracking existed (no input/output split, not priced)
        "tokens_untracked": sum(max(0, c.token_usage_sum - tracked_tokens(c)) for c in conversations),
        "cost_usd": sum(c.cost_usd for c in conversations),
        "response_time_sum": sum(c.response_time_sum for c in conversations),
    }
    totals["tokens_total"] = (totals["tokens_input"] + totals["tokens_output"]
                              + totals["tokens_cache_read"] + totals["tokens_cache_write"])
    totals["cost_inr"] = to_inr(totals["cost_usd"])
    priced_convs = [c for c in conversations if c.cost_usd > 0]
    replies_priced = sum(c.response_count for c in priced_convs)
    totals["avg_cost_per_conversation_inr"] = (to_inr(sum(c.cost_usd for c in priced_convs)) / len(priced_convs)) if priced_convs else 0.0
    totals["avg_cost_per_reply_inr"] = (totals["cost_inr"] / replies_priced) if replies_priced else 0.0
    totals["avg_tokens_per_reply"] = (sum(tracked_tokens(c) for c in priced_convs) / replies_priced) if replies_priced else 0.0
    totals["avg_response_time"] = (totals["response_time_sum"] / totals["ai_replies"]) if totals["ai_replies"] else 0.0
    totals["cache_hit_rate"] = (
        totals["tokens_cache_read"] / (totals["tokens_input"] + totals["tokens_cache_read"]) * 100
        if (totals["tokens_input"] + totals["tokens_cache_read"]) else 0.0
    )
    del totals["response_time_sum"]

    scores = [c.lead_score for c in conversations if c.lead_score is not None]
    status_counts: Dict[str, int] = {}
    stage_counts: Dict[str, int] = {}
    objection_counts: Dict[str, int] = {}
    tool_counts: Dict[str, int] = {}
    campaign_rows: Dict[str, Dict[str, Any]] = {}
    for c in conversations:
        status_counts[c.lead_status] = status_counts.get(c.lead_status, 0) + 1
        stage_counts[c.stage.value] = stage_counts.get(c.stage.value, 0) + 1
        for o in c.objections:
            objection_counts[o] = objection_counts.get(o, 0) + 1
        for name, n in c.tool_counts.items():
            tool_counts[name] = tool_counts.get(name, 0) + n
        row = campaign_rows.setdefault(c.campaign_id, {
            "campaign_id": c.campaign_id, "campaign_name": campaign_names.get(c.campaign_id, "Deleted campaign"),
            "conversations": 0, "qualified": 0, "tokens": 0, "cost_usd": 0.0,
        })
        row["conversations"] += 1
        row["qualified"] += 1 if c.lead_status == "QUALIFIED" else 0
        row["tokens"] += tracked_tokens(c)
        row["cost_usd"] += c.cost_usd
    for row in campaign_rows.values():
        row["cost_inr"] = to_inr(row["cost_usd"])

    decided = status_counts.get("QUALIFIED", 0) + status_counts.get("NOT_QUALIFIED", 0)
    leads = {
        "in_progress": status_counts.get("IN_PROGRESS", 0),
        "qualified": status_counts.get("QUALIFIED", 0),
        "not_qualified": status_counts.get("NOT_QUALIFIED", 0),
        "opted_out": status_counts.get("OPTED_OUT", 0),
        "handoffs": sum(1 for c in conversations if c.handoff_requested),
        "meetings_booked": len(meetings),
        "qualification_rate": (status_counts.get("QUALIFIED", 0) / decided * 100) if decided else 0.0,
        "scored_leads": len(scores),
        "avg_score": (sum(scores) / len(scores)) if scores else None,
    }

    # Daily usage series (zero-filled)
    since = (now - timedelta(days=days - 1)).date()
    by_day = {row.get("day"): row for row in outbound_db_service.get_daily_usage(tenant_id, since.isoformat())}
    daily = []
    for i in range(days):
        day = (since + timedelta(days=i)).isoformat()
        row = by_day.get(day, {})
        cost = float(row.get("cost_usd", 0) or 0)
        daily.append({
            "day": day,
            "tokens_input": int(row.get("tokens_input", 0) or 0),
            "tokens_output": int(row.get("tokens_output", 0) or 0),
            "tokens_cache_read": int(row.get("tokens_cache_read", 0) or 0),
            "tokens_embedding": int(row.get("tokens_embedding", 0) or 0),
            "llm_calls": int(row.get("llm_calls", 0) or 0),
            "replies": int(row.get("replies", 0) or 0),
            "customer_messages": int(row.get("customer_messages", 0) or 0),
            "cost_usd": cost,
            "cost_inr": to_inr(cost),
        })

    conversation_rows = [{
        "conversation_id": c.conversation_id,
        "lead_name": c.lead_name,
        "lead_phone": c.lead_phone,
        "campaign_id": c.campaign_id,
        "campaign_name": campaign_names.get(c.campaign_id, "Deleted campaign"),
        "stage": c.stage.value,
        "lead_status": c.lead_status,
        "lead_score": c.lead_score,
        "customer_messages": c.customer_message_count,
        "ai_replies": c.response_count,
        "llm_calls": c.llm_calls,
        "tokens_input": c.tokens_input,
        "tokens_output": c.tokens_output,
        "tokens_cache_read": c.tokens_cache_read,
        "tokens_embedding": c.tokens_embedding,
        "tokens_untracked": max(0, c.token_usage_sum - tracked_tokens(c)),
        "cost_usd": c.cost_usd,
        "cost_inr": to_inr(c.cost_usd),
        "avg_response_time": (c.response_time_sum / c.response_count) if c.response_count else 0.0,
        "last_message_at": c.last_message_at.isoformat(),
    } for c in conversations]

    return {
        "tenant_id": tenant_id,
        "generated_at": now.isoformat(),
        "period_days": days,
        "pricing": pricing_info(outbound_llm_service.llm_model_id, outbound_llm_service.embedding_model_id),
        "totals": totals,
        "leads": leads,
        "stages": stage_counts,
        "objections": objection_counts,
        "tools": tool_counts,
        "campaigns": sorted(campaign_rows.values(), key=lambda r: r["conversations"], reverse=True),
        "daily": daily,
        "conversations": conversation_rows,
    }
