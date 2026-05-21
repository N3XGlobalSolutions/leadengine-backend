"""Conversation and message models."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ConversationState(str, Enum):
    """State machine states for qualification flow."""

    GREETING = "greeting"  # Initial state - just started
    AWAITING_CONTACT = "contact"  # MANDATORY: Collecting name + phone
    QUALIFYING = "qualifying"  # Going through qualification questions
    QUALIFYING_AGENTIC = "qualifying_agentic"  # S3 Max: LangGraph-driven qualification
    COMPLETED = "completed"  # All questions done, summary shown
    ENDED = "ended"  # User clicked action button


class Message(BaseModel):
    """Chat message model."""

    message_id: str
    conversation_id: str
    role: str  # "user" or "assistant"
    content: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    sources: List[str] = []  # Source documents for assistant messages


class Conversation(BaseModel):
    """Conversation model with state machine tracking."""

    conversation_id: str
    tenant_id: str
    user_identifier: Optional[str] = None  # Email, phone, or anonymous ID
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_message_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    message_count: int = 0
    is_active: bool = True
    lead_captured: bool = False
    extracted_lead_data: Dict[str, Any] = {}  # Format: {"name": "John", "phone": "123"}
    lead_status: str = "collecting"  # "collecting", "completed"

    # State machine fields
    current_state: str = ConversationState.GREETING.value  # Current state
    current_step_index: int = 0  # Which question we're on (0-based)
    step_ignore_count: int = 0  # How many times user ignored current question
    name_ask_count: int = 0  # How many times we've explicitly asked for name
    mandatory_collected: bool = False  # name+phone captured?

    # S3 Max agentic state (persisted as JSON blob in DynamoDB)
    s3_max_state: Optional[Dict[str, Any]] = None

    # S3 Mapping state (persisted as JSON blob in DynamoDB)
    s3_mapping_state: Optional[Dict[str, Any]] = None



# Request/Response models for chat
class ChatRequest(BaseModel):
    """Chat request model."""

    query: str
    tenant_id: str
    conversation_id: Optional[str] = None  # Create new if not provided
    user_identifier: Optional[str] = None
    api_key: Optional[str] = None  # For widget authentication


class ChatResponse(BaseModel):
    """Chat response model with explicit state info."""

    answer: str
    conversation_id: str
    message_id: str
    sources: List[str] = []
    suggested_actions: List[str] = []  # e.g., ["lead_captured"]

    # Explicit state info - NO MORE PARSING FROM TEXT!
    current_state: str = ""  # "greeting", "contact", "qualifying", "completed"
    current_step_index: int = 0  # 0-based index for progress
    total_steps: int = 0  # Total qualification questions
    summary_data: Optional[Dict[str, Any]] = None  # Backend-created summary
    show_actions: bool = False  # Explicit flag for action buttons
    quick_replies: List[str] = []  # Quick reply options for current question

    # S3-specific fields
    s3_lead_output: Optional[Dict[str, Any]] = None  # Rich output for sales (S3 only)
    conversational_transition: Optional[str] = (
        None  # S2/S3 acknowledgment before question
    )

    # S3 Max fields
    s3_max_lead_output: Optional[Dict[str, Any]] = None  # Rich agentic output
    estimated_progress: Optional[float] = None  # 0.0 - 1.0 for dynamic progress bar
