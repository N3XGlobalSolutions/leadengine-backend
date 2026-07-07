"""Asynchronous event bus for post-processing decoupled tasks."""

import asyncio
from typing import Any, Callable, Dict, List
from app.services.outbound.outbound_db_service import outbound_db_service
from app.core.logging_config import get_logger

logger = get_logger(__name__)


class OutboundEventBus:
    """Publish-Subscribe model decoupling post-AI completion tasks."""

    def __init__(self):
        self._subscribers: Dict[str, List[Callable]] = {}
        
        # Self-register default subscribers
        self.subscribe("conversation_processed", self._update_analytics_subscriber)
        self.subscribe("conversation_processed", self._crm_sync_subscriber)

    def subscribe(self, event_type: str, callback: Callable) -> None:
        """Register a handler for a specific event type."""
        if event_type not in self._subscribers:
            self._subscribers[event_type] = []
        self._subscribers[event_type].append(callback)

    def publish(self, event_type: str, data: Any) -> None:
        """Publish event to all registered subscribers."""
        logger.info(f"[EventBus] Publishing event '{event_type}'")
        
        subscribers = self._subscribers.get(event_type, [])
        for sub in subscribers:
            try:
                # Direct invocation or task loop scheduling
                if asyncio.iscoroutinefunction(sub):
                    asyncio.create_task(sub(data))
                else:
                    # Run in separate thread or loop pool if synchronous
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        loop.run_in_executor(None, sub, data)
                    else:
                        sub(data)
            except Exception as e:
                logger.error(f"[EventBus] Error executing subscriber on '{event_type}': {e}")

    # ── DEFAULT SUBSCRIBERS ───────────────────────────────────────────

    def _update_analytics_subscriber(self, data: Any) -> None:
        """Update analytics metrics after conversation processing."""
        session_data = data.get("session", {})
        tenant_id = session_data.get("tenant_id")
        if not tenant_id:
            return

        logger.info(f"[EventBus] Updating metrics analytics for tenant: {tenant_id}")
        
        # Load and update analytics
        analytics = outbound_db_service.get_analytics(tenant_id)
        analytics.total_conversations = outbound_db_service.list_tenant_conversations(tenant_id)
        # Recompute totals
        conversations = outbound_db_service.list_tenant_conversations(tenant_id)
        analytics.total_conversations = len(conversations)
        
        # Bookings count
        meetings = outbound_db_service.list_tenant_meetings(tenant_id)
        analytics.meetings_booked = len([m for m in meetings if m.status != "CANCELLED"])
        
        # Token sum
        analytics.token_usage = sum(c.token_usage_sum for c in conversations)
        
        # Average responses count and latency
        total_responses = sum(c.response_count for c in conversations)
        total_latency = sum(c.response_time_sum for c in conversations)
        
        analytics.average_response_time = (total_latency / total_responses) if total_responses > 0 else 0.0
        analytics.average_conversation_length = (sum(c.message_count for c in conversations) / len(conversations)) if conversations else 0.0
        
        # Tool usage counts
        tool_counts = {}
        for conv in conversations:
            # We can query all messages of conversation to parse tools used
            messages = outbound_db_service.get_conversation_messages(conv.conversation_id, limit=100)
            for m in messages:
                if m.tool_calls:
                    for tc in m.tool_calls:
                        t_name = tc.get("name")
                        if t_name:
                            tool_counts[t_name] = tool_counts.get(t_name, 0) + 1
        analytics.tool_usage = tool_counts
        
        # Calculate conversion rate: (meetings booked / total conversations)
        if analytics.total_conversations > 0:
            analytics.conversion_rate = float(analytics.meetings_booked) / float(analytics.total_conversations) * 100.0
        else:
            analytics.conversion_rate = 0.0

        outbound_db_service.save_analytics(analytics)
        logger.info(f"[EventBus] Analytics updated successfully for tenant {tenant_id}")

    def _crm_sync_subscriber(self, data: Any) -> None:
        """Simulate CRM pushing sync (e.g. to Bitrix)."""
        session_data = data.get("session", {})
        tenant_id = session_data.get("tenant_id")
        lead_id = session_data.get("lead_id")
        stage = session_data.get("stage")
        memory = session_data.get("memory", {})

        logger.info(
            f"[EventBus] Triggering CRM Sync for Lead {lead_id} (Tenant: {tenant_id}) "
            f"Stage: {stage} | Budget: {memory.get('budget')} | Interests: {memory.get('interested_products')}"
        )
        # Integrates with existing CRM/Bitrix configurations if needed


# Global event bus singleton
outbound_event_bus = OutboundEventBus()
