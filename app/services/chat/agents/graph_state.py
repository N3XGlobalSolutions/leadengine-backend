"""S3 Max LangGraph State Schema.

Shared state that all agent nodes read from and write to.
Persisted as a JSON blob in Conversation.s3_max_state between messages.
"""

from typing import Any, Dict, List, Optional
from typing_extensions import TypedDict


class ScorecardEntry(TypedDict, total=False):
    """Single dimension in the qualification scorecard."""
    value: str
    confidence: float  # 0.0 - 1.0
    source_question: str
    raw_answer: str


class S3MaxState(TypedDict, total=False):
    """Full state for the S3 Max LangGraph.

    This state flows through all agent nodes. Each node reads what it
    needs and writes back its updates. The state is persisted to DynamoDB
    between user messages.
    """

    # ── Identifiers ──
    tenant_id: str
    conversation_id: str

    # ── Conversation ──
    messages: List[Dict[str, str]]       # Full chat history [{role, content}]
    current_message: str                  # Latest user message

    # ── Contact info ──
    phone: Optional[str]
    name: Optional[str]

    # ── Qualification tracking ──
    scorecard: Dict[str, ScorecardEntry]  # {dimension: {value, confidence, source_question}}
    questions_asked: int                  # Strict counter (3-20 guardrail)
    question_history: List[str]           # Previously asked questions (dedup)
    current_question_id: str              # Which Q was just answered

    # ── Question bank ──
    question_bank: List[Dict[str, Any]]   # Pre-generated from KB

    # ── LQC ──
    lqc_criteria: str                     # Natural language criteria
    covered_dimensions: List[str]         # What's been asked about
    required_dimensions: List[str]        # Must-cover dimensions

    # ── Confidence & control ──
    confidence_score: float               # 0.0-1.0, triggers early exit at >0.85
    safety_net_triggered: bool            # True if hit 20 questions
    evasion_count: int                    # Consecutive evasive responses
    is_disinterested: bool                # True if user explicitly states they are not interested

    # ── Knowledge base context ──
    kb_context: str                       # Retrieved chunks for question generation
    company_name: str                     # Tenant's company name for personalisation
    language: str                         # Tenant's preferred language (en, hi, ta)

    # ── Output (read by the adapter after graph completes) ──
    bot_response: str                     # Response to send back to user
    quick_replies: List[str]              # Optional quick reply buttons
    estimated_progress: float             # 0.0-1.0 for frontend progress bar
    is_complete: bool                     # Qualification done?
    lead_verdict: Optional[str]           # QUALIFIED / DISQUALIFIED
    lead_output: Optional[Dict[str, Any]] # Rich output for sales team
    next_state: str                       # ConversationState value to transition to
    show_actions: bool                    # Whether to show action buttons

    # ── Router decision ──
    route: str  # greeting | contact | response | question | off_topic
    just_answered_rag: bool               # True if RAG answered in this pass
    is_skeptical_intent: bool             # True if few-shot router classified as skeptical

    # ── Contact Consent ──
    contact_consent_asked: bool           # True once the consent question has been sent
    contact_consent_answer: Optional[str] # "yes" / "no" / "maybe" (stored after user replies)
