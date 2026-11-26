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
    OptOutStep,
    PrefetchKnowledgeStep,
    BuildPromptStep,
    RunLLMStep,
    CaptureLeadDetailsStep,
    ApplyAgentStateStep,
    ToolExecutionStep,
    MemoryUpdateStep,
    PersistConversationStep,
    SendReplyStep,
)
from app.services.ai.outbound.enterprise_steps import GuardrailsValidationStep
from app.services.outbound.outbound_db_service import outbound_db_service
from app.core.logging_config import get_logger

logger = get_logger(__name__)

# The lock must outlive a full turn (LLM call + possible tool hop), otherwise a
# second message could run concurrently and both replies would interleave.
LOCK_TTL_SECONDS = 90
LOCK_ATTEMPTS = 8
LOCK_RETRY_SECONDS = 2.0


class OutboundConversationOrchestrator:
    """Orchestrates outbound session lifecycles, locks, and pipeline execution."""

    def __init__(self):
        # Assemble pipeline steps in order. Persist and Send always run, so even if
        # an earlier step fails the message is saved and the customer gets a reply.
        self.pipeline = ConversationPipeline([
            LoadSessionStep(),
            LoadConversationStep(),
            LoadMemoryStep(),
            LoadTenantSettingsStep(),
            OptOutStep(),
            # The keyword-guess "enterprise" steps (BANT score, decision-maker
            # probability, risk/momentum) were removed: lead status and score now
            # come from the agent's evaluation against the business instructions.
            PrefetchKnowledgeStep(),
            BuildPromptStep(),
            RunLLMStep(),                     # single LLM call unless a tool is needed
            GuardrailsValidationStep(),
            CaptureLeadDetailsStep(),         # only if the model skipped the state line
            ApplyAgentStateStep(),
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
        # 1. Acquire distributed lock. If the customer sends several messages quickly,
        # wait for the previous turn to finish instead of dropping the message.
        lock_acquired = False
        for attempt in range(1, LOCK_ATTEMPTS + 1):
            if outbound_db_service.acquire_lock(conversation_id, ttl_seconds=LOCK_TTL_SECONDS):
                lock_acquired = True
                break
            logger.warning(f"[Orchestrator] Conversation {conversation_id} is busy (Attempt {attempt}/{LOCK_ATTEMPTS}). Waiting...")
            await asyncio.sleep(LOCK_RETRY_SECONDS)

        if not lock_acquired:
            # Most likely a stale lock from a crashed/timed-out run. Answering without
            # the lock is better than silently dropping the customer's message.
            logger.error(
                f"[Orchestrator] Could not acquire lock for conversation {conversation_id} after "
                f"{LOCK_ATTEMPTS} attempts. Processing message {message_id} without the lock."
            )

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
            # 4. Release the lock only if we hold it (never delete another run's lock)
            if lock_acquired:
                outbound_db_service.release_lock(conversation_id)
                logger.debug(f"[Orchestrator] Released lock for conversation {conversation_id}")


# Singleton orchestrator instance
outbound_conversation_orchestrator = OutboundConversationOrchestrator()
