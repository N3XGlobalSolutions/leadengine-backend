"""Tenant models for multi-tenant configuration."""

import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


class Plan(str, Enum):
    """Subscription plan tiers."""

    S1 = "S1"          # Starter
    S2 = "S2"          # Professional
    S3 = "S3"          # Enterprise
    S3_INTERNATIONAL = "S3_INTERNATIONAL" # For international Clients
    S3_MAX = "S3_MAX"  # Enterprise Max (Agentic)
    S3_MAPPING = "S3_MAPPING"  # Enterprise Mapping (Structured deterministic questions with optional inline tables)


class SignalLevel(str, Enum):
    """Lead signal strength per answer option."""
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    DISQUALIFY = "disqualify"  # Immediate exit, send exit_message


class QuestionMapOption(BaseModel):
    """Single answer option inside a mapped question."""
    text: str                          # Display text (quick reply button label)
    signal: SignalLevel                # Lead signal this answer represents
    exit_message: Optional[str] = None  # Required when signal=DISQUALIFY


class QuestionMapTable(BaseModel):
    """Optional inline table to display alongside a question (e.g. ROI rate grid)."""
    headers: List[str]          # Column header labels
    rows: List[List[str]]       # Table rows, each row is a list of cell strings


class QuestionMapItem(BaseModel):
    """A single question in the admin-defined question map."""
    id: str                                # "Q1", "Q2", etc.
    question: str                          # The question text shown to user
    quick_replies: List[QuestionMapOption] # Answer options with signals
    table: Optional[QuestionMapTable] = None  # Optional inline table (rendered in chat UI)
    required: bool = True


class QuickReply(BaseModel):
    """Quick reply option for qualification questions."""

    id: str  # "1", "2", "3"
    text: str  # Display text
    value: Optional[str] = None  # Optional value if different from text


class QualificationQuestion(BaseModel):
    """Qualification question configuration."""

    id: str  # "Q1", "Q2", etc.
    label: str = "Question" # Display label
    instruction: str # The actual question text to ask
    collects: List[str] = [] # List of fields this question collects (e.g. ["name", "email"])
    quick_replies: List[QuickReply] = []  # Quick reply button options
    required: bool = True


class TenantConfig(BaseModel):
    """Enhanced configuration settings for a tenant."""

    # Subscription & Identification
    plan: Plan = Plan.S1
    url_slug: str = "default-tenant"  # e.g., "abc-corporation" for custom URLs

    # Bot Branding
    bot_name: str = "QBot Assistant"
    company_name: Optional[str] = None  # Display name for the company
    avatar_url: Optional[str] = None  # Bot avatar image URL

    # Colors (hex format)
    primary_color: str = "#3B82F6"  # Blue
    secondary_color: str = "#8B5CF6"  # Purple

    # UI Customization
    headline_prefix: str = "Welcome to"
    highlighted_headline: str = "QBot"  # Highlighted/bold part
    sub_headline: str = "How can I help you today?"
    key_features: List[str] = []  # Tags like ["24/7 Support", "Instant Answers"]

    # Qualification
    qualification_questions: List[QualificationQuestion] = []

    # Existing Legacy Fields
    branding: Optional[Dict[str, Any]] = None  # Deprecated, kept for backward compatibility
    welcome_message: str = "Hello! How can I help you today?"
    lead_form_enabled: bool = True
    lead_form_fields: List[str] = ["name", "phone"]
    language: str = "en"
    tone: str = "professional"  # professional, friendly, casual
    guardrails: List[str] = []  # List of topics to avoid
    max_conversation_length: int = 50
    lead_trigger_count: int = 3  # Messages before lead capture starts
    api_key: Optional[str] = None  # API key for widget authentication
    company_context: str = ""  # Company info for better LLM context

    #Clients domain for Widget Integration
    client_domain: Optional[str] = Field(
        default=None,
        description="Primary client website domain"
    )
    #Processed Clients Domain origins
    allowed_origins: List[str] = Field(
        default_factory=list,
        description="Allowed origins for widget requests"
    )
    
    # Lead Qualification Criteria
    lead_qualification_criteria: Optional[str] = Field(
        default=None,
        description="Natural language criteria for qualifying leads (e.g., 'Budget > $50k, timeline within 6 months')"
    )

    # S3 Max — Pre-generated question bank (populated on document upload)
    question_bank: Optional[List[Dict[str, Any]]] = Field(
        default=None,
        description="Pre-generated qualification questions from knowledge base for S3_MAX tier"
    )

    # S3 Mapping — admin-defined structured question map (max 30 questions, no branching)
    question_map: Optional[List[QuestionMapItem]] = Field(
        default=None,
        description="Structured question map with signal scoring. Used by S3_MAPPING plan. Max 30 deterministic questions. Each question may optionally carry an inline table."
    )

    @field_validator("allowed_origins")
    @classmethod
    def validate_allowed_origins(cls, v: List[str]) -> List[str]:

        normalized = []

        for origin in v:

            origin = origin.lower().rstrip("/")

            if not origin.startswith(("http://", "https://")):
                raise ValueError(
                    "Origins must start with http:// or https://"
                )

            normalized.append(origin)

        return normalized

    @field_validator("question_map")
    @classmethod
    def validate_question_map(cls, v):
        """Enforce max 30 admin-defined mapped questions."""
        if v is not None and len(v) > 30:
            raise ValueError("question_map cannot have more than 30 questions.")
        return v

    @field_validator("url_slug")
    @classmethod
    def validate_slug(cls, v: str) -> str:
        """Validate URL slug format (lowercase, alphanumeric, hyphens only)."""
        if not re.match(r"^[a-z0-9-]+$", v):
            raise ValueError(
                "URL slug must contain only lowercase letters, numbers, and hyphens"
            )
        if len(v) < 3:
            raise ValueError("URL slug must be at least 3 characters long")
        if len(v) > 50:
            raise ValueError("URL slug must be no more than 50 characters")
        return v

    @field_validator("primary_color", "secondary_color")
    @classmethod
    def validate_color(cls, v: str) -> str:
        """Validate hex color format (#RRGGBB)."""
        if not re.match(r"^#[0-9A-Fa-f]{6}$", v):
            raise ValueError("Color must be in hex format (#RRGGBB)")
        return v.upper()  # Normalize to uppercase

    @field_validator("bot_name")
    @classmethod
    def validate_bot_name(cls, v: str) -> str:
        """Validate bot name length."""
        if len(v) < 2:
            raise ValueError("Bot name must be at least 2 characters long")
        if len(v) > 50:
            raise ValueError("Bot name must be no more than 50 characters")
        return v


class TenantStatus(str, Enum):
    """Tenant status."""

    ACTIVE = "Active"
    INACTIVE = "Inactive"
    PAUSED = "Paused"
    ONBOARDING = "Onboarding"


class Tenant(BaseModel):
    """Complete tenant model."""

    tenant_id: str
    name: str
    config: TenantConfig = Field(default_factory=TenantConfig)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    status: TenantStatus = TenantStatus.ACTIVE
    is_active: bool = True  # Deprecated: Kept for backward compatibility
    is_deleted: bool = False  # Soft delete flag
    deleted_at: Optional[datetime] = None  # Soft delete timestamp
    created_by: Optional[str] = None  # Full name of the user who created the tenant


class TenantCreate(BaseModel):
    """Model for creating a new tenant."""

    name: str
    config: Optional[TenantConfig] = None
    status: Optional[TenantStatus] = TenantStatus.ONBOARDING
    created_by: Optional[str] = None


class TenantUpdate(BaseModel):
    """Model for updating tenant information."""

    name: Optional[str] = None
    config: Optional[TenantConfig] = None
    status: Optional[TenantStatus] = None
    is_active: Optional[bool] = None  # Deprecated
