"""Conversation Orchestrator for coordinating the Outbound AI pipeline and locking."""

import asyncio
import time
from app.services.ai.outbound.conversation_pipeline import (
    ConversationPipeline,
    OutboundAISession,
    LoadSessionStep,
    LoadConversationStep,
    LoadMemoryStep,
    LoadTenantSettingsStep,
    LoadProductsStep,
    BuildPromptStep,
    RunLLMStep,
    ToolExecutionStep,
    MemoryUpdateStep,
    PersistConversationStep,
    SendReplyStep,
)
from app.services.ai.outbound.enterprise_steps import (
    AnalyzeIntentAndStrategyStep,
    LeadQualificationStep,
    MultiStepPlanningStep,
    GuardrailsValidationStep,
)
from app.services.outbound.outbound_db_service import outbound_db_service
from app.core.logging_config import get_logger

logger = get_logger(__name__)


class OutboundConversationOrchestrator:
    """Orchestrates outbound session lifecycles, locks, and pipeline execution."""

    def __init__(self):
        # Assemble pipeline steps in order
        self.pipeline = ConversationPipeline([
            LoadSessionStep(),
            LoadConversationStep(),
            LoadMemoryStep(),
            LoadTenantSettingsStep(),
            LoadProductsStep(),
            AnalyzeIntentAndStrategyStep(),
            LeadQualificationStep(),
            MultiStepPlanningStep(),
            BuildPromptStep(),
            RunLLMStep(),
            GuardrailsValidationStep(),
            ToolExecutionStep(),
            MemoryUpdateStep(),
            PersistConversationStep(),
            SendReplyStep(),
        ])

    async def handle_customer_message(
        self,
        tenant_id: str,
        campaign_id: str,
        lead_id: str,
        conversation_id: str,
        sender_phone: str,
        incoming_text: str,
        message_id: str,
    ) -> bool:
        """Entry point for incoming message routing. Handles locking and runs pipeline.

        Args:
            tenant_id: Tenant classification
            campaign_id: Parent campaign
            lead_id: Prospect lead identification
            conversation_id: Unique chat conversation ID
            sender_phone: Customer sender phone number
            incoming_text: Input message text
            message_id: WhatsApp unique message ID for dedup

        Returns:
            bool: True if processed successfully, False if skipped/locked
        """
        # 1. Acquire distributed (fenced) lock, retrying up to 3 times.
        lock_owner = None
        for attempt in range(1, 4):
            lock_owner = outbound_db_service.acquire_lock(conversation_id, ttl_seconds=120)
            if lock_owner:
                break
            logger.warning(f"[Orchestrator] Lock acquisition failed for conversation {conversation_id} (Attempt {attempt}/3). Retrying...")
            await asyncio.sleep(1.0)

        if not lock_owner:
            logger.error(f"[Orchestrator] Could not acquire lock for conversation {conversation_id}. Aborting.")
            return False

        try:
            # 2. Instantiate Session context
            session = OutboundAISession(
                tenant_id=tenant_id,
                campaign_id=campaign_id,
                lead_id=lead_id,
                conversation_id=conversation_id,
                sender_phone=sender_phone,
                incoming_text=incoming_text,
                message_id=message_id,
            )

            # 3. Run execution pipeline
            await self.pipeline.run(session)
            return True

        except Exception as e:
            logger.error(f"[Orchestrator] Pipeline execution crashed: {e}", exc_info=True)
            return False

        finally:
            # 4. Release distributed lock (only if we still own it — fencing)
            outbound_db_service.release_lock(conversation_id, owner=lock_owner)
            logger.debug(f"[Orchestrator] Released lock for conversation {conversation_id}")


# Singleton orchestrator instance
outbound_conversation_orchestrator = OutboundConversationOrchestrator()
