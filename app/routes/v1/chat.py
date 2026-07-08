"""Chat and conversation routes - State Machine Architecture."""

from typing import List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from fastapi import Request

from app.models.conversation import (
    ChatRequest,
    ChatResponse,
    Conversation,
    Message,
    ConversationState,
)
from app.models.user import UserResponse
from app.models.tenant import Plan
from app.services import conversation_service, tenant_service
from app.services.ai.rag_service import rag_service
from app.services.ai.llm_service import llm_service
from app.services.chat import s3_international_state_machine
from app.services.chat.state_machine import state_machine
from app.services.chat.s2_state_machine import s2_state_machine
from app.services.chat.s3_state_machine import s3_state_machine
from app.services.chat.s3_international_state_machine import (
    s3_international_state_machine,
)
from app.services.chat.s3_max_state_machine import s3_max_state_machine
from app.services.chat.s3_mapping_state_machine import s3_mapping_state_machine
from app.utils.dependencies import get_current_user, validate_tenant_access
from app.utils.localization import get_message, get_language_name
from app.core.config import get_settings
from app.core.logging_config import get_logger

router = APIRouter(prefix="/api/chat", tags=["Chat"])
logger = get_logger(__name__)
settings = get_settings()


@router.post("", response_model=ChatResponse)
def chat(
    request: ChatRequest,
    http_request: Request,
    api_key: Optional[str] = Header(None, alias="X-API-Key"),
    widget_key: Optional[str] = Header(None, alias="X-Widget-Key"),
):
    """
    Chat with the bot using State Machine Architecture.

    NOTE: This is a SYNC handler on purpose. The body performs blocking I/O
    (Bedrock/Claude LLM calls, DynamoDB, RAG). Declaring it ``def`` lets
    Starlette run it in a worker threadpool so a slow LLM call does not block
    the event loop / other concurrent requests (previously it was ``async def``
    with blocking calls, serializing all traffic).

    Key Principles:
    - Backend controls flow via state machine
    - LLM only generates natural language responses
    - Phone number is STRICTLY MANDATORY
    - Summary is created by backend, not LLM

    Security: the client-supplied tenant_id in the body is NEVER trusted on its
    own. The effective tenant is derived from a verified credential:
      * X-API-Key   -> server/integration key resolved to a tenant, or
      * X-Widget-Key -> the tenant's widget key, bound to the claimed tenant_id
                        AND a whitelisted Origin.
    When CHAT_REQUIRE_KEY is enabled (default), a request without a valid key is
    rejected instead of silently trusting the body tenant_id.
    """
    try:
        # ---- Tenant authentication (multi-tenant isolation) ----
        verified_tenant_id: Optional[str] = None
        if api_key:
            verified_tenant_id = tenant_service.validate_api_key(api_key)
            if not verified_tenant_id:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key"
                )
        elif widget_key:
            # Widget key must match the claimed tenant's configured key AND the
            # request Origin must be whitelisted for that tenant.
            claimed = tenant_service.get_tenant(request.tenant_id) if request.tenant_id else None
            origin = http_request.headers.get("origin")
            allowed_origins = getattr(getattr(claimed, "config", None), "allowed_origins", None) or []
            widget_secret = getattr(getattr(claimed, "config", None), "api_key", None)
            if (
                not claimed
                or not widget_secret
                or widget_secret != widget_key
                or not origin
                or origin not in allowed_origins
            ):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid widget key or unauthorized origin",
                )
            verified_tenant_id = request.tenant_id

        if verified_tenant_id:
            request.tenant_id = verified_tenant_id
        elif settings.CHAT_REQUIRE_KEY:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="A valid API key (X-API-Key) or widget key (X-Widget-Key) is required",
            )
        # else (dev only): fall through trusting the body tenant_id

        # Get or create conversation
        if request.conversation_id:
            conversation = conversation_service.get_conversation(
                request.conversation_id
            )
            if not conversation:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Conversation not found",
                )
        else:
            conversation = conversation_service.create_conversation(
                request.tenant_id, request.user_identifier
            )

        # Add user message
        conversation_service.add_message(
            conversation.conversation_id, role="user", content=request.query
        )

        # Fetch Tenant Config
        tenant = tenant_service.get_tenant(request.tenant_id)
        if not tenant:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Tenant not found",
            )

        config = tenant.config
        questions = config.qualification_questions or []
        total_steps = len(questions)

        # Check Tenant Status
        from app.models.tenant import TenantStatus

        if tenant.status == TenantStatus.INACTIVE:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="This service is currently inactive.",
            )

        if tenant.status == TenantStatus.PAUSED:
            # Return maintenance/paused response
            # Construct standard ChatResponse (flat structure, not nested Message)
            import uuid

            return ChatResponse(
                conversation_id=conversation.conversation_id,
                message_id=str(uuid.uuid4()),
                answer="Our automated assistant is temporarily paused. Please contact support or try again later.",
                current_state="paused",
                sources=[],
                suggested_actions=[],
            )

        # ============================================================
        # STATE MACHINE PROCESSING - Route based on tenant plan
        # ============================================================

        # Select state machine based on plan (S1 vs S2)
        if config.plan == Plan.S2:
            logger.info(f"Using S2 state machine for tenant plan: {config.plan}")

            # Fetch history for context awareness (S2 only)
            history = conversation_service.get_context_history(
                conversation.conversation_id, max_messages=10
            )

            state_result = s2_state_machine.process_message(
                conversation=conversation,
                user_message=request.query,
                tenant_config=config,
                history=history,
            )
        elif config.plan == Plan.S3:
            logger.info(f"Using S3 state machine for tenant plan: {config.plan}")

            # Fetch history for context awareness
            history = conversation_service.get_context_history(
                conversation.conversation_id, max_messages=10
            )

            state_result = s3_state_machine.process_message(
                conversation=conversation,
                user_message=request.query,
                tenant_config=config,
                history=history,
            )
        elif config.plan == Plan.S3_INTERNATIONAL:
            logger.info(f"Using S3 state machine for tenant plan: {config.plan}")

            # Fetch history for context awareness
            history = conversation_service.get_context_history(
                conversation.conversation_id, max_messages=10
            )

            state_result = s3_international_state_machine.process_message(
                conversation=conversation,
                user_message=request.query,
                tenant_config=config,
                history=history,
            )

        elif config.plan == Plan.S3_MAX:
            logger.info(f"Using S3 Max (agentic) state machine for tenant plan: {config.plan}")

            # Fetch history for context awareness
            history = conversation_service.get_context_history(
                conversation.conversation_id, max_messages=20
            )

            state_result = s3_max_state_machine.process_message(
                conversation=conversation,
                message=request.query,
                tenant_config=config,
                conversation_messages=history,
            )

            # S3 Max: The bot_response is fully generated by the graph
            # Skip the normal action-based response generation
            answer = state_result.bot_response
            sources = []

            # Persist s3_max_state for next message
            if state_result.updated_s3_max_state:
                conversation_service.update_s3_max_state(
                    conversation.conversation_id,
                    state_result.updated_s3_max_state,
                )

            # Update extracted data
            current_data = dict(conversation.extracted_lead_data)
            if state_result.extracted_data:
                current_data.update(state_result.extracted_data)
                conversation_service.update_extracted_data(
                    conversation.conversation_id, current_data
                )

            # Update conversation state
            conversation_service.update_conversation_state(
                conversation_id=conversation.conversation_id,
                current_state=state_result.next_state,
                step_index=state_result.step_index,
                ignore_count=0,
                name_ask_count=conversation.name_ask_count,
            )

            # Lead capture for S3 Max
            suggested_actions = []
            if state_result.is_complete and state_result.lead_verdict:
                if not conversation.lead_captured:
                    from app.services import lead_service
                    from app.models.lead import LeadCreate, LeadStatus

                    lead_status = (
                        LeadStatus.QUALIFIED
                        if state_result.lead_verdict == "QUALIFIED"
                        else LeadStatus.DISQUALIFIED
                    )

                    lead_metadata = dict(state_result.summary_data or {})
                    if state_result.s3_max_lead_output:
                        lead_metadata["s3_max_lead_output"] = state_result.s3_max_lead_output

                    lead_create = LeadCreate(
                        tenant_id=request.tenant_id,
                        conversation_id=conversation.conversation_id,
                        name=current_data.get("name"),
                        phone=current_data.get("phone"),
                        message="Lead captured via S3 Max agentic qualification",
                        status=lead_status,
                        metadata=lead_metadata,
                    )
                    lead_service.create_lead(lead_create)
                    conversation_service.mark_lead_captured(conversation.conversation_id)

                    if lead_status == LeadStatus.QUALIFIED:
                        from app.services.crm.bitrix_service import push_lead_to_bitrix
                        push_lead_to_bitrix(
                            company_name=config.company_name or tenant.name,
                            name=current_data.get("name"),
                            phone=current_data.get("phone"),
                            metadata=lead_metadata,
                        )

                    logger.info(
                        f"S3 Max lead created: {lead_status.value}, "
                        f"priority={state_result.s3_max_lead_output.get('lead_priority', 'N/A') if state_result.s3_max_lead_output else 'N/A'}"
                    )

                suggested_actions.append("lead_captured")
                suggested_actions.append("chat_ended")

            # Save assistant message
            assistant_message = conversation_service.add_message(
                conversation.conversation_id,
                role="assistant",
                content=answer,
                sources=sources,
            )

            return ChatResponse(
                answer=answer,
                conversation_id=conversation.conversation_id,
                message_id=assistant_message.message_id,
                sources=sources,
                suggested_actions=suggested_actions,
                current_state=state_result.next_state,
                current_step_index=state_result.step_index,
                total_steps=0,  # Dynamic — no fixed total
                summary_data=state_result.summary_data,
                show_actions=state_result.show_actions,
                quick_replies=state_result.quick_replies,
                s3_max_lead_output=state_result.s3_max_lead_output,
                estimated_progress=state_result.estimated_progress,
            )

        elif config.plan == Plan.S3_MAPPING:
            logger.info(f"Using S3 Mapping state machine for tenant plan: {config.plan}")

            state_result = s3_mapping_state_machine.process_message(
                message=request.query,
                conversation=conversation,
                config=config,
            )

            # S3 Mapping: response fully built by the state machine
            answer = state_result.bot_response
            sources = []

            # Persist mapping state for next message
            if state_result.updated_mapping_state:
                conversation_service.update_s3_mapping_state(
                    conversation.conversation_id,
                    state_result.updated_mapping_state,
                )

            # Lead capture on summary/disqualified
            if state_result.action in ("show_summary", "disqualified") and state_result.lead_output:
                import uuid
                lead_output = state_result.lead_output
                name = lead_output.get("name", "")
                phone = lead_output.get("phone", "")

                if phone and not conversation.lead_captured:
                    from app.services import lead_service
                    from app.models.lead import LeadStatus, LeadCreate

                    priority_str = lead_output.get("lead_priority", "MEDIUM")
                    is_disqualified = lead_output.get("disqualified", False)

                    lead_status = (
                        LeadStatus.DISQUALIFIED if is_disqualified else LeadStatus.QUALIFIED
                    )

                    lead_metadata = {
                        "source": "s3_mapping",
                        "lead_priority": priority_str,
                        "s3_mapping_lead_output": lead_output,
                    }

                    lead_service.create_lead(LeadCreate(
                        tenant_id=request.tenant_id,
                        conversation_id=conversation.conversation_id,
                        name=name or None,
                        phone=phone,
                        status=lead_status,
                        metadata=lead_metadata,
                        message=f"S3 Mapping lead: {priority_str}",
                    ))
                    conversation_service.mark_lead_captured(
                        conversation.conversation_id, name=name, phone=phone
                    )
                    logger.info(
                        f"S3 Mapping lead captured: priority={priority_str}, "
                        f"total_questions={lead_output.get('total_questions')}"
                    )

            # Handle terminal button actions for S3 Mapping (after lead already captured)
            if state_result.action == "disqualify_lead":
                from app.services import lead_service
                from app.models.lead import LeadStatus
                lead = lead_service.get_conversation_lead(conversation.conversation_id)
                if lead:
                    lead_service.update_lead_status(lead.lead_id, LeadStatus.DISQUALIFIED)
                    logger.info(f"S3 Mapping: Lead {lead.lead_id} DISQUALIFIED via 'Maybe later'")

            if state_result.action == "schedule_call":
                from app.services import lead_service
                lead = lead_service.get_conversation_lead(conversation.conversation_id)
                if lead:
                    updates = {"metadata": dict(lead.metadata or {})}
                    updates["metadata"]["call_requested"] = True
                    lead_service.update_lead_details(lead.lead_id, updates)
                    logger.info(f"S3 Mapping: Lead {lead.lead_id} requested call via button")


            # Save assistant message
            assistant_message = conversation_service.add_message(
                conversation.conversation_id,
                role="assistant",
                content=answer,
                sources=sources,
            )

            # Determine if this is a terminal state — tell frontend to end the session
            TERMINAL_ACTIONS = {"show_summary", "disqualified", "ended", "schedule_call", "disqualify_lead"}
            s3_mapping_suggested_actions = []
            if state_result.action in TERMINAL_ACTIONS:
                s3_mapping_suggested_actions.append("chat_ended")

            # Build and return response immediately
            return ChatResponse(
                conversation_id=conversation.conversation_id,
                message_id=assistant_message.message_id,
                answer=answer,
                sources=[],
                current_state=state_result.next_state,
                current_step_index=0,
                total_steps=len(config.question_map or []),
                quick_replies=state_result.quick_replies,
                show_actions=state_result.show_actions,
                summary_data=state_result.summary_data,
                s3_lead_output=state_result.lead_output,
                estimated_progress=None,
                suggested_actions=s3_mapping_suggested_actions,
            )

        else:
            # S1 (default)
            logger.info(f"Using S1 state machine for tenant plan: {config.plan}")
            state_result = state_machine.process_message(
                conversation=conversation,
                user_message=request.query,
                tenant_config=config,
            )

        logger.info(
            f"State machine result: action={state_result.action}, next_state={state_result.next_state}"
        )

        # Generate response based on state machine decision
        answer = ""
        sources = []

        if state_result.action == "ask_contact":
            # Simple greeting + ask for contact info
            answer = state_result.question_to_ask

        elif state_result.action == "ask_question":
            # Ask qualification question naturally
            answer = _generate_question_response(
                question=state_result.question_to_ask,
                step_index=state_result.step_index,
                total_steps=total_steps,
                config=config,
            )

            # S3 provides a conversational transition that must be prepended
            # so it gets saved to the database history correctly
            transition = getattr(state_result, "conversational_transition", "") or ""
            if transition:
                answer = f"{transition} {answer}"

        elif state_result.action == "answer_and_ask":

            rag_answer, rag_sources = rag_service.answer_query_simple(

                query=state_result.rag_query,

                tenant_id=request.tenant_id,

                language=config.language

            )

            sources = rag_sources

            transition = getattr(state_result, "conversational_transition", None)

            parts = []

            if rag_answer:
                parts.append(rag_answer)

            if transition:
                parts.append(transition)

            if state_result.question_to_ask:
                parts.append(state_result.question_to_ask)

                answer = "\n\n".join(parts) if parts else ""
            else:
                answer = state_result.question_to_ask

        elif state_result.action == "show_summary":
            # Generate summary message from backend data
            answer = _generate_summary_message(state_result.summary_data, config)

        elif state_result.action == "ask_name":
            # Asking for name specifically (after phone was collected)
            answer = state_result.question_to_ask

        elif state_result.action == "acknowledge":
            # Simple acknowledgment for completed state
            answer = "Thank you! Would you like to schedule a call with our team?"

        elif state_result.action == "ended":
            answer = "Thank you for chatting with us! Have a great day."

        elif state_result.action == "disqualify_lead":
            # Explicit functionality for "Maybe later" button
            from app.services import lead_service
            from app.models.lead import LeadStatus

            lead = lead_service.get_conversation_lead(conversation.conversation_id)
            if lead:
                lead_service.update_lead_status(lead.lead_id, LeadStatus.DISQUALIFIED)
                logger.info(
                    f"Lead {lead.lead_id} explicitly DISQUALIFIED via 'Maybe later'"
                )

                # Also update metadata with downgraded S3 lead output if available
                s3_out = getattr(state_result, "s3_lead_output", None)
                if s3_out:
                    updated_metadata = dict(lead.metadata or {})
                    updated_metadata["s3_lead_output"] = s3_out
                    lead_service.update_lead_details(
                        lead.lead_id, {"metadata": updated_metadata}
                    )
                    logger.info(
                        f"Lead {lead.lead_id} metadata updated with downgraded priority: {s3_out.get('lead_priority')}"
                    )

            answer = "No problem at all. Feel free to reach out whenever you're ready."

        elif state_result.action == "schedule_call":
            # Explicit functionality for "Yes, schedule a call"
            from app.services import lead_service

            lead = lead_service.get_conversation_lead(conversation.conversation_id)
            if lead:
                # Update metadata to note the explicit call request, but keep status as is
                # (LQC should have already determined if they are QUALIFIED)
                updates = {"metadata": dict(lead.metadata or {})}
                updates["metadata"]["call_requested"] = True
                lead_service.update_lead_details(lead.lead_id, updates)
                logger.info(f"Lead {lead.lead_id} requested call via button")

            answer = "Thank you for confirming. Our team will contact you shortly to schedule the call."

        # S2/S3-specific actions
        elif state_result.action == "adaptive_ask":
            # S2/S3: Adaptive question with conversational transition
            transition = getattr(state_result, "conversational_transition", "") or ""
            question = state_result.question_to_ask or ""

            if transition:
                # Legacy behavior: combine transition + instruction
                answer = f"{transition} {question}"
            else:
                # New behavior: S2 LLM already generated the full, natural question
                answer = question

        elif state_result.action == "soft_disqualify":
            # S2: Soft disqualification - polite de-prioritization
            answer = (
                state_result.question_to_ask
                or "Based on what you shared, this might not be the right time. Would you like us to reconnect later?"
            )

        # Update extracted data and get the updated version
        current_data = dict(conversation.extracted_lead_data)
        if state_result.extracted_data:
            current_data.update(state_result.extracted_data)
            conversation_service.update_extracted_data(
                conversation.conversation_id, current_data
            )

            # If lead was already captured, update the Lead record too
            if conversation.lead_captured:
                from app.services import lead_service

                lead = lead_service.get_conversation_lead(conversation.conversation_id)
                if lead:
                    updates = {}
                    # Check for phone/name updates
                    if (
                        "phone" in state_result.extracted_data
                        and state_result.extracted_data["phone"]
                    ):
                        updates["phone"] = state_result.extracted_data["phone"]
                    if (
                        "name" in state_result.extracted_data
                        and state_result.extracted_data["name"]
                    ):
                        updates["name"] = state_result.extracted_data["name"]
                    # Update metadata with latest summary and S3 output
                    if state_result.summary_data:
                        new_metadata = dict(state_result.summary_data)
                        if (
                            hasattr(state_result, "s3_lead_output")
                            and state_result.s3_lead_output
                        ):
                            new_metadata["s3_lead_output"] = state_result.s3_lead_output
                        if (
                            hasattr(state_result, "s2_lead_output")
                            and state_result.s2_lead_output
                        ):
                            new_metadata["s2_lead_output"] = state_result.s2_lead_output
                        updates["metadata"] = new_metadata

                    if updates:
                        lead_service.update_lead_details(lead.lead_id, updates)
                        logger.info(
                            f"Updated lead {lead.lead_id} with: {list(updates.keys())}"
                        )

        # Calculate name_ask_count for next state
        # Increment if state machine signals we should (asked for name)
        new_name_ask_count = conversation.name_ask_count
        if state_result.increment_name_ask:
            new_name_ask_count += 1

        # Update state machine fields
        conversation_service.update_conversation_state(
            conversation_id=conversation.conversation_id,
            current_state=state_result.next_state,
            step_index=state_result.step_index,
            ignore_count=0
            if state_result.action == "ask_question"
            else conversation.step_ignore_count + 1,
            name_ask_count=new_name_ask_count,
        )

        # ============================================================
        # LEAD CAPTURE - Backend controlled, not LLM-dependent
        # ============================================================

        suggested_actions = []

        if (
            state_result.next_state == ConversationState.COMPLETED.value
            and state_result.summary_data
        ):
            # Create lead from backend-generated summary
            if not conversation.lead_captured:
                from app.services import lead_service
                from app.models.lead import LeadCreate, LeadStatus
                from app.services.lead.lqc_evaluator import lqc_evaluator

                # CRITICAL: ALWAYS evaluate against LQC criteria
                # No more auto-qualification just because they have phone
                has_phone = bool(current_data.get("phone"))

                if not has_phone:
                    # Missing mandatory phone - cannot create lead yet
                    lead_status = LeadStatus.NEW
                    logger.warning("Cannot create lead - missing phone number")
                else:
                    # Has phone - now evaluate against LQC
                    if config.lead_qualification_criteria:
                        # Criteria defined - MUST evaluate
                        logger.info(
                            f"Evaluating LQC: {config.lead_qualification_criteria}"
                        )

                        is_qualified = lqc_evaluator.evaluate_criteria(
                            answers=current_data,
                            questions=questions,
                            criteria=config.lead_qualification_criteria,
                        )

                        if is_qualified:
                            lead_status = LeadStatus.QUALIFIED
                            logger.info("LQC Result: QUALIFIED ✅")
                        else:
                            lead_status = LeadStatus.DISQUALIFIED
                            logger.info("LQC Result: DISQUALIFIED ❌")
                    else:
                        # NO criteria defined - treat as DISQUALIFIED
                        # Tenant MUST define criteria to qualify leads
                        lead_status = LeadStatus.DISQUALIFIED
                        logger.warning(
                            "No LQC criteria defined - marking as DISQUALIFIED"
                        )

                # Prepare metadata with S3 output if available
                lead_metadata = dict(state_result.summary_data or {})
                if (
                    hasattr(state_result, "s3_lead_output")
                    and state_result.s3_lead_output
                ):
                    lead_metadata["s3_lead_output"] = state_result.s3_lead_output
                if (
                    hasattr(state_result, "s2_lead_output")
                    and state_result.s2_lead_output
                ):
                    lead_metadata["s2_lead_output"] = state_result.s2_lead_output

                # Create lead with determined status
                lead_create = LeadCreate(
                    tenant_id=request.tenant_id,
                    conversation_id=conversation.conversation_id,
                    name=current_data.get("name"),
                    phone=current_data.get("phone"),
                    message="Lead captured via chat",
                    status=lead_status,
                    metadata=lead_metadata,
                )
                lead_service.create_lead(lead_create)
                conversation_service.mark_lead_captured(conversation.conversation_id)

                # Push QUALIFIED leads to Bitrix24 CRM (global — all tenants)
                if lead_status == LeadStatus.QUALIFIED:
                    from app.services.crm.bitrix_service import push_lead_to_bitrix
                    push_lead_to_bitrix(
                        company_name=config.company_name or tenant.name,
                        name=current_data.get("name"),
                        phone=current_data.get("phone"),
                        metadata=lead_metadata,
                    )

                    if config.plan in [Plan.S1, Plan.S2]:
                        suggested_actions.append("schedule_call")

            suggested_actions.append("lead_captured")

        assistant_message = conversation_service.add_message(
            conversation.conversation_id,
            role="assistant",
            content=answer,
            sources=sources,
        )

        return ChatResponse(
            answer=answer,
            conversation_id=conversation.conversation_id,
            message_id=assistant_message.message_id,
            sources=sources,
            suggested_actions=suggested_actions,
            # Explicit state info - frontend uses these, no more text parsing!
            current_state=state_result.next_state,
            current_step_index=state_result.step_index,
            total_steps=total_steps,
            summary_data=state_result.summary_data,
            show_actions=state_result.show_actions,
            quick_replies=state_result.quick_replies,
            # S3-specific fields
            s3_lead_output=getattr(state_result, "s3_lead_output", None),
            conversational_transition=getattr(
                state_result, "conversational_transition", None
            ),
        )

    except HTTPException:
        raise
    except Exception:
        logger.error("Chat error", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Chat processing failed. Please try again later.",
        )


def _translate_text(text: str, language_name: str) -> str:
    """Translate text to target language."""
    try:
        prompt = f"""
Translate the following text to {language_name}.

Text:
{text}

Return ONLY the translated text.
"""

        return llm_service.generate_response(prompt).strip()

    except Exception as e:
        logger.error(f"Translation failed: {e}")
        return text

def _generate_question_response(
    question: str, step_index: int, total_steps: int, config
) -> str:
    """Generate a natural response that asks the qualification question."""
    try:
        # Varied conversation starters based on step position
        if step_index == 0:
            context = "This is the first question, so be welcoming and warm."
        elif step_index == total_steps - 1:
            context = "This is the last question, so acknowledge they're almost done."
        else:
            context = "This is a middle question, so keep the flow natural."

        language_name = get_language_name(config.language)
        translated_question = _translate_text(question, language_name)

        prompt = f"""You're a friendly sales assistant having a natural chat. Ask this question in a conversational way.
CRITICAL LANGUAGE RULE:
You MUST respond ONLY in {language_name}.
Do NOT use English.
Do NOT translate to English.
Entire response must be in {language_name} only.

Question to ask: "{translated_question}"
Context: {context}

CRITICAL RULES:
1. DO NOT use phrases like "Just curious", "I was wondering", "Quick question" - these sound robotic when repeated
2. Keep it to 1-2 short sentences maximum
3. Be warm and friendly, like talking to a friend
4. Vary your approach - sometimes be direct, sometimes add a brief friendly comment
5. End with the question naturally
6. No emojis unless absolutely natural
7. Respond ONLY in {language_name}

Now write your response (1-2 sentences max):"""

        response = llm_service.generate_response(prompt)
        return response.strip()
    except Exception as e:
        logger.error(f"Failed to generate question response: {e}")
        return question  # Fallback to raw question



def _weave_answer_and_question(rag_answer: str, question: str, config) -> str:
    """Weave RAG answer with qualification question into one natural response."""
    try:
        language_name = get_language_name(config.language)

        # Translate both answer and question first
        translated_rag = _translate_text(rag_answer, language_name)
        translated_question = _translate_text(question, language_name)

        prompt = f"""
You are a friendly sales assistant.

CRITICAL LANGUAGE RULE:
You MUST respond ONLY in {language_name}.
Do NOT use English.
Do NOT mix languages.
Entire response must be fully in {language_name}.

The user's question was answered with:
"{translated_rag}"

Now ask this follow-up question naturally:
"{translated_question}"

RULES:
1. Keep total response under 4 sentences
2. First answer the user's question briefly
3. Then naturally ask the follow-up question
4. Sound human and conversational
5. Avoid robotic transitions
6. Output ONLY in {language_name}

Response:
"""

        response = llm_service.generate_response(prompt)
        return response.strip()

    except Exception as e:
        logger.error(f"Failed to weave response: {e}")
        return f"{rag_answer} {question}"

def _generate_summary_message(summary_data: dict, config) -> str:
    """Generate a friendly summary message from the backend-created summary."""
    lang = config.language or "en"
    l = get_message(lang, "summary")

    if not summary_data:
        return f"{l['intro']} {l['outro']}"

    # Build summary text
    parts = []
    parts.append(f"**{l['name']}**: {summary_data.get('name', l['not_provided'])}")
    parts.append(f"**{l['phone']}**: {summary_data.get('phone', l['not_provided'])}")

    # Add question answers
    for key, value in summary_data.items():
        if key.startswith("q") and isinstance(value, dict):
            parts.append(
                f"**{value.get('label', key)}**: {value.get('answer', l['not_provided'])}"
            )

    summary_text = "\n".join(parts)

    return f"{l['intro']}\n\n{summary_text}\n\n{l['outro']}"


# =============================================
# Conversation Management Endpoints
# =============================================


@router.get("/conversations/{conversation_id}", response_model=Conversation)
async def get_conversation(
    conversation_id: str,
    current_user: UserResponse = Depends(get_current_user),
):
    """Get conversation by ID."""
    conversation = conversation_service.get_conversation(conversation_id)
    if not conversation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
        )

    # Validate user has access to this tenant
    await validate_tenant_access(conversation.tenant_id, current_user)

    return conversation


@router.get("/conversations/{conversation_id}/messages", response_model=List[Message])
async def get_conversation_messages(
    conversation_id: str,
    limit: int = 50,
    current_user: UserResponse = Depends(get_current_user),
):
    """Get messages for a conversation (requires auth)."""
    try:
        # Validate user has access to this conversation's tenant
        conversation = conversation_service.get_conversation(conversation_id)
        if not conversation:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
            )

        await validate_tenant_access(conversation.tenant_id, current_user)

        return conversation_service.get_messages(conversation_id, limit=limit)
    except HTTPException:
        raise
    except Exception:
        logger.error("Request failed", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Request failed. Please try again later.",
        )


@router.get("/tenants/{tenant_id}/conversations", response_model=List[Conversation])
async def get_tenant_conversations(
    tenant_id: str,
    skip: int = Query(0, ge=0),
    limit: int = Query(500, ge=1, le=1000),  # hard-capped (was 10000) to prevent unbounded loads
    current_user: UserResponse = Depends(get_current_user),
):
    """Get all conversations for a tenant (requires auth and tenant access)."""
    try:
        await validate_tenant_access(tenant_id, current_user)
        return conversation_service.get_tenant_conversations(
            tenant_id, skip=skip, limit=limit
        )
    except HTTPException:
        raise
    except Exception:
        logger.error("Request failed", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Request failed. Please try again later.",
        )


@router.get("/tenants/{tenant_id}/conversations/count")
async def get_tenant_conversations_count(
    tenant_id: str,
    current_user: UserResponse = Depends(get_current_user),
):
    """Get total count of all conversations for a tenant."""
    try:
        await validate_tenant_access(tenant_id, current_user)
        count = conversation_service.get_tenant_conversation_count(tenant_id)
        return {"count": count}
    except HTTPException:
        raise
    except Exception:
        logger.error("Request failed", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Request failed. Please try again later.",
        )

@router.post("/widget/validate")
async def validate_widget(
    request: Request,
    widget_key: str = Header(..., alias="X-Widget-Key"),
    tenant_id: str = Header(..., alias="X-Tenant-Id"),
):
    # Get tenant
    tenant = tenant_service.get_tenant(tenant_id)
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant not found",
        )

    # Validate widget key
    if tenant.config.api_key != widget_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid widget key",
        )

    # Validate origin
    origin = request.headers.get("origin")

    if not origin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing origin header",
        )

    if origin not in tenant.config.allowed_origins:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Unauthorized domain",
        )

    return {
        "success": True,
        "tenant_name": tenant.name,
    }

# === SESSION MANAGEMENT ENDPOINTS ===
# Deprecated: DynamoDB service is stateless regarding "sessions" but preserves history.
# Endpoints removed as part of MongoDB cleanup.
