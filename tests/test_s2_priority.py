import pytest
from unittest.mock import Mock
from app.models.conversation import Conversation, ConversationState
from app.models.tenant import TenantConfig, QualificationQuestion
from app.services.chat.s2_state_machine import S2ChatStateMachine


@pytest.fixture
def s2_state_machine():
    sm = S2ChatStateMachine()
    sm._llm_service = Mock()
    # Mock extract_lead_info to return nothing or basic info so we rely on manual data setup
    sm._llm_service.extract_lead_info.return_value = {}
    return sm


@pytest.fixture
def tenant_config():
    return TenantConfig(
        url_slug="test-tenant",
        qualification_questions=[
            QualificationQuestion(id="q1", instruction="Q1", collects=["q1"]),
            QualificationQuestion(id="q2", instruction="Q2", collects=["q2"]),
        ],
    )


@pytest.fixture
def conversation():
    return Conversation(
        conversation_id="test-conv",
        tenant_id="test-tenant",
        current_state=ConversationState.QUALIFYING.value,
        current_step_index=0,
        extracted_lead_data={"name": "Test User", "phone": "1234567890"},
    )


def test_s2_completion_shows_summary(s2_state_machine, conversation, tenant_config):
    """Test that S2 generates summary when all questions are answered."""
    # Setup: Last question answered, transitioning to completed
    conversation.current_step_index = 1  # Last question is index 1

    # Action: Process answer to last question
    result = s2_state_machine.process_message(
        conversation=conversation,
        user_message="Answer to Q2",
        tenant_config=tenant_config,
    )

    # Assertions
    assert result.next_state == ConversationState.COMPLETED.value
    assert result.action == "show_summary"
    assert result.summary_data is not None
    assert result.show_actions is True


def test_maybe_later_disqualifies_lead(s2_state_machine, conversation, tenant_config):
    """Test that 'Maybe later' returns disqualify_lead action."""
    # Setup: Conversation in COMPLETED state
    conversation.current_state = ConversationState.COMPLETED.value

    # Action: User says "Maybe later"
    result = s2_state_machine.process_message(
        conversation=conversation,
        user_message="Maybe later",
        tenant_config=tenant_config,
    )

    # Assertions
    assert result.action == "disqualify_lead"


def test_yes_schedule_call_action(s2_state_machine, conversation, tenant_config):
    """Test 'Yes, schedule a call' returns correct action."""
    conversation.current_state = ConversationState.COMPLETED.value

    result = s2_state_machine.process_message(
        conversation=conversation,
        user_message="Yes, schedule a call",
        tenant_config=tenant_config,
    )

    assert result.action == "schedule_call"
