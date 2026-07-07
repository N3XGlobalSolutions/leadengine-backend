"""Unit tests for the Outbound AI Sales Agent component."""

from datetime import datetime, date, timezone
import zoneinfo
from unittest.mock import MagicMock, patch
import pytest

from app.models.outbound.outbound_entities import (
    OutboundAgentSettings,
    OutboundCalendarSettings,
    OutboundCalendarMeeting,
    ConversationStage,
    OutboundProduct,
)
from app.services.ai.outbound.conversation_pipeline import OutboundAISession
from app.services.ai.outbound.response_validator import response_validator
from app.services.ai.outbound.tool_executor import ToolPermissionManager
from app.services.ai.outbound.prompt_builder import outbound_prompt_builder
from app.services.outbound.providers.google_calendar_provider import SimulationCalendarProvider


# ── 1. RESPONSE VALIDATOR TESTS ────────────────────────────────────────

def test_response_validator_citations_and_markdown():
    session = OutboundAISession(
        tenant_id="tenant_123",
        campaign_id="camp_123",
        lead_id="lead_123",
        conversation_id="conv_123",
        sender_phone="+919876543210",
        incoming_text="Hi",
        message_id="msg_abc"
    )
    session.agent_settings = OutboundAgentSettings(tenant_id="tenant_123", business_summary="Test")

    # Test stripping citations
    text = "We offer premium services [Source 1]. Click here [2]."
    cleaned = response_validator.validate_response(session, text)
    assert "[Source 1]" not in cleaned
    assert "[2]" not in cleaned

    # Test converting markdown links
    text_with_link = "Check our website [LeadEngine](https://qbotglobal.com)."
    cleaned_link = response_validator.validate_response(session, text_with_link)
    assert "LeadEngine (https://qbotglobal.com)" in cleaned_link


def test_response_validator_profanity():
    session = OutboundAISession(
        tenant_id="tenant_123",
        campaign_id="camp_123",
        lead_id="lead_123",
        conversation_id="conv_123",
        sender_phone="+919876543210",
        incoming_text="Hi",
        message_id="msg_abc"
    )
    session.agent_settings = OutboundAgentSettings(tenant_id="tenant_123", business_summary="Test")

    # Blacklisted word triggers safety fallback
    bad_text = "This product is a scam and you are talking shit."
    cleaned = response_validator.validate_response(session, bad_text)
    assert "Our goal is to assist you" in cleaned


# ── 2. CALENDAR PROVIDER SLOT MATH TESTS ───────────────────────────────

@patch("app.services.outbound.outbound_db_service.outbound_db_service.list_tenant_meetings")
def test_simulation_calendar_slots(mock_list_meetings):
    settings = OutboundCalendarSettings(
        tenant_id="tenant_123",
        calendar_id="primary",
        timezone="America/New_York",
        working_days=["monday", "tuesday", "wednesday", "thursday", "friday"],
        working_hours_start="09:00",
        working_hours_end="11:00", # 2 hours slot window
        meeting_duration=30,
        buffer=10
    )

    # Mock no meetings booked yet
    mock_list_meetings.return_value = []

    provider = SimulationCalendarProvider()
    target_date = date(2026, 6, 29) # Monday
    slots = provider.get_available_slots(settings, target_date)

    # 9:00, 9:30, 10:00, 10:30
    assert len(slots) == 4
    assert "2026-06-29T09:00:00" in slots[0]

    # Mock a busy meeting from 09:30 to 10:00
    busy_meeting = OutboundCalendarMeeting(
        meeting_id="meet_1",
        tenant_id="tenant_123",
        lead_id="lead_123",
        conversation_id="conv_123",
        event_id="evt_1",
        summary="Discovery Call",
        description="",
        start_time=datetime(2026, 6, 29, 9, 30, tzinfo=zoneinfo.ZoneInfo("America/New_York")),
        end_time=datetime(2026, 6, 29, 10, 0, tzinfo=zoneinfo.ZoneInfo("America/New_York")),
        timezone="America/New_York",
        attendee_name="Prospect",
        attendee_email="p@ex.com",
        attendee_phone="+919876543210",
        status="BOOKED"
    )
    mock_list_meetings.return_value = [busy_meeting]

    slots_after_booking = provider.get_available_slots(settings, target_date)
    # The first slot (9:00-9:30) overlaps with the start of busy meeting (9:30) plus buffer (9:00 to 9:40 overlaps 9:30)
    # The second slot (9:40 to 10:10) overlaps the busy meeting end (10:00)
    # Only the last slot starting at 10:20 ends up free!
    assert len(slots_after_booking) < 3


# ── 3. TOOL PERMISSION CHECKS ──────────────────────────────────────────

def test_tool_permission_manager():
    session = OutboundAISession(
        tenant_id="tenant_123",
        campaign_id="camp_123",
        lead_id="lead_123",
        conversation_id="conv_123",
        sender_phone="+919876543210",
        incoming_text="Hi",
        message_id="msg_abc"
    )

    # Disable capabilities
    session.agent_settings = OutboundAgentSettings(
        tenant_id="tenant_123",
        business_summary="Test",
        enable_scheduling=False,
        enable_product_search=False
    )

    # Calendar check should fail
    allowed, reason = ToolPermissionManager.check_permissions(session, "check_calendar")
    assert allowed is False
    assert "scheduling is disabled" in reason

    # Product list search should fail
    allowed_prod, reason_prod = ToolPermissionManager.check_permissions(session, "search_products")
    assert allowed_prod is False
    assert "Product search" in reason_prod


# ── 4. PROMPT BUILDER TESTS ────────────────────────────────────────────

def test_prompt_builder():
    session = OutboundAISession(
        tenant_id="tenant_123",
        campaign_id="camp_123",
        lead_id="lead_123",
        conversation_id="conv_123",
        sender_phone="+919876543210",
        incoming_text="Explain CRM pricing",
        message_id="msg_abc"
    )
    session.agent_settings = OutboundAgentSettings(
        tenant_id="tenant_123",
        ai_name="Alex Outbound",
        business_summary="We sell top cloud solutions.",
        sales_tone="Consultative, analytical and helper-driven"
    )
    
    session.selected_products = [
        OutboundProduct(
            product_id="prod_1",
            tenant_id="tenant_123",
            name="Cloud CRM Pro",
            description="Enterprise database solution",
            short_description="CRM software",
            price=99.0,
            currency="USD",
            category="Software",
            status="ACTIVE"
        )
    ]

    system, user = outbound_prompt_builder.build_prompts(session)

    assert "Alex Outbound" in system
    assert "Consultative, analytical and helper-driven" in system
    assert "Cloud CRM Pro" in user
    assert "99.0" in user
    assert "Explain CRM pricing" in user
