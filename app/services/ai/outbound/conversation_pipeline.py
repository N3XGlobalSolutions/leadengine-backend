"""Modular Conversation Pipeline and Steps for Outbound AI Agent execution."""

from abc import ABC, abstractmethod
import time
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import uuid

from pydantic import BaseModel, Field
from app.models.outbound.outbound_entities import (
    ConversationStage,
    ConversationStageManager,
    OutboundConversation,
    OutboundAIMessage,
    OutboundMemory,
    OutboundProduct,
    OutboundAgentSettings,
    OutboundCalendarSettings,
    OutboundCalendarMeeting,
    OutboundConversationAnalytics,
)
from app.services.outbound.outbound_db_service import outbound_db_service
from app.services.outbound.outbound_product_service import outbound_product_service
from app.core.logging_config import get_logger

logger = get_logger(__name__)


# ── AI SESSION OBJECT ──────────────────────────────────────────────────

class OutboundAISession(BaseModel):
    """Shared execution context passed through the outbound sales agent pipeline."""
    
    # Context IDs
    tenant_id: str
    campaign_id: str
    lead_id: str
    conversation_id: str
    
    # Input details
    sender_phone: str
    incoming_text: str
    message_id: str  # For idempotency
    
    # Loaded settings & entities (Static Cacheable)
    agent_settings: Optional[OutboundAgentSettings] = None
    calendar_settings: Optional[OutboundCalendarSettings] = None
    products: List[OutboundProduct] = Field(default_factory=list)
    
    # Loaded stateful entities (Mutable)
    conversation: Optional[OutboundConversation] = None
    memory: Optional[OutboundMemory] = None
    stage: ConversationStage = ConversationStage.NEW
    
    # Execution metrics and buffers
    history: List[OutboundAIMessage] = Field(default_factory=list)
    retrieved_knowledge: List[Dict[str, Any]] = Field(default_factory=list)
    selected_products: List[OutboundProduct] = Field(default_factory=list)
    
    tool_requests: List[Dict[str, Any]] = Field(default_factory=list)
    tool_outputs: List[Dict[str, Any]] = Field(default_factory=list)
    final_reply: Optional[str] = None
    
    # Telemetry and logging
    prompt_version: str = "1.0.0"
    prompt_id: str = "outbound_sales_v1"
    
    start_time: float = Field(default_factory=time.time)
    metrics: Dict[str, Any] = Field(default_factory=dict)
    token_usage: Dict[str, int] = Field(default_factory=lambda: {"prompt": 0, "completion": 0, "total": 0})
    estimated_cost: float = 0.0

    def track_latency(self, name: str, start: float) -> None:
        """Helper to compute step execution duration."""
        self.metrics[name] = time.time() - start


# ── PIPELINE INTERFACE ─────────────────────────────────────────────────

class PipelineStep(ABC):
    """Abstract class representing a single step in the Outbound AI pipeline."""

    @abstractmethod
    async def execute(self, session: OutboundAISession) -> None:
        """Execute logic for this step, updating the session object."""
        pass


class ConversationPipeline:
    """Orchestrates pipeline steps execution in sequence."""

    def __init__(self, steps: List[PipelineStep]):
        self.steps = steps

    async def run(self, session: OutboundAISession) -> None:
        """Run all registered pipeline steps in order with error recovery."""
        logger.info(f"[Pipeline] Starting execution for Lead {session.lead_id} (Conv: {session.conversation_id})")
        
        for step in self.steps:
            step_name = step.__class__.__name__
            step_start = time.time()
            try:
                logger.debug(f"[Pipeline] Executing step {step_name}")
                await step.execute(session)
                session.track_latency(step_name, step_start)
            except Exception as e:
                logger.error(f"[Pipeline] Error in step {step_name}: {e}", exc_info=True)
                session.track_latency(f"{step_name}_FAILED", step_start)
                # Fail gracefully by generating a default fallback message
                session.final_reply = "I'm sorry, I encountered a temporary issue. Could you please repeat that?"
                break
                
        total_time = time.time() - session.start_time
        logger.info(f"[Pipeline] Execution completed in {total_time:.2f}s")


# ── STEP IMPLEMENTATIONS ───────────────────────────────────────────────

class LoadSessionStep(PipelineStep):
    """Initialize session data and check locks."""
    
    async def execute(self, session: OutboundAISession) -> None:
        session.start_time = time.time()
        session.metrics = {}


class LoadConversationStep(PipelineStep):
    """Fetch or create OutboundConversation entity."""

    async def execute(self, session: OutboundAISession) -> None:
        conv = outbound_db_service.get_conversation(session.conversation_id)
        
        if not conv:
            # First reply to campaign message! Initialize new conversation
            logger.info(f"[LoadConversationStep] Creating new outbound conversation: {session.conversation_id}")
            conv = OutboundConversation(
                conversation_id=session.conversation_id,
                tenant_id=session.tenant_id,
                lead_id=session.lead_id,
                campaign_id=session.campaign_id,
                started_at=datetime.now(timezone.utc),
                last_message_at=datetime.now(timezone.utc),
                stage=ConversationStage.CONTACTED, # Customer has replied to initial campaign template
                status="ACTIVE"
            )
            outbound_db_service.save_conversation(conv)
            
            # Write mapping records for reverse lookup
            outbound_db_service.link_phone_to_conversation(session.tenant_id, session.sender_phone, session.conversation_id)
            outbound_db_service.link_lead_to_conversation(session.lead_id, session.conversation_id)
        
        session.conversation = conv
        session.stage = conv.stage
        
        # Load conversation message history
        session.history = outbound_db_service.get_conversation_messages(session.conversation_id, limit=50)


class LoadMemoryStep(PipelineStep):
    """Fetch or create OutboundMemory entity."""

    async def execute(self, session: OutboundAISession) -> None:
        session.memory = outbound_db_service.get_memory(session.conversation_id)


class LoadTenantSettingsStep(PipelineStep):
    """Load AI settings and calendar settings with fallback caching."""

    async def execute(self, session: OutboundAISession) -> None:
        # Load settings via cacheable DB service calls
        session.agent_settings = outbound_db_service.get_agent_settings(session.tenant_id)
        session.calendar_settings = outbound_db_service.get_calendar_settings(session.tenant_id)


class LoadProductsStep(PipelineStep):
    """Load product catalog if product search is enabled."""

    async def execute(self, session: OutboundAISession) -> None:
        if session.agent_settings and session.agent_settings.enable_product_search:
            session.products = outbound_product_service.list_products(session.tenant_id)


class BuildPromptStep(PipelineStep):
    """Prepare and version system instructions and prompts."""

    async def execute(self, session: OutboundAISession) -> None:
        # Resolve prompt version metadata
        session.prompt_id = "outbound_sales_v1"
        session.prompt_version = "1.0.0"


class RunLLMStep(PipelineStep):
    """Call OutboundSalesAgent reasoning loop to resolve user query and tool hops."""

    async def execute(self, session: OutboundAISession) -> None:
        from app.services.ai.outbound.sales_agent import outbound_sales_agent

        # Save incoming customer message in history
        user_msg_id = session.message_id
        user_msg = OutboundAIMessage(
            message_id=user_msg_id,
            conversation_id=session.conversation_id,
            role="user",
            content=session.incoming_text,
            timestamp=datetime.now(timezone.utc),
            prompt_version=session.prompt_version
        )
        session.history.append(user_msg)

        # Record initial history length to identify new messages later
        session.metrics["initial_history_len"] = float(len(session.history))

        # Run reasoning loop
        response = await outbound_sales_agent.execute_reasoning_loop(session)
        session.final_reply = response


class ToolExecutionStep(PipelineStep):
    """Step to log tool invocation and permission highlights."""

    async def execute(self, session: OutboundAISession) -> None:
        # Tool execution telemetry is already captured inside OutboundSalesAgent loop
        logger.debug(f"[ToolExecutionStep] Tool calls resolved: {len(session.tool_outputs)}")


class MemoryUpdateStep(PipelineStep):
    """Summarize conversations and update long term prospect memory values."""

    async def execute(self, session: OutboundAISession) -> None:
        memory = session.memory
        if not memory:
            return

        # Advance memory conversation stage matching current pipeline stage
        memory.conversation_stage = session.stage
        
        # Heuristic update: Parse text for budget or details if found
        text_lower = session.incoming_text.lower()
        if "budget" in text_lower or "$" in text_lower or "price" in text_lower:
            # AI will update budget properly later, this is a basic extraction fallback
            match = re.search(r"\$?\d+[\d,]*", session.incoming_text)
            if match:
                memory.budget = f"Discussed: {match.group(0)}"

        # If meeting was booked or cancelled, update memory properties
        # (This is also handled by scheduling tool definitions directly)
        
        # Trigger LLM-based automatic summarization if conversation length > 4000 tokens
        total_tokens = session.conversation.token_usage_sum if session.conversation else 0
        if total_tokens > 4000 and len(session.history) % 10 == 0:
            try:
                from app.services.ai.outbound_llm_service import outbound_llm_service
                logger.info(f"[MemoryUpdateStep] History exceeds 4000 tokens. Running automatic summary task.")
                
                history_text = "\n".join(f"{m.role}: {m.content}" for m in session.history[-20:])
                summary_prompt = (
                    f"Summarize the key agreements, budgets, objections, and user profile details from this sales chat history:\n"
                    f"{history_text}\n\n"
                    f"Return ONLY the plain summary under 150 words."
                )
                output, _, _, _ = outbound_llm_service.converse(
                    messages=[{"role": "user", "content": [{"text": summary_prompt}]}],
                    max_tokens=256
                )
                summary_text = output.get("content", [{}])[0].get("text", "")
                if summary_text:
                    memory.conversation_summary = summary_text.strip()
            except Exception as se:
                logger.warning(f"[MemoryUpdateStep] Summary generation failed: {se}")


class PersistConversationStep(PipelineStep):
    """Write modified session variables, memory states, and messages back to DynamoDB."""

    async def execute(self, session: OutboundAISession) -> None:
        # Calculate new messages added during this pipeline turn
        init_len = int(session.metrics.get("initial_history_len", 0))
        new_messages = session.history[init_len - 1:]  # Include user incoming message & final assistant responses

        # Append final reply message to session history
        if session.final_reply:
            assistant_msg_id = str(uuid.uuid4())
            assistant_msg = OutboundAIMessage(
                message_id=assistant_msg_id,
                conversation_id=session.conversation_id,
                role="assistant",
                content=session.final_reply,
                timestamp=datetime.now(timezone.utc),
                prompt_version=session.prompt_version
            )
            session.history.append(assistant_msg)
            new_messages.append(assistant_msg)

        # 1. Save all new message records
        for msg in new_messages:
            outbound_db_service.add_message(msg)

        # 2. Update conversation metrics
        conv = session.conversation
        if conv:
            conv.stage = session.stage
            conv.last_message_at = datetime.now(timezone.utc)
            conv.message_count = len(session.history)
            
            # Response metrics
            conv.response_count += 1
            latency = time.time() - session.start_time
            conv.response_time_sum += latency
            conv.token_usage_sum += session.token_usage.get("total", 0)
            
            outbound_db_service.save_conversation(conv)

        # 3. Save memory state
        if session.memory:
            outbound_db_service.save_memory(session.memory)

        # 4. Trigger Event Bus publishing
        try:
            from app.services.outbound.outbound_event_bus import outbound_event_bus
            from app.models.outbound.outbound_entities import OutboundConversationAnalytics
            
            # Publish event
            outbound_event_bus.publish("conversation_processed", {
                "session": session.model_dump(mode="json"),
                "latency": time.time() - session.start_time
            })
        except Exception as eb_err:
            logger.warning(f"[PersistConversationStep] Event bus publish failed: {eb_err}")


class SendReplyStep(PipelineStep):
    """Validate and dispatch the final reply over WhatsApp."""

    async def execute(self, session: OutboundAISession) -> None:
        if not session.final_reply:
            logger.error("[SendReplyStep] Missing final reply. Skipping dispatch.")
            return

        # 1. Run validation checks
        from app.services.ai.outbound.response_validator import response_validator
        validated_text = response_validator.validate_response(session, session.final_reply)
        session.final_reply = validated_text

        # 2. Load tenant WhatsApp configuration
        tenant = outbound_db_service.get_agent_settings(session.tenant_id)
        # Load core tenant details containing encrypted config keys
        from app.services import tenant_service
        tenant_config = tenant_service.get_tenant(session.tenant_id)
        
        if not tenant_config:
            logger.error(f"[SendReplyStep] Core tenant config not found for tenant: {session.tenant_id}")
            return

        # 3. Send WhatsApp reply
        from app.services.outbound.whatsapp_sender import whatsapp_sender
        logger.info(f"[SendReplyStep] Dispatching reply to {session.sender_phone}: '{validated_text[:60]}...'")
        
        result = await whatsapp_sender.send_message(
            to_phone=session.sender_phone,
            message=validated_text,
            tenant_config=tenant_config
        )
        
        logger.info(f"[SendReplyStep] WhatsApp dispatch complete. Status: {result.get('status')}")

