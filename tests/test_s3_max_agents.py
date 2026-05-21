"""Unit tests for S3 Max Agent Nodes.

Tests cover:
- Router Agent: message classification (greeting, contact, question, response)
- Memory Agent: scorecard computation, confidence, progress, early exit
- Guardrails: min/max questions, safety net, early exit, completion decision
- Question Generator: question selection and deduplication (mocked LLM)
"""

import pytest
from unittest.mock import Mock, patch, MagicMock

# --- Router Agent Tests ---

from app.services.chat.agents.router_agent import (
    router_node,
    extract_phone,
    extract_name,
    is_user_question,
    is_greeting,
)


class TestPhoneExtraction:
    """Tests for phone number extraction in router agent."""

    def test_indian_mobile(self):
        assert extract_phone("My number is 9876543210") == "9876543210"

    def test_indian_with_country_code(self):
        phone = extract_phone("Call me at +91 9876543210")
        assert phone == "9876543210"

    def test_no_phone_in_text(self):
        assert extract_phone("My name is John Doe") is None

    def test_short_number_not_extracted(self):
        assert extract_phone("I need 1234 items") is None

    def test_invalid_starting_digit(self):
        assert extract_phone("Number is 1234567890") is None  # Starts with 1

    def test_phone_in_sentence(self):
        assert extract_phone("Reach me at 8765432109 anytime") == "8765432109"


class TestNameExtraction:
    """Tests for name extraction in router agent."""

    def test_my_name_is_pattern(self):
        assert extract_name("My name is John Doe") == "John Doe"

    def test_i_am_pattern(self):
        assert extract_name("I am Alice") == "Alice"

    def test_standalone_name(self):
        assert extract_name("Rahul Kumar") == "Rahul Kumar"

    def test_no_name_in_message(self):
        assert extract_name("I want to buy a house") is None

    def test_greeting_not_extracted_as_name(self):
        assert extract_name("Hi") is None


class TestQuestionDetection:
    """Tests for user question detection."""

    def test_question_mark(self):
        assert is_user_question("What properties do you have?") is True

    def test_question_keyword(self):
        assert is_user_question("tell me about the project") is True

    def test_how_much(self):
        assert is_user_question("how much does it cost") is True

    def test_not_a_question(self):
        assert is_user_question("$50k to $100k") is False

    def test_short_answer(self):
        assert is_user_question("yes") is False


class TestGreetingDetection:
    """Tests for greeting detection."""

    def test_hello(self):
        assert is_greeting("Hello") is True

    def test_hi(self):
        assert is_greeting("hi") is True

    def test_not_greeting(self):
        assert is_greeting("I want a 3 BHK apartment") is False


class TestRouterNode:
    """Tests for the router_node graph function."""

    def test_empty_message_routes_to_greeting(self):
        state = {"current_message": "", "messages": []}
        result = router_node(state)
        assert result["route"] == "greeting"

    def test_phone_in_message_routes_to_contact(self):
        state = {
            "current_message": "My phone is 9876543210",
            "phone": None,
            "name": None,
            "questions_asked": 0,
            "messages": [],
        }
        result = router_node(state)
        assert result["route"] == "contact"
        assert result["phone"] == "9876543210"

    @patch("app.services.chat.agents.router_agent.classify_contact_intent", return_value="GENERAL")
    def test_no_phone_routes_to_contact(self, mock_classify):
        state = {
            "current_message": "I want to buy a flat",
            "phone": None,
            "name": None,
            "questions_asked": 0,
            "messages": [],
        }
        result = router_node(state)
        assert result["route"] == "contact"
        mock_classify.assert_called_once_with("I want to buy a flat")

    def test_first_message_greeting_with_phone(self):
        """Once both phone AND name are collected, even greeting-like messages
        should NOT be routed to 'greeting' to avoid re-asking for phone number."""
        state = {
            "current_message": "Hello",
            "phone": "9876543210",
            "name": "John",
            "questions_asked": 0,
            "messages": [],
        }
        result = router_node(state)
        # New correct behavior: phone+name already set → skip greeting, treat as response
        assert result["route"] == "response"

    def test_question_routed_correctly(self):
        state = {
            "current_message": "What apartments do you have in downtown?",
            "phone": "9876543210",
            "name": "John",
            "questions_asked": 2,
            "messages": [{"role": "user"}, {"role": "bot"}, {"role": "user"}],
        }
        result = router_node(state)
        assert result["route"] == "question"

    def test_answer_routed_as_response(self):
        state = {
            "current_message": "I'm looking at a budget of around 2 crore",
            "phone": "9876543210",
            "name": "John",
            "questions_asked": 2,
            "messages": [{"role": "user"}, {"role": "bot"}, {"role": "user"}],
        }
        result = router_node(state)
        assert result["route"] == "response"

    @patch("app.services.chat.agents.router_agent.classify_contact_intent", return_value="GENERAL")
    def test_name_extracted_during_contact_phase(self, mock_classify):
        state = {
            "current_message": "My name is Rahul Sharma",
            "phone": None,
            "name": None,
            "questions_asked": 0,
            "messages": [],
        }
        result = router_node(state)
        assert result["route"] == "contact"
        assert result["name"] == "Rahul Sharma"
        mock_classify.assert_called_once_with("My name is Rahul Sharma")

    @patch("app.services.chat.agents.router_agent.classify_contact_intent", return_value="GENERAL")
    def test_phone_and_name_together(self, mock_classify):
        state = {
            "current_message": "I am Rahul, my number is 9876543210",
            "phone": None,
            "name": None,
            "questions_asked": 0,
            "messages": [],
        }
        result = router_node(state)
        assert result["route"] == "contact"
        assert result["phone"] == "9876543210"
        assert result.get("name") is not None
        mock_classify.assert_not_called()  # Extract phone avoids the fallback branch


# --- Memory Agent Tests ---

from app.services.chat.agents.memory_agent import (
    update_memory_node,
    can_early_exit,
    has_minimum_info,
    MIN_DIMENSIONS_FOR_EARLY_EXIT,
    EARLY_EXIT_CONFIDENCE,
    MIN_INFO_THRESHOLD,
)


class TestUpdateMemoryNode:
    """Tests for the memory agent's update function."""

    def test_empty_scorecard_returns_zero_progress(self):
        state = {
            "scorecard": {},
            "covered_dimensions": [],
            "required_dimensions": ["budget", "timeline", "need"],
            "questions_asked": 0,
        }
        result = update_memory_node(state)
        assert result["confidence_score"] == 0.0
        assert result["estimated_progress"] == 0.0

    def test_partial_coverage_updates_progress(self):
        state = {
            "scorecard": {
                "budget": {"value": "2 Cr", "confidence": 0.9},
            },
            "covered_dimensions": ["budget"],
            "required_dimensions": ["budget", "timeline", "need"],
            "questions_asked": 3,
        }
        result = update_memory_node(state)
        assert 0.0 < result["confidence_score"] < 1.0
        assert 0.0 < result["estimated_progress"] < 1.0

    def test_full_coverage_high_confidence(self):
        state = {
            "scorecard": {
                "budget": {"value": "2 Cr", "confidence": 0.95},
                "timeline": {"value": "3 months", "confidence": 0.9},
                "need": {"value": "Investment", "confidence": 0.85},
            },
            "covered_dimensions": ["budget", "timeline", "need"],
            "required_dimensions": ["budget", "timeline", "need"],
            "questions_asked": 8,
        }
        result = update_memory_node(state)
        assert result["confidence_score"] > 0.7
        assert result["estimated_progress"] > 0.5

    def test_progress_capped_at_95_percent(self):
        state = {
            "scorecard": {
                "budget": {"value": "x", "confidence": 1.0},
                "timeline": {"value": "x", "confidence": 1.0},
                "need": {"value": "x", "confidence": 1.0},
                "location": {"value": "x", "confidence": 1.0},
                "urgency": {"value": "x", "confidence": 1.0},
            },
            "covered_dimensions": ["budget", "timeline", "need", "location", "urgency"],
            "required_dimensions": ["budget", "timeline", "need"],
            "questions_asked": 20,
        }
        result = update_memory_node(state)
        assert result["estimated_progress"] <= 0.95


class TestCanEarlyExit:
    """Tests for early exit condition checks."""

    def test_too_few_questions_no_exit(self):
        state = {
            "questions_asked": 3,
            "confidence_score": 0.95,
            "covered_dimensions": ["budget", "timeline", "need"],
            "required_dimensions": ["budget", "timeline", "need"],
        }
        assert can_early_exit(state) is False

    def test_low_confidence_no_exit(self):
        state = {
            "questions_asked": 8,
            "confidence_score": 0.5,
            "covered_dimensions": ["budget", "timeline", "need"],
            "required_dimensions": ["budget", "timeline", "need"],
        }
        assert can_early_exit(state) is False

    def test_insufficient_dimensions_no_exit(self):
        state = {
            "questions_asked": 8,
            "confidence_score": 0.95,
            "covered_dimensions": ["budget"],
            "required_dimensions": ["budget", "timeline", "need"],
        }
        assert can_early_exit(state) is False

    def test_all_conditions_met_allows_exit(self):
        state = {
            "questions_asked": 7,
            "confidence_score": 0.9,
            "covered_dimensions": ["budget", "timeline", "need"],
            "required_dimensions": ["budget", "timeline", "need"],
        }
        assert can_early_exit(state) is True


class TestHasMinimumInfo:
    """Tests for minimum info threshold check."""

    def test_empty_scorecard_not_sufficient(self):
        state = {
            "scorecard": {},
            "required_dimensions": ["budget", "timeline", "need"],
        }
        assert has_minimum_info(state) is False

    def test_partial_scorecard_sufficient(self):
        state = {
            "scorecard": {
                "budget": {"value": "2 Cr"},
                "timeline": {"value": "3 months"},
            },
            "required_dimensions": ["budget", "timeline", "need"],
        }
        # 2/3 = 0.66 >= 0.6 threshold
        assert has_minimum_info(state) is True


# --- Guardrails Tests ---

from app.services.chat.s3_max_guardrails import (
    check_completion_node,
    completion_decision,
    MIN_QUESTIONS,
    MAX_QUESTIONS,
)


class TestCheckCompletionNode:
    """Tests for the guardrail completion check."""

    def test_below_minimum_continues(self):
        state = {
            "questions_asked": 1,
            "confidence_score": 0.95,
            "covered_dimensions": ["budget", "timeline", "need"],
            "required_dimensions": ["budget", "timeline", "need"],
        }
        result = check_completion_node(state)
        assert result["route"] == "continue"

    def test_at_minimum_with_low_confidence_continues(self):
        state = {
            "questions_asked": MIN_QUESTIONS,
            "confidence_score": 0.3,
            "covered_dimensions": ["budget"],
            "required_dimensions": ["budget", "timeline", "need"],
        }
        result = check_completion_node(state)
        assert result["route"] == "continue"

    def test_safety_net_at_max_questions(self):
        state = {
            "questions_asked": MAX_QUESTIONS,
            "confidence_score": 0.4,
            "covered_dimensions": ["budget"],
            "required_dimensions": ["budget", "timeline", "need"],
        }
        result = check_completion_node(state)
        assert result["route"] == "safety_net"
        assert result.get("safety_net_triggered") is True

    def test_early_exit_high_confidence(self):
        state = {
            "questions_asked": 7,
            "confidence_score": 0.9,
            "covered_dimensions": ["budget", "timeline", "need"],
            "required_dimensions": ["budget", "timeline", "need"],
        }
        result = check_completion_node(state)
        assert result["route"] == "early_exit"

    def test_normal_completion_all_dimensions_covered(self):
        state = {
            "questions_asked": 6,
            "confidence_score": 0.75,
            "covered_dimensions": ["budget", "timeline", "need", "location"],
            "required_dimensions": ["budget", "timeline", "need"],
        }
        result = check_completion_node(state)
        assert result["route"] == "early_exit"

    def test_above_max_triggers_safety_net(self):
        state = {
            "questions_asked": 25,  # Way above max
            "confidence_score": 0.2,
            "covered_dimensions": [],
            "required_dimensions": ["budget", "timeline", "need"],
        }
        result = check_completion_node(state)
        assert result["route"] == "safety_net"


class TestCompletionDecision:
    """Tests for the routing decision function."""

    def test_returns_route_from_state(self):
        assert completion_decision({"route": "continue"}) == "continue"
        assert completion_decision({"route": "early_exit"}) == "early_exit"
        assert completion_decision({"route": "safety_net"}) == "safety_net"

    def test_defaults_to_continue(self):
        assert completion_decision({}) == "continue"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
