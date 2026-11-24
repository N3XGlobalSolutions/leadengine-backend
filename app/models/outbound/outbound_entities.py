"""Outbound AI specific database entities and Pydantic models."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, field_validator
from decimal import Decimal


class ConversationStage(str, Enum):
    """Lifecycle stages of outbound sales conversations."""

    NEW = "NEW"
    CONTACTED = "CONTACTED"
    ENGAGED = "ENGAGED"
    QUALIFIED = "QUALIFIED"
    PRODUCT_DISCUSSION = "PRODUCT_DISCUSSION"
    PRICING = "PRICING"
    NEGOTIATION = "NEGOTIATION"
    MEETING_REQUESTED = "MEETING_REQUESTED"
    MEETING_BOOKED = "MEETING_BOOKED"
    FOLLOW_UP = "FOLLOW_UP"
    WON = "WON"
    LOST = "LOST"

    @classmethod
    def from_value(cls, value: Any) -> "ConversationStage":
        """Safely parse or map raw string/enum value to a valid ConversationStage."""
        if isinstance(value, cls):
            return value
        if not value or not isinstance(value, str):
            return cls.NEW

        # Try direct Enum match
        try:
            return cls(value)
        except ValueError:
            pass

        # Handle legacy / SalesGoal values stored by past bug
        sales_goal_mapping = {
            "COLD_OUTREACH": cls.CONTACTED,
            "IDENTIFY_DECISION_MAKER": cls.ENGAGED,
            "COLLECT_REQUIREMENTS": cls.QUALIFIED,
            "PRODUCT_RECOMMENDATION": cls.PRODUCT_DISCUSSION,
            "COLLECT_BUDGET": cls.PRICING,
            "BOOK_MEETING": cls.MEETING_REQUESTED,
            "CLOSE_SALE": cls.NEGOTIATION,
            "UPSELL": cls.WON,
            "CROSS_SELL": cls.WON,
            "REACTIVATE": cls.CONTACTED,
        }
        return sales_goal_mapping.get(value, cls.NEW)



class ConversationStageManager:
    """Validates legal transitions between conversation stages."""

    # Set of valid next stages from any given stage
    VALID_TRANSITIONS: Dict[ConversationStage, List[ConversationStage]] = {
        ConversationStage.NEW: [ConversationStage.CONTACTED, ConversationStage.LOST],
        ConversationStage.CONTACTED: [ConversationStage.ENGAGED, ConversationStage.LOST],
        ConversationStage.ENGAGED: [ConversationStage.QUALIFIED, ConversationStage.PRODUCT_DISCUSSION, ConversationStage.LOST],
        ConversationStage.QUALIFIED: [ConversationStage.PRODUCT_DISCUSSION, ConversationStage.PRICING, ConversationStage.LOST],
        ConversationStage.PRODUCT_DISCUSSION: [ConversationStage.PRICING, ConversationStage.MEETING_REQUESTED, ConversationStage.LOST],
        ConversationStage.PRICING: [ConversationStage.NEGOTIATION, ConversationStage.MEETING_REQUESTED, ConversationStage.LOST],
        ConversationStage.NEGOTIATION: [ConversationStage.MEETING_REQUESTED, ConversationStage.WON, ConversationStage.LOST],
        ConversationStage.MEETING_REQUESTED: [ConversationStage.MEETING_BOOKED, ConversationStage.NEGOTIATION, ConversationStage.LOST],
        ConversationStage.MEETING_BOOKED: [ConversationStage.FOLLOW_UP, ConversationStage.WON, ConversationStage.LOST],
        ConversationStage.FOLLOW_UP: [ConversationStage.MEETING_REQUESTED, ConversationStage.WON, ConversationStage.LOST],
        ConversationStage.WON: [],
        ConversationStage.LOST: [ConversationStage.NEW, ConversationStage.CONTACTED]  # Allow re-engagement
    }

    @classmethod
    def is_transition_valid(cls, current: ConversationStage, target: ConversationStage) -> bool:
        """Verify if a transition from current to target is allowed."""
        if current == target:
            return True
        allowed = cls.VALID_TRANSITIONS.get(current, [])
        return target in allowed


class OutboundConversation(BaseModel):
    """Tracks metadata of an outbound AI sales conversation."""

    conversation_id: str
    tenant_id: str
    lead_id: str
    campaign_id: str
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_message_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    stage: ConversationStage = ConversationStage.NEW
    status: str = "ACTIVE"  # "ACTIVE", "COMPLETED", "PAUSED"
    message_count: int = 0
    response_time_sum: float = 0.0  # Sum of latencies in seconds
    response_count: int = 0  # Number of replies from AI
    token_usage_sum: int = 0  # Total tokens consumed in conversation


class OutboundAIMessage(BaseModel):
    """Individual message (user or assistant) in an OutboundConversation."""

    message_id: str
    conversation_id: str
    role: str  # "user", "assistant", "system", "tool"
    content: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    tool_calls: Optional[List[Dict[str, Any]]] = None
    prompt_version: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)  # tokens, latency, cost, sources


class OutboundMemory(BaseModel):
    """Structured memory profile updated dynamically by the agent."""

    conversation_id: str
    conversation_summary: str = ""
    interested_products: List[str] = Field(default_factory=list)
    budget: Optional[str] = None
    preferred_language: str = "en"
    meeting_status: str = "PENDING"  # "PENDING", "BOOKED", "CANCELLED", "RESCHEDULED"
    objections: List[str] = Field(default_factory=list)
    preferences: Dict[str, Any] = Field(default_factory=dict)
    customer_intent: str = ""
    last_action: str = ""
    conversation_stage: ConversationStage = ConversationStage.NEW


class OutboundProduct(BaseModel):
    """Represents a product in the Product Catalog."""

    product_id: str
    tenant_id: str
    name: str
    description: str
    short_description: str
    price: Decimal
    currency: str = "USD"
    images: List[str] = Field(default_factory=list)
    gallery: List[str] = Field(default_factory=list)
    videos: List[str] = Field(default_factory=list)
    features: List[str] = Field(default_factory=list)
    benefits: List[str] = Field(default_factory=list)
    specifications: Dict[str, str] = Field(default_factory=dict)
    category: str
    tags: List[str] = Field(default_factory=list)
    brochure_pdf: Optional[str] = None
    status: str = "ACTIVE"  # "ACTIVE", "INACTIVE"
    search_keywords: List[str] = Field(default_factory=list)


class OutboundAgentSettings(BaseModel):
    """AI Voice and capability settings for a tenant's outbound agent."""

    tenant_id: str
    ai_name: str = "Sales Assistant"
    business_summary: str = ""
    sales_tone: str = "Professional and helpful"  # "Professional", "Friendly", "Direct"
    conversation_style: str = "Informative and natural"
    target_audience: str = "General business clients"
    primary_language: str = "en"
    max_response_length: int = 1500  # Characters
    enable_scheduling: bool = True
    enable_product_search: bool = True
    enable_knowledge_search: bool = True


class OutboundCalendarSettings(BaseModel):
    """OAuth and calendar integration credentials for a tenant."""

    tenant_id: str
    google_client_id: Optional[str] = None
    google_client_secret: Optional[str] = None
    google_refresh_token: Optional[str] = None  # Store encrypted
    calendar_id: str = "primary"
    timezone: str = "UTC"
    working_days: List[str] = Field(default_factory=lambda: ["monday", "tuesday", "wednesday", "thursday", "friday"])
    working_hours_start: str = "09:00"  # HH:MM
    working_hours_end: str = "17:00"    # HH:MM
    meeting_duration: int = 30           # Minutes
    buffer: int = 15                     # Minutes between slot blockages


class OutboundCalendarMeeting(BaseModel):
    """Tracks a scheduled meeting internally."""

    meeting_id: str
    tenant_id: str
    lead_id: str
    conversation_id: str
    event_id: str  # Google Calendar event ID
    summary: str
    description: str
    start_time: datetime
    end_time: datetime
    timezone: str
    attendee_name: str
    attendee_email: str
    attendee_phone: str
    meet_link: Optional[str] = None
    status: str = "BOOKED"  # "BOOKED", "CANCELLED", "RESCHEDULED"


class OutboundConversationAnalytics(BaseModel):
    """Analytics aggregated metrics for outbound agent performance."""

    tenant_id: str
    total_conversations: int = 0
    meetings_booked: int = 0
    products_shared: int = 0
    knowledge_searches: int = 0
    tool_usage: Dict[str, int] = Field(default_factory=dict)
    average_response_time: float = 0.0
    average_conversation_length: float = 0.0
    token_usage: int = 0
    conversion_rate: float = 0.0
