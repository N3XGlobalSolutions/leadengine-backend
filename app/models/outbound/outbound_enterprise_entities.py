"""Enterprise SaaS enhancements data models for the Outbound AI Sales Agent."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class CustomerIntentType(str, Enum):
    """SaaS classification of client intents."""
    BUYING = "BUYING"
    CURIOUS = "CURIOUS"
    PRICING = "PRICING"
    SUPPORT_QUESTION = "SUPPORT_QUESTION"
    COMPARISON = "COMPARISON"
    SCHEDULING = "SCHEDULING"
    NEGOTIATION = "NEGOTIATION"
    COLD = "COLD"
    WARM = "WARM"
    HOT = "HOT"
    LOST = "LOST"
    RETURNING = "RETURNING"


class SalesGoal(str, Enum):
    """Dynamic conversation objectives indicating current target state."""
    COLD_OUTREACH = "COLD_OUTREACH"
    IDENTIFY_DECISION_MAKER = "IDENTIFY_DECISION_MAKER"
    COLLECT_REQUIREMENTS = "COLLECT_REQUIREMENTS"
    PRODUCT_RECOMMENDATION = "PRODUCT_RECOMMENDATION"
    COLLECT_BUDGET = "COLLECT_BUDGET"
    BOOK_MEETING = "BOOK_MEETING"
    UPSELL = "UPSELL"
    CROSS_SELL = "CROSS_SELL"
    CLOSE_SALE = "CLOSE_SALE"
    REACTIVATE = "REACTIVATE"


class ObjectionType(str, Enum):
    """Categorized objections requiring tailored counter-strategies."""
    PRICE = "PRICE"
    TRUST = "TRUST"
    COMPETITOR = "COMPETITOR"
    TIMING = "TIMING"
    NEED = "NEED"
    AUTHORITY = "AUTHORITY"
    FEATURE = "FEATURE"
    IMPLEMENTATION = "IMPLEMENTATION"
    ROI = "ROI"


class CustomerPersona(BaseModel):
    """Evolving customer profile built dynamically from conversation history."""
    industry: str = ""
    role: str = ""
    company_size: str = ""
    pain_points: List[str] = Field(default_factory=list)
    goals: List[str] = Field(default_factory=list)
    budget_limit: Optional[float] = None
    communication_style: str = "Informative"
    preferred_language: str = "en"
    technical_knowledge: str = "General"  # e.g. "Basic", "Advanced"
    buying_behavior: str = "Neutral"       # e.g. "Price-Sensitive", "Decisive"


class LeadQualification(BaseModel):
    """Automated lead qualification state tracking."""
    lead_score: int = 0                     # 0-100 scale
    buying_intent_score: int = 0            # 0-100 scale
    engagement_score: int = 0               # 0-100 scale
    qualification_status: str = "UNQUALIFIED" # "UNQUALIFIED", "PARTIALLY_QUALIFIED", "QUALIFIED"
    decision_maker_probability: float = 0.0 # 0.0-1.0
    budget_confirmed: bool = False
    timeline_confirmed: bool = False


class ConversationHealth(BaseModel):
    """Conversation metric trackers for risk levels and scheduling momentum."""
    risk_score: int = 0                     # 0-100 scale
    momentum_score: int = 50                # 0-100 scale
    engagement_index: int = 50              # 0-100 scale
    customer_interest_level: str = "MEDIUM" # "LOW", "MEDIUM", "HIGH"
    conversion_probability: float = 0.0     # 0.0-1.0


class MultiStepPlan(BaseModel):
    """Pre-computed agent actions list to complete before emitting answers."""
    current_objective: str = ""
    steps: List[str] = Field(default_factory=list)
    next_best_action: str = ""
    missing_information: List[str] = Field(default_factory=list)
    risk_level: str = "LOW"                 # "LOW", "MEDIUM", "HIGH"


class AIReasoningMetadata(BaseModel):
    """Trace logs of the AI's internal selection rationale for audit reviews."""
    reasoning_id: str
    conversation_id: str
    message_id: str
    selected_strategy: str
    strategy_rationale: str
    active_goal: SalesGoal
    goal_change_rationale: Optional[str] = None
    stage_change_rationale: Optional[str] = None
    tool_rationale: Dict[str, str] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class SalesMemory(BaseModel):
    """Composite sales memory tracking customer and conversation metrics."""
    persona: CustomerPersona = Field(default_factory=CustomerPersona)
    qualification: LeadQualification = Field(default_factory=LeadQualification)
    health: ConversationHealth = Field(default_factory=ConversationHealth)
    active_goal: SalesGoal = SalesGoal.COLD_OUTREACH
    detected_objections: List[ObjectionType] = Field(default_factory=list)
    purchase_signals: List[str] = Field(default_factory=list)
    commitments: List[str] = Field(default_factory=list)
    open_questions: List[str] = Field(default_factory=list)
    last_reasoning: Optional[str] = None
