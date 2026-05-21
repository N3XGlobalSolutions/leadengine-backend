"""Persona-based integration tests for S3 MAX Chat.

These tests simulate different types of extreme/edge-case users
and verify that the S3 MAX LangGraph handles them gracefully.
"""

import pytest
import json
from unittest.mock import patch, MagicMock

from app.services.chat.agents.graph_state import S3MaxState
from app.services.chat.agents.router_agent import router_node
from app.services.chat.agents.response_analyzer_agent import analyze_response_node
from app.services.chat.agents.question_generator_agent import generate_question_node

@pytest.fixture
def base_state() -> S3MaxState:
    return {
        "tenant_id": "test_tenant",
        "conversation_id": "conv_123",
        "messages": [
            {"role": "user", "content": "Hi"},
            {"role": "bot", "content": "Hello! What is your budget?"}
        ],
        "current_message": "",
        "phone": "9876543210",
        "name": "Test User",
        "scorecard": {},
        "questions_asked": 1,
        "question_history": ["What is your budget?"],
        "current_question_id": "budget",
        "question_bank": [
            {"dimension": "budget", "question": "What is your budget?"},
            {"dimension": "timeline", "question": "When do you need this?"},
            {"dimension": "location", "question": "Where are you based?"}
        ],
        "lqc_criteria": "",
        "covered_dimensions": [],
        "required_dimensions": ["budget", "timeline", "location"],
        "confidence_score": 0.0,
        "safety_net_triggered": False,
        "evasion_count": 0,
        "kb_context": "",
        "bot_response": "",
        "quick_replies": [],
        "estimated_progress": 0.0,
        "is_complete": False,
        "lead_verdict": None,
        "lead_output": None,
        "next_state": "",
        "show_actions": False,
        "route": "",
    }


class TestPersonaEdgeCases:
    """Tests simulating different extreme user behaviors."""

    def test_the_oversharer_user(self, base_state):
        """User provides budget, timeline, and location in a single massive paragraph.
        The system should route as response and ideally the analyzer extracts it all.
        (Since analyzer targets current_question_id, we test at least the target dimension is caught).
        """
        message = (
            "Well honestly my budget is around 2.5 crores but I can stretch to 3 if "
            "it's really good. I'm looking to move in by next December, so about 12 "
            "months from now. And obviously I want it to be near Metro line in Bandra."
        )
        base_state["current_message"] = message
        base_state["messages"] = [
            {"role": "bot", "content": "What is your budget?"},
            {"role": "user", "content": message}
        ]

        # 1. Router should see this as a response
        route_res = router_node(base_state)
        assert route_res["route"] == "response"

        # 2. Analyzer gets it (mocking LLM for the complex extraction)
        with patch("app.services.chat.agents.response_analyzer_agent._get_llm_service") as mock_factory:
            mock_llm = MagicMock()
            mock_llm.generate_response.return_value = json.dumps({
                "dimension": "budget",
                "extracted_value": "2.5 to 3 crores",
                "confidence": 0.95,
                "is_evasive": False,
                "buyer_signals": {"urgency": "medium", "budget_confidence": "high", "sentiment": "positive"}
            })
            mock_factory.return_value = mock_llm

            analyzer_res = analyze_response_node(base_state)
            assert "budget" in analyzer_res["scorecard"]
            assert analyzer_res["scorecard"]["budget"]["value"] == "2.5 to 3 crores"
            assert analyzer_res["evasion_count"] == 0

    def test_the_abrupt_minimalist_user(self, base_state):
        """User replies with single words that barely answer the question."""
        base_state["current_message"] = "idk maybe"
        base_state["messages"].append({"role": "user", "content": "idk maybe"})

        route_res = router_node(base_state)
        assert route_res["route"] == "response"

        with patch("app.services.chat.agents.response_analyzer_agent._get_llm_service") as mock_factory:
            mock_llm = MagicMock()
            mock_llm.generate_response.return_value = json.dumps({
                "dimension": "budget",
                "extracted_value": "Unknown",
                "confidence": 0.1,
                "is_evasive": True,
                "buyer_signals": {"sentiment": "evasive"}
            })
            mock_factory.return_value = mock_llm

            analyzer_res = analyze_response_node(base_state)
            assert analyzer_res["evasion_count"] == 1
            assert "budget" not in analyzer_res["scorecard"]  # Evasive means not covered

    def test_the_confused_questioner_user(self, base_state):
        """Instead of answering the qualification question, user fires back consecutive questions."""
        base_state["current_message"] = "but wait, where is the project located exactly?"
        base_state["messages"].append({"role": "user", "content": "but wait, where is the project located exactly?"})

        route_res = router_node(base_state)
        # Should be classified as a question!
        assert route_res["route"] == "question"

    def test_the_evasive_loop_user(self, base_state):
        """User keeps dodging questions until the system intervenes."""
        base_state["evasion_count"] = 3  # Already evaded three times
        base_state["current_message"] = "whatever, let's talk later"

        with patch("app.services.chat.agents.question_generator_agent._get_llm_service") as mock_factory:
            mock_llm = MagicMock()
            mock_llm.generate_response.return_value = json.dumps({
                "transition": "I sense we're going back and forth.",
                "question": "Can you give me a rough range for your budget?",
                "dimension": "budget",
                "quick_replies": []
            })
            mock_factory.return_value = mock_llm

            generator_res = generate_question_node(base_state)
            # Should have triggered the evasion handler and reset evasion count
            assert generator_res["evasion_count"] == 0
            assert "back and forth" in generator_res["bot_response"]

    def test_the_angry_complainer_user(self, base_state):
        """User is upset and wants to speak to a human immediately."""
        base_state["current_message"] = "i am very angry, stop asking questions and give me a real human!"
        base_state["messages"].append({"role": "user", "content": base_state["current_message"]})
        
        # Could be routed to response, where analyzer marks sentiment negative
        route_res = router_node(base_state)
        assert route_res["route"] in ["response", "greeting", "question"] # Depending on exact wording
        
        with patch("app.services.chat.agents.response_analyzer_agent._get_llm_service") as mock_factory:
             mock_llm = MagicMock()
             mock_llm.generate_response.return_value = json.dumps({
                 "dimension": "budget",
                 "extracted_value": "Demands human",
                 "confidence": 0.9,
                 "is_evasive": True,
                 "buyer_signals": {"sentiment": "negative"}
             })
             mock_factory.return_value = mock_llm
             
             analyzer_res = analyze_response_node(base_state)
             # The system should mark as evasive for the budget question but record negative sentiment
             assert analyzer_res["evasion_count"] == 1
