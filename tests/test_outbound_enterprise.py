"""Unit tests for the Outbound AI Sales Agent Enterprise SaaS enhancements."""

from datetime import datetime, date, timezone
from unittest.mock import MagicMock, patch
import pytest

from app.models.outbound.outbound_enterprise_entities import (
    CustomerIntentType,
    SalesGoal,
    ObjectionType,
    CustomerPersona,
    LeadQualification,
    ConversationHealth,
    SalesMemory,
)
from app.models.outbound.outbound_entities import OutboundAgentSettings, OutboundMemory
from app.services.ai.outbound.conversation_pipeline import OutboundAISession
from app.services.ai.outbound.enterprise_engine import (
    lead_qualification_engine,
    health_calculator,
    sales_strategy_engine,
    ai_guardrails,
)


# ── 1. BANT LEAD QUALIFICATION TESTS ──────────────────────────────────

def test_lead_qualification_scoring():
    session = OutboundAISession(
        tenant_id="tenant_123",
        campaign_id="camp_123",
        lead_id="lead_123",
        conversation_id="conv_123",
        sender_phone="+919876543210",
        incoming_text="My budget limit is around $5,000 for this project.",
        message_id="msg_abc"
    )
    session.history = []
    
    # Initialize blank sales memory
    sales_mem = SalesMemory()
    sales_mem.persona.pain_points = ["manual tracking"] # Needs identified (+25)
    
    # Evaluate qualification
    lead_qualification_engine.evaluate(session, sales_mem)
    
    # Budget confirmed in incoming text (+25)
    assert sales_mem.qualification.budget_confirmed is True
    # Lead score should be 75 (Budget + Need + Authority default)
    assert sales_mem.qualification.lead_score == 75
    assert sales_mem.qualification.qualification_status == "QUALIFIED"


# ── 2. CONVERSATION HEALTH METRICS TESTS ──────────────────────────────

def test_conversation_health_and_risks():
    session = OutboundAISession(
        tenant_id="tenant_123",
        campaign_id="camp_123",
        lead_id="lead_123",
        conversation_id="conv_123",
        sender_phone="+919876543210",
        incoming_text="Stop contacting me, uninterested.",
        message_id="msg_abc"
    )
    session.history = []
    
    sales_mem = SalesMemory()
    
    health_calculator.calculate(session, sales_mem)
    
    # Risk score should jump due to opt-out keyword
    assert sales_mem.health.risk_score >= 80
    assert sales_mem.health.customer_interest_level == "LOW"
    assert sales_mem.health.conversion_probability == 0.0 # High risk zero conversions


# ── 3. STRATEGY ENGINE DIRECTIVES TESTS ───────────────────────────────

def test_strategy_engine_goal_transitions():
    session = OutboundAISession(
        tenant_id="tenant_123",
        campaign_id="camp_123",
        lead_id="lead_123",
        conversation_id="conv_123",
        sender_phone="+919876543210",
        incoming_text="Hi",
        message_id="msg_abc"
    )
    session.history = []
    
    sales_mem = SalesMemory()
    
    # 1. Unqualified leads start at budget collection
    goal, next_action, strategy = sales_strategy_engine.select_strategy(
        session, sales_mem, CustomerIntentType.CURIOUS, None
    )
    assert goal == SalesGoal.COLLECT_BUDGET
    assert "budget" in next_action.lower()
    
    # 2. Qualified leads advance to booking
    sales_mem.qualification.lead_score = 75
    goal, next_action, strategy = sales_strategy_engine.select_strategy(
        session, sales_mem, CustomerIntentType.CURIOUS, None
    )
    assert goal == SalesGoal.BOOK_MEETING
    assert "calendar" in next_action.lower()


# ── 4. AI GUARDRAILS TESTS ────────────────────────────────────────────

def test_ai_guardrails_blocks_duplicate_tools():
    session = OutboundAISession(
        tenant_id="tenant_123",
        campaign_id="camp_123",
        lead_id="lead_123",
        conversation_id="conv_123",
        sender_phone="+919876543210",
        incoming_text="Hi",
        message_id="msg_abc"
    )
    session.memory = OutboundMemory(conversation_id="conv_123")
    
    # Setup past tool calls showing we already dispatched a brochure
    session.tool_outputs = [
        {"name": "send_brochure", "success": True}
    ]
    
    tool_specs = [
        {"toolSpec": {"name": "send_brochure", "description": "", "inputSchema": {}}},
        {"toolSpec": {"name": "check_calendar", "description": "", "inputSchema": {}}}
    ]
    
    filtered_specs = ai_guardrails.validate_and_filter_tools(session, tool_specs)
    
    # send_brochure should be blocked from the available tools list
    tool_names = [spec["toolSpec"]["name"] for spec in filtered_specs]
    assert "send_brochure" not in tool_names
    assert "check_calendar" in tool_names


def test_ai_guardrails_hallucination_cleaning():
    session = OutboundAISession(
        tenant_id="tenant_123",
        campaign_id="camp_123",
        lead_id="lead_123",
        conversation_id="conv_123",
        sender_phone="+919876543210",
        incoming_text="Hi",
        message_id="msg_abc"
    )
    
    raw_response = "We can offer a special 50% discount off our catalog details."
    cleaned = ai_guardrails.clean_hallucinated_offers(session, raw_response)
    
    # Discount details should be sanitized
    assert "50%" not in cleaned
    assert "discount" not in cleaned
