"""S3 Max State Machine Adapter.

Wraps the LangGraph to be compatible with the existing chat.py route interface.
Handles:
- Loading/saving s3_max_state from/to conversation metadata in DynamoDB
- Invoking the LangGraph with the current state
- Returning a result object compatible with the chat route
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional

from app.core.logging_config import get_logger
from app.models.conversation import Conversation
from app.models.tenant import TenantConfig
from app.utils.localization import get_message, get_language_name
from app.services.chat.s3_max_graph import get_s3_max_graph
from app.services.chat.agents.graph_state import S3MaxState
from app.services.chat.agents.memory_agent import DEFAULT_REQUIRED_DIMENSIONS

logger = get_logger(__name__)


def _deserialize_dynamo(obj: Any) -> Any:
    """Recursively convert DynamoDB Decimal types to native Python int/float.
    
    DynamoDB stores all numbers as Decimal. When s3_max_state is persisted
    and reloaded, numeric fields become Decimal objects which crash on
    arithmetic operations with regular floats/ints.
    """
    if isinstance(obj, Decimal):
        return int(obj) if obj == int(obj) else float(obj)
    elif isinstance(obj, dict):
        return {k: _deserialize_dynamo(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_deserialize_dynamo(v) for v in obj]
    return obj


@dataclass
class S3MaxStateMachineResult:
    """Result from S3 Max state machine processing.
    
    Compatible with the existing chat route response building.
    """
    next_state: str
    action: str  # "greet", "ask_phone", "qualify", "answer_rag", "complete"
    step_index: int = 0
    question_to_ask: Optional[str] = None
    rag_query: Optional[str] = None
    summary_data: Optional[Dict[str, Any]] = None
    quick_replies: List[str] = field(default_factory=list)
    show_actions: bool = False
    extracted_data: Dict[str, str] = field(default_factory=dict)
    increment_name_ask: bool = False
    
    # S3 Max specific
    bot_response: str = ""
    s3_max_lead_output: Optional[Dict[str, Any]] = None
    estimated_progress: float = 0.0
    is_complete: bool = False
    lead_verdict: Optional[str] = None
    conversational_transition: Optional[str] = None
    
    # Full updated state to persist
    updated_s3_max_state: Optional[Dict[str, Any]] = None


class S3MaxChatStateMachine:
    """Adapter between LangGraph and the chat.py route interface."""

    def process_message(
        self,
        conversation: Conversation,
        message: str,
        tenant_config: TenantConfig,
        conversation_messages: Optional[List[Dict[str, str]]] = None,
    ) -> S3MaxStateMachineResult:
        """Process a user message through the S3 Max LangGraph.
        
        Args:
            conversation: Current conversation state
            message: User's message
            tenant_config: Tenant configuration
            conversation_messages: Optional chat history
            
        Returns:
            S3MaxStateMachineResult with response and updated state
        """
        # 1. Load or initialize state
        state = self._load_state(
            conversation, message, tenant_config, conversation_messages
        )

        # 2. Run the LangGraph
        graph = get_s3_max_graph()
        try:
            result_state = graph.invoke(state)
        except Exception as e:
            logger.error(
                f"S3 Max graph execution failed: {type(e).__name__}: {e}",
                exc_info=True,
            )
            return self._error_fallback(conversation, str(e))

        # 3. Convert graph output to result object
        return self._build_result(result_state, conversation)

    def _load_state(
        self,
        conversation: Conversation,
        message: str,
        tenant_config: TenantConfig,
        conversation_messages: Optional[List[Dict[str, str]]],
    ) -> S3MaxState:
        """Load state from conversation or initialize fresh."""
        
        # Retrieve persisted state if available
        # DynamoDB returns numbers as Decimal — convert to native int/float
        persisted = _deserialize_dynamo(conversation.s3_max_state or {})
        
        # Build messages list
        messages = []
        if conversation_messages:
            messages = conversation_messages
        
        # Add current message
        messages.append({"role": "user", "content": message})

        # Determine required dimensions from LQC
        required_dims = persisted.get(
            "required_dimensions",
            list(DEFAULT_REQUIRED_DIMENSIONS),
        )

        # Get question bank from tenant config
        question_bank = persisted.get(
            "question_bank",
            tenant_config.question_bank or [],
        )

        state: S3MaxState = {
            # Identifiers
            "tenant_id": conversation.tenant_id,
            "conversation_id": conversation.conversation_id,
            
            # Conversation
            "messages": messages,
            "current_message": message,
            
            # Contact (from persisted or extracted lead data)
            "phone": persisted.get("phone") or conversation.extracted_lead_data.get("phone"),
            "name": persisted.get("name") or conversation.extracted_lead_data.get("name"),
            
            # Qualification tracking
            "scorecard": persisted.get("scorecard", {}),
            "questions_asked": persisted.get("questions_asked", 0),
            "question_history": persisted.get("question_history", []),
            "current_question_id": persisted.get("current_question_id", ""),
            
            # Question bank
            "question_bank": question_bank,
            
            # LQC
            "lqc_criteria": tenant_config.lead_qualification_criteria or "",
            "covered_dimensions": persisted.get("covered_dimensions", []),
            "required_dimensions": required_dims,
            
            # Confidence
            "confidence_score": persisted.get("confidence_score", 0.0),
            "safety_net_triggered": persisted.get("safety_net_triggered", False),
            "evasion_count": persisted.get("evasion_count", 0),
            
            # KB context
            "kb_context": persisted.get("kb_context", ""),
            "company_name": tenant_config.company_name or "",
            "language": tenant_config.language or "en",
            
            # Output (will be populated by graph)
            "bot_response": "",
            "quick_replies": [],
            "estimated_progress": persisted.get("estimated_progress", 0.0),
            "is_complete": False,
            "lead_verdict": None,
            "lead_output": None,
            "next_state": conversation.current_state,
            "show_actions": False,
            "route": "",
            # Contact consent — persisted between turns
            "contact_consent_asked": persisted.get("contact_consent_asked", False),
            "contact_consent_answer": persisted.get("contact_consent_answer"),
        }

        return state

    def _build_result(
        self,
        state: S3MaxState,
        conversation: Conversation,
    ) -> S3MaxStateMachineResult:
        """Convert graph output state to S3MaxStateMachineResult."""
        
        next_state = state.get("next_state", conversation.current_state)
        is_complete = state.get("is_complete", False)
        bot_response = state.get("bot_response", "")
        lead_output = state.get("lead_output")
        
        # Map state to action
        action = "qualify"
        if next_state == "contact":
            action = "ask_phone" if not state.get("phone") else "ask_name"
        elif next_state == "greeting":
            action = "greet"
        elif next_state == "completed":
            action = "complete"
        elif next_state == "qualifying_agentic":
            action = "qualify"

        # Build extracted data from state
        extracted_data = {}
        if state.get("phone"):
            extracted_data["phone"] = state["phone"]
        if state.get("name"):
            extracted_data["name"] = state["name"]
        
        # Add scorecard data (coerce to string for consistent typing)
        for dim, entry in state.get("scorecard", {}).items():
            if isinstance(entry, dict):
                extracted_data[dim] = str(entry.get("value", ""))

        # Build summary for completed state
        summary_data: Dict[str, Any] | None = None
        if is_complete:
            summary_data = {
                "name": state.get("name", ""),
                "phone": state.get("phone", ""),
            }
            for dim, entry in state.get("scorecard", {}).items():
                if isinstance(entry, dict):
                    summary_data[dim] = {
                        "label": dim.replace("_", " ").title(),
                        "answer": entry.get("value", ""),
                    }
            if lead_output:
                summary_data["s3_max_lead_output"] = lead_output

        # Persist state for next message
        updated_state = {
            "phone": state.get("phone"),
            "name": state.get("name"),
            "scorecard": state.get("scorecard", {}),
            "questions_asked": state.get("questions_asked", 0),
            "question_history": state.get("question_history", []),
            "current_question_id": state.get("current_question_id", ""),
            "question_bank": state.get("question_bank", []),
            "covered_dimensions": state.get("covered_dimensions", []),
            "required_dimensions": state.get("required_dimensions", []),
            "confidence_score": state.get("confidence_score", 0.0),
            "safety_net_triggered": state.get("safety_net_triggered", False),
            "evasion_count": state.get("evasion_count", 0),
            "kb_context": state.get("kb_context", ""),
            "language": state.get("language", "en"),
            "estimated_progress": state.get("estimated_progress", 0.0),
            # Contact consent — must persist so router can intercept user's reply
            "contact_consent_asked": state.get("contact_consent_asked", False),
            "contact_consent_answer": state.get("contact_consent_answer"),
        }

        return S3MaxStateMachineResult(
            next_state=next_state,
            action=action,
            step_index=state.get("questions_asked", 0),
            question_to_ask=bot_response if action == "qualify" else None,
            rag_query=None,
            summary_data=summary_data,
            quick_replies=state.get("quick_replies", []),
            show_actions=state.get("show_actions", False),
            extracted_data=extracted_data,
            bot_response=bot_response,
            s3_max_lead_output=lead_output,
            estimated_progress=state.get("estimated_progress", 0.0),
            is_complete=is_complete,
            lead_verdict=state.get("lead_verdict"),
            updated_s3_max_state=updated_state,
        )

    def _error_fallback(
        self,
        conversation: Conversation,
        error_msg: str,
    ) -> S3MaxStateMachineResult:
        """Fallback when the graph fails."""
        logger.error(f"S3 Max fallback triggered: {error_msg}")
        
        persisted = conversation.s3_max_state or {}
        lang = persisted.get("language", "en")
        bot_response = get_message(lang, "technical_error")
        
        return S3MaxStateMachineResult(
            next_state=conversation.current_state,
            action="qualify",
            bot_response=bot_response,
            quick_replies=[],
            estimated_progress=0.0,
        )


# Singleton
s3_max_state_machine = S3MaxChatStateMachine()
