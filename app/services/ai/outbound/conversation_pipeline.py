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
from app.core.logging_config import get_logger

logger = get_logger(__name__)

# Sent when the pipeline fails before a reply could be generated, so the customer
# is never left without any answer.
FALLBACK_REPLY = "Thanks for your message! Our team will get back to you shortly."

# Messages loaded from DynamoDB per turn. Only the recent text turns are sent to the
# model (see sales_agent.HISTORY_WINDOW); older context lives in the memory summary.
HISTORY_LOAD_LIMIT = 40

# Summarise older conversation every N assistant replies to keep prompts small.
SUMMARY_EVERY_N_REPLIES = 8

# Minimum Titan V2 similarity for pre-fetched knowledge chunks (0.35 dropped real matches).
KNOWLEDGE_MIN_SCORE = 0.25

# Real media sent per reply (WhatsApp image/document messages), to avoid flooding the chat.
MAX_MEDIA_PER_REPLY = 3

OPT_OUT_RE = re.compile(
    r"^\s*(stop|unsubscribe|stop messaging( me)?|don'?t (message|text|contact) me|"
    r"do not (message|text|contact) me|remove me|opt ?out)\W*$",
    re.IGNORECASE,
)
OPT_OUT_REPLY = "Understood, you won't receive further messages from us. If you change your mind, just reply here."


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

    # Tenant / campaign context used by the prompt. `tenant` holds the full tenant
    # record (incl. encrypted WhatsApp credentials) and is excluded from dumps.
    tenant: Optional[Any] = Field(default=None, exclude=True)
    company_name: str = ""
    client_domain: str = ""
    lead_name: str = ""
    campaign_message: str = ""

    # Loaded stateful entities (Mutable)
    conversation: Optional[OutboundConversation] = None
    memory: Optional[OutboundMemory] = None
    stage: ConversationStage = ConversationStage.NEW

    # Execution metrics and buffers
    history: List[OutboundAIMessage] = Field(default_factory=list)
    retrieved_knowledge: List[Dict[str, Any]] = Field(default_factory=list)
    selected_products: List[OutboundProduct] = Field(default_factory=list)

    active_tools: List[str] = Field(default_factory=list)  # tools offered to the model this turn
    tool_requests: List[Dict[str, Any]] = Field(default_factory=list)
    tool_outputs: List[Dict[str, Any]] = Field(default_factory=list)
    final_reply: Optional[str] = None
    agent_state: Dict[str, Any] = Field(default_factory=dict)  # parsed <state> line
    # Real WhatsApp media to send after the text reply: {"type": "image"|"document", "link", "caption", "filename"}
    outgoing_media: List[Dict[str, Any]] = Field(default_factory=list)

    # Flow control
    skip_llm: bool = False    # reply already decided (e.g. opt-out confirmation)
    skip_reply: bool = False  # don't send anything this turn

    # Telemetry and logging
    prompt_version: str = "2.0.0"
    prompt_id: str = "outbound_sales_v2"

    start_time: float = Field(default_factory=time.time)
    metrics: Dict[str, Any] = Field(default_factory=dict)
    token_usage: Dict[str, int] = Field(default_factory=lambda: {
        "prompt": 0, "completion": 0, "cache_read": 0, "cache_write": 0, "embedding": 0, "total": 0
    })
    llm_calls: int = 0
    estimated_cost: float = 0.0  # USD, LLM calls only (embeddings priced in embedding_cost_usd())

    def track_latency(self, name: str, start: float) -> None:
        """Helper to compute step execution duration."""
        self.metrics[name] = time.time() - start

    def add_llm_usage(self, usage: Dict[str, int], cost_usd: float) -> None:
        """Accumulate one LLM call's Bedrock usage report."""
        for key in ("prompt", "completion", "cache_read", "cache_write", "total"):
            self.token_usage[key] = self.token_usage.get(key, 0) + int(usage.get(key, 0) or 0)
        self.llm_calls += 1
        self.estimated_cost += cost_usd

    def embedding_cost_usd(self) -> float:
        from app.services.outbound.pricing import embedding_cost_usd
        from app.services.ai.outbound_llm_service import outbound_llm_service
        return embedding_cost_usd(outbound_llm_service.embedding_model_id, self.token_usage.get("embedding", 0))

    def total_cost_usd(self) -> float:
        return self.estimated_cost + self.embedding_cost_usd()


# ── PIPELINE INTERFACE ─────────────────────────────────────────────────

class PipelineStep(ABC):
    """Abstract class representing a single step in the Outbound AI pipeline."""

    # Steps with always_run=True still execute after an earlier step failed, so the
    # customer's message is saved and a (fallback) reply is still sent.
    always_run: bool = False

    @abstractmethod
    async def execute(self, session: OutboundAISession) -> None:
        """Execute logic for this step, updating the session object."""
        pass


class ConversationPipeline:
    """Orchestrates pipeline steps execution in sequence."""

    def __init__(self, steps: List[PipelineStep]):
        self.steps = steps

    async def run(self, session: OutboundAISession) -> None:
        """Run all steps in order. After a failure, only always_run steps execute."""
        logger.info(f"[Pipeline] Starting execution for Lead {session.lead_id} (Conv: {session.conversation_id})")

        failed = False
        for step in self.steps:
            step_name = step.__class__.__name__
            if failed and not step.always_run:
                continue

            step_start = time.time()
            try:
                logger.debug(f"[Pipeline] Executing step {step_name}")
                await step.execute(session)
                session.track_latency(step_name, step_start)
            except Exception as e:
                logger.error(f"[Pipeline] Error in step {step_name}: {e}", exc_info=True)
                session.track_latency(f"{step_name}_FAILED", step_start)
                session.metrics["failed_step"] = step_name
                failed = True
                if not session.final_reply and not session.skip_reply:
                    session.final_reply = FALLBACK_REPLY

        total_time = time.time() - session.start_time
        logger.info(
            f"[Pipeline] Execution completed in {total_time:.2f}s | tokens={session.token_usage.get('total', 0)} "
            f"| tools={[o.get('name') for o in session.tool_outputs]} | failed={failed}"
        )


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

        # Load the most recent messages (chronological)
        session.history = outbound_db_service.get_conversation_messages(
            session.conversation_id, limit=HISTORY_LOAD_LIMIT
        )


class LoadMemoryStep(PipelineStep):
    """Fetch or create OutboundMemory entity."""

    async def execute(self, session: OutboundAISession) -> None:
        session.memory = outbound_db_service.get_memory(session.conversation_id)


class LoadTenantSettingsStep(PipelineStep):
    """Load AI/calendar settings plus tenant, lead and campaign context for the prompt."""

    async def execute(self, session: OutboundAISession) -> None:
        session.agent_settings = outbound_db_service.get_agent_settings(session.tenant_id)
        session.calendar_settings = outbound_db_service.get_calendar_settings(session.tenant_id)

        # The rest is optional context: a failure here must not block the reply.
        try:
            from app.services import tenant_service
            tenant = tenant_service.get_tenant(session.tenant_id)
            if tenant:
                session.tenant = tenant
                config = getattr(tenant, "config", None)
                session.company_name = (getattr(config, "company_name", None) or getattr(tenant, "name", "") or "").strip()
                session.client_domain = (getattr(config, "client_domain", None) or "").strip()
        except Exception as e:
            logger.warning(f"[LoadTenantSettingsStep] Tenant context unavailable: {e}")

        try:
            from app.services.outbound.outbound_lead_service import outbound_lead_service
            lead = outbound_lead_service.get_lead(session.campaign_id, session.lead_id)
            if lead and getattr(lead, "name", None):
                session.lead_name = lead.name.strip()
        except Exception as e:
            logger.warning(f"[LoadTenantSettingsStep] Lead context unavailable: {e}")

        try:
            from app.services.outbound.campaign_service import campaign_service
            from app.services.outbound.campaign_executor_service import campaign_message_text
            campaign = campaign_service.get_campaign(session.campaign_id)
            if campaign:
                session.campaign_message = campaign_message_text(campaign, session.lead_name, session.sender_phone)
        except Exception as e:
            logger.warning(f"[LoadTenantSettingsStep] Campaign context unavailable: {e}")


class LoadProductsStep(PipelineStep):
    """Deprecated: products are now fetched on demand by the catalog tools.

    Loading the whole catalog on every message was an unnecessary DB read and the
    result was never used in the prompt. Kept as a no-op for compatibility.
    """

    async def execute(self, session: OutboundAISession) -> None:
        return None


class OptOutStep(PipelineStep):
    """Handle STOP / unsubscribe requests without calling the LLM."""

    async def execute(self, session: OutboundAISession) -> None:
        memory = session.memory
        if not memory:
            return

        if OPT_OUT_RE.match(session.incoming_text or ""):
            memory.preferences["opted_out"] = True
            memory.preferences["opted_out_at"] = datetime.now(timezone.utc).isoformat()
            session.stage = ConversationStage.LOST
            session.final_reply = OPT_OUT_REPLY
            session.skip_llm = True
            session.agent_state = {"status": "OPTED_OUT"}
            # Future campaigns of this client skip this number.
            outbound_db_service.set_opt_out(session.tenant_id, session.sender_phone, True)
            logger.info(f"[OptOutStep] Lead {session.lead_id} opted out.")
            return

        # A new message from someone who opted out earlier means they re-engaged.
        if memory.preferences.get("opted_out"):
            memory.preferences["opted_out"] = False
            outbound_db_service.set_opt_out(session.tenant_id, session.sender_phone, False)
            session.agent_state = {"status": "IN_PROGRESS"}
            logger.info(f"[OptOutStep] Lead {session.lead_id} re-engaged after opting out.")


class PrefetchKnowledgeStep(PipelineStep):
    """Look up the knowledge base up front for substantive messages.

    One embedding + vector search is far cheaper than an extra LLM tool hop, and
    the model can't "forget" to check the company's documents.
    """

    async def execute(self, session: OutboundAISession) -> None:
        settings = session.agent_settings
        if session.skip_llm or not settings or not settings.enable_knowledge_search:
            return

        from app.services.ai.outbound.tool_router import needs_knowledge_lookup
        text = (session.incoming_text or "").strip()
        if not needs_knowledge_lookup(text):
            return

        # Short follow-ups ("and the fees?") need the previous question for context.
        query = text
        if len(text.split()) < 6:
            previous_user = next(
                (m.content for m in reversed(session.history) if m.role == "user" and m.content and m.content != text),
                "",
            )
            if previous_user:
                query = f"{previous_user} {text}"

        from app.services.ai.outbound_rag_service import outbound_rag_service
        results = outbound_rag_service.search(
            query, session.tenant_id, limit=3, min_score=KNOWLEDGE_MIN_SCORE, usage=session.token_usage
        )
        session.retrieved_knowledge = results
        session.metrics["knowledge_prefetch_hits"] = len(results)


class BuildPromptStep(PipelineStep):
    """Prepare and version system instructions and prompts."""

    async def execute(self, session: OutboundAISession) -> None:
        # Resolve prompt version metadata
        session.prompt_id = "outbound_sales_v2"
        session.prompt_version = "2.0.0"


class RunLLMStep(PipelineStep):
    """Call OutboundSalesAgent reasoning loop to resolve user query and tool hops."""

    async def execute(self, session: OutboundAISession) -> None:
        # Everything appended to history from here on belongs to this turn.
        session.metrics["turn_start_index"] = len(session.history)

        user_msg = OutboundAIMessage(
            message_id=session.message_id,
            conversation_id=session.conversation_id,
            role="user",
            content=session.incoming_text,
            timestamp=datetime.now(timezone.utc),
            prompt_version=session.prompt_version
        )
        session.history.append(user_msg)

        if session.skip_llm:
            return

        from app.services.ai.outbound.sales_agent import outbound_sales_agent
        from app.services.ai.outbound.tool_router import select_tools

        session.active_tools = select_tools(session)
        response = await outbound_sales_agent.execute_reasoning_loop(session)
        session.final_reply = response


class ToolExecutionStep(PipelineStep):
    """Step to log tool invocation and permission highlights."""

    async def execute(self, session: OutboundAISession) -> None:
        # Tool execution telemetry is already captured inside OutboundSalesAgent loop
        logger.debug(f"[ToolExecutionStep] Tool calls resolved: {len(session.tool_outputs)}")


class ApplyAgentStateStep(PipelineStep):
    """Store the details and qualification status the agent reported in its <state> line."""

    async def execute(self, session: OutboundAISession) -> None:
        state = session.agent_state or {}
        memory = session.memory
        if not memory or not state:
            return

        profile = memory.preferences.get("lead_profile")
        if not isinstance(profile, dict):
            profile = {"collected": {}, "status": "IN_PROGRESS"}
        profile.setdefault("collected", {})

        collected = state.get("collected") or {}
        if collected:
            profile["collected"].update(collected)
            if collected.get("budget"):
                memory.budget = collected["budget"]

        status = state.get("status")
        if status:
            profile["status"] = status

        if state.get("score") is not None:
            profile["score"] = state["score"]
            profile["score_reason"] = state.get("score_reason", "")

        objection = state.get("objection")
        if objection:
            objections = profile.setdefault("objections", [])
            if objection not in objections:
                objections.append(objection)

        if state.get("handoff"):
            profile["handoff_requested"] = True
            memory.last_action = "HANDOFF_REQUESTED"

        profile["updated_at"] = datetime.now(timezone.utc).isoformat()
        memory.preferences["lead_profile"] = profile

        # Reflect the qualification outcome in the conversation stage.
        if status == "QUALIFIED" and session.stage != ConversationStage.MEETING_BOOKED:
            session.stage = ConversationStage.QUALIFIED
        elif status in ("NOT_QUALIFIED", "OPTED_OUT"):
            session.stage = ConversationStage.LOST
        elif status == "IN_PROGRESS" and session.stage in (ConversationStage.NEW, ConversationStage.CONTACTED):
            session.stage = ConversationStage.ENGAGED
        if status == "OPTED_OUT":
            memory.preferences["opted_out"] = True


class CaptureLeadDetailsStep(PipelineStep):
    """Fallback when the model skipped the <state> line: extract the details the
    customer just shared with one small, cheap LLM call (~300 tokens).

    Runs only for substantive customer messages that aren't plain questions, so
    most turns cost nothing extra.
    """

    async def execute(self, session: OutboundAISession) -> None:
        if session.skip_llm or "collected" in (session.agent_state or {}):
            return
        text = (session.incoming_text or "").strip()
        from app.services.ai.outbound.tool_router import is_small_talk
        if is_small_talk(text) or len(text.split()) < 2 or (text.endswith("?") and len(text.split()) < 12):
            return

        import json as _json
        from app.services.ai.outbound_llm_service import outbound_llm_service
        from app.services.ai.outbound.agent_state import normalize_state

        last_question = next(
            (m.content for m in reversed(session.history[:-1]) if m.role == "assistant" and m.content), ""
        )
        prompt = (
            "Extract the details a customer shared in their WhatsApp reply to a sales assistant.\n"
            f"Assistant's previous message: {last_question[:400]}\n"
            f"Customer's reply: {text[:600]}\n\n"
            'Return only JSON: {"collected": {"snake_case_key": "short value"}, "objection": null}\n'
            "collected: facts about the customer (needs, budget, location, timeline, qualification, preferences). "
            "{} if none. objection: one of PRICE, TIMING, TRUST, COMPETITOR, NEED, AUTHORITY, or null."
        )
        try:
            output, _, usage, cost = outbound_llm_service.converse(
                messages=[{"role": "user", "content": [{"text": prompt}]}],
                temperature=0.0,
                max_tokens=150,
            )
            session.add_llm_usage(usage, cost)
            raw = "".join(p.get("text", "") for p in output.get("content", []) if "text" in p).strip()
            raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw)
            extracted = normalize_state(_json.loads(raw))
        except Exception as e:
            logger.warning(f"[CaptureLeadDetailsStep] Extraction failed: {e}")
            return

        if extracted.get("collected") or extracted.get("objection"):
            merged = dict(session.agent_state or {})
            merged.update({k: v for k, v in extracted.items() if k in ("collected", "objection")})
            session.agent_state = merged
            logger.info(f"[CaptureLeadDetailsStep] Captured: {extracted}")


class MemoryUpdateStep(PipelineStep):
    """Keep the memory stage in sync and periodically summarise older conversation."""

    async def execute(self, session: OutboundAISession) -> None:
        memory = session.memory
        if not memory:
            return

        memory.conversation_stage = session.stage

        # Only recent turns are sent to the model, so every few replies we fold the
        # older conversation into a short summary that stays in the prompt.
        conv = session.conversation
        replies = conv.response_count if conv else 0
        if session.skip_llm or replies < SUMMARY_EVERY_N_REPLIES or replies % SUMMARY_EVERY_N_REPLIES != 0:
            return

        try:
            from app.services.ai.outbound_llm_service import outbound_llm_service

            text_turns = [m for m in session.history if m.role in ("user", "assistant") and (m.content or "").strip()]
            history_text = "\n".join(f"{m.role}: {m.content[:400]}" for m in text_turns[-24:])
            previous = memory.conversation_summary or "None"
            summary_prompt = (
                "Update the summary of this WhatsApp sales conversation. Keep the customer's needs, "
                "details they shared, objections, agreements and next steps.\n"
                f"Previous summary: {previous}\n\nRecent messages:\n{history_text}\n\n"
                "Return only the updated summary, under 120 words."
            )
            output, _, usage, cost = outbound_llm_service.converse(
                messages=[{"role": "user", "content": [{"text": summary_prompt}]}],
                temperature=0.0,
                max_tokens=220,
            )
            session.add_llm_usage(usage, cost)

            summary_text = "".join(part.get("text", "") for part in output.get("content", []) if "text" in part)
            if summary_text.strip():
                memory.conversation_summary = summary_text.strip()
                logger.info(f"[MemoryUpdateStep] Conversation summary updated ({len(summary_text)} chars).")
        except Exception as se:
            logger.warning(f"[MemoryUpdateStep] Summary generation failed: {se}")


class PersistConversationStep(PipelineStep):
    """Write modified session variables, memory states, and messages back to DynamoDB."""

    always_run = True

    async def execute(self, session: OutboundAISession) -> None:
        start = session.metrics.get("turn_start_index")
        if start is None:
            # The turn failed before the customer message was recorded: store it now.
            new_messages = [
                OutboundAIMessage(
                    message_id=session.message_id,
                    conversation_id=session.conversation_id,
                    role="user",
                    content=session.incoming_text,
                    timestamp=datetime.now(timezone.utc),
                    prompt_version=session.prompt_version,
                )
            ]
        else:
            new_messages = list(session.history[int(start):])

        usage = session.token_usage
        cost_usd = session.total_cost_usd()
        latency = time.time() - session.start_time
        tools_used = [o.get("name") for o in session.tool_outputs if o.get("name")]

        if session.final_reply and not session.skip_reply:
            assistant_msg = OutboundAIMessage(
                message_id=str(uuid.uuid4()),
                conversation_id=session.conversation_id,
                role="assistant",
                content=session.final_reply,
                timestamp=datetime.now(timezone.utc),
                prompt_version=session.prompt_version,
                metadata={
                    "tokens": usage.get("total", 0),
                    "tokens_input": usage.get("prompt", 0),
                    "tokens_output": usage.get("completion", 0),
                    "tokens_cache_read": usage.get("cache_read", 0),
                    "tokens_cache_write": usage.get("cache_write", 0),
                    "tokens_embedding": usage.get("embedding", 0),
                    "llm_calls": session.llm_calls,
                    "cost_usd": round(cost_usd, 8),
                    "latency": round(latency, 2),
                    "tools": tools_used,
                    "knowledge_hits": len(session.retrieved_knowledge),
                    "media": [
                        {"type": m.get("type"), "caption": m.get("caption", ""), "link": m.get("link")}
                        for m in session.outgoing_media[:MAX_MEDIA_PER_REPLY]
                    ],
                },
            )
            session.history.append(assistant_msg)
            new_messages.append(assistant_msg)

        # 1. Save all new message records
        for msg in new_messages:
            outbound_db_service.add_message(msg)

        # 2. Update conversation metrics, real usage/cost and the lead snapshot
        conv = session.conversation
        if conv:
            conv.stage = session.stage
            conv.last_message_at = datetime.now(timezone.utc)
            conv.message_count += len(new_messages)
            if session.final_reply and not session.skip_reply:
                conv.response_count += 1
                conv.response_time_sum += latency
            conv.token_usage_sum += usage.get("total", 0)
            conv.tokens_input += usage.get("prompt", 0)
            conv.tokens_output += usage.get("completion", 0)
            conv.tokens_cache_read += usage.get("cache_read", 0)
            conv.tokens_cache_write += usage.get("cache_write", 0)
            conv.tokens_embedding += usage.get("embedding", 0)
            conv.llm_calls += session.llm_calls
            conv.cost_usd += cost_usd
            for name in tools_used:
                conv.tool_counts[name] = conv.tool_counts.get(name, 0) + 1

            conv.customer_message_count += 1
            conv.last_customer_message = session.incoming_text or ""
            if session.lead_name:
                conv.lead_name = session.lead_name
            conv.lead_phone = session.sender_phone
            profile = (session.memory.preferences.get("lead_profile") if session.memory else None) or {}
            if isinstance(profile, dict):
                conv.lead_status = profile.get("status", conv.lead_status) or conv.lead_status
                if profile.get("score") is not None:
                    conv.lead_score = int(profile["score"])
                    conv.lead_score_reason = profile.get("score_reason", "") or ""
                conv.objections = list(profile.get("objections") or conv.objections)
                conv.handoff_requested = bool(profile.get("handoff_requested", conv.handoff_requested))
            outbound_db_service.save_conversation(conv)

        # 3. Save memory state
        if session.memory:
            outbound_db_service.save_memory(session.memory)

        # 4. Daily usage counters for the analytics page (atomic ADD, one write per turn)
        try:
            outbound_db_service.record_daily_usage(session.tenant_id, datetime.now(timezone.utc).strftime("%Y-%m-%d"), {
                "tokens_input": usage.get("prompt", 0),
                "tokens_output": usage.get("completion", 0),
                "tokens_cache_read": usage.get("cache_read", 0),
                "tokens_cache_write": usage.get("cache_write", 0),
                "tokens_embedding": usage.get("embedding", 0),
                "llm_calls": session.llm_calls,
                "replies": 1 if (session.final_reply and not session.skip_reply) else 0,
                "customer_messages": 1,
                "cost_usd": round(cost_usd, 8),
            })
        except Exception as usage_err:
            logger.warning(f"[PersistConversationStep] Daily usage update failed: {usage_err}")

        logger.info(
            f"[PersistConversationStep] Turn usage: in={usage.get('prompt', 0)} out={usage.get('completion', 0)} "
            f"cache={usage.get('cache_read', 0)} emb={usage.get('embedding', 0)} calls={session.llm_calls} "
            f"cost=${cost_usd:.6f}"
        )


class SendReplyStep(PipelineStep):
    """Dispatch the final (already validated) reply over WhatsApp."""

    always_run = True

    async def execute(self, session: OutboundAISession) -> None:
        if session.skip_reply:
            return
        if not session.final_reply:
            logger.error("[SendReplyStep] Missing final reply. Skipping dispatch.")
            return

        tenant = session.tenant
        if tenant is None:
            from app.services import tenant_service
            tenant = tenant_service.get_tenant(session.tenant_id)
        if not tenant:
            logger.error(f"[SendReplyStep] Core tenant config not found for tenant: {session.tenant_id}")
            return

        from app.services.outbound.whatsapp_sender import whatsapp_sender
        logger.info(f"[SendReplyStep] Dispatching reply to {session.sender_phone} ({len(session.final_reply)} chars)")

        result = await whatsapp_sender.send_message(
            to_phone=session.sender_phone,
            message=session.final_reply,
            tenant_config=tenant.config,
        )

        status = result.get("status")
        session.metrics["send_status"] = status
        if status == "sent":
            logger.info(f"[SendReplyStep] Reply sent (ID: {result.get('message_id')})")
        else:
            logger.error(f"[SendReplyStep] Reply not delivered. Status: {status} | Error: {result.get('error')}")
            return

        # Real product photos / brochures, sent as WhatsApp media after the text.
        for media in session.outgoing_media[:MAX_MEDIA_PER_REPLY]:
            if media.get("type") == "image":
                media_result = await whatsapp_sender.send_image(
                    session.sender_phone, media["link"], tenant.config, caption=media.get("caption", "")
                )
            else:
                media_result = await whatsapp_sender.send_document(
                    session.sender_phone, media["link"], tenant.config,
                    filename=media.get("filename", ""), caption=media.get("caption", "")
                )
            if media_result.get("status") != "sent":
                logger.error(f"[SendReplyStep] {media.get('type')} not sent ({media['link']}): {media_result.get('error')}")
