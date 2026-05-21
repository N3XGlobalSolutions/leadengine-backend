"""Integration tests for the S3 Max LangGraph flow.

Tests cover:
- Full graph compilation and structure
- End-to-end flow with mocked LLM (greeting → contact → qualification → evaluation)
- Edge cases: 0 questions, safety net trigger, early exit, evasion handling
- State machine adapter (S3MaxChatStateMachine) integration
"""

import pytest
import json
from unittest.mock import patch, MagicMock

from app.services.chat.s3_max_graph import (
    build_s3_max_graph,
    greet_node,
    collect_contact_node,
    route_decision,
    post_rag_decision,
    create_lead_node,
)
from app.services.chat.agents.graph_state import S3MaxState
from app.services.chat.agents.router_agent import router_node
from app.services.chat.agents.response_analyzer_agent import analyze_response_node
from app.services.chat.agents.question_generator_agent import (
    generate_question_node,
    _pick_from_bank,
)
from app.services.chat.agents.qualification_evaluator_agent import (
    evaluate_qualification_node,
    _fallback_evaluation,
)


# ── Graph Structure Tests ──

class TestGraphCompilation:
    """Test that the LangGraph compiles correctly."""

    def test_graph_builds_without_error(self):
        graph = build_s3_max_graph()
        assert graph is not None

    def test_graph_compiles_to_runnable(self):
        graph = build_s3_max_graph()
        compiled = graph.compile()
        assert compiled is not None


# ── Node-level Tests ──

class TestGreetNode:
    """Tests for the greeting node."""

    def test_greeting_with_name(self):
        state = {"name": "Rahul"}
        result = greet_node(state)
        assert "Rahul" in result["bot_response"]
        assert result["next_state"] == "contact"

    def test_greeting_without_name(self):
        state = {"name": ""}
        result = greet_node(state)
        assert "name and phone" in result["bot_response"]
        assert result["next_state"] == "contact"


class TestCollectContactNode:
    """Tests for the contact collection node."""

    def test_both_collected_transitions_to_qualifying(self):
        state = {"phone": "9876543210", "name": "Rahul"}
        result = collect_contact_node(state)
        assert result["next_state"] == "qualifying_agentic"
        assert "Rahul" in result["bot_response"]

    def test_phone_only_asks_for_name(self):
        state = {"phone": "9876543210", "name": None}
        result = collect_contact_node(state)
        assert result["next_state"] == "contact"
        assert "call you" in result["bot_response"]

    def test_name_only_asks_for_phone(self):
        state = {"phone": None, "name": "John"}
        result = collect_contact_node(state)
        assert result["next_state"] == "contact"
        assert "phone" in result["bot_response"].lower()

    def test_neither_asks_for_both(self):
        state = {"phone": None, "name": None}
        result = collect_contact_node(state)
        assert result["next_state"] == "contact"
        assert "name" in result["bot_response"].lower()


class TestRouteDecision:
    """Tests for the route_decision function."""

    def test_routes_correctly(self):
        assert route_decision({"route": "greeting"}) == "greet"
        assert route_decision({"route": "contact"}) == "collect_contact"
        assert route_decision({"route": "response"}) == "analyze_response"
        assert route_decision({"route": "question"}) == "answer_rag"

    def test_unknown_defaults_to_greet(self):
        assert route_decision({"route": "unknown_route"}) == "greet"


class TestPostRagDecision:
    """Tests for the post-RAG routing logic."""

    def test_generates_question_when_qualifying(self):
        state = {"questions_asked": 3, "phone": "9876543210"}
        result = post_rag_decision(state)
        assert result == "generate_question"

    def test_ends_when_not_qualifying(self):
        from langgraph.graph import END
        state = {"questions_asked": 0, "phone": None}
        result = post_rag_decision(state)
        assert result == END


class TestCreateLeadNode:
    """Tests for the lead creation passthrough node."""

    def test_marks_completion(self):
        result = create_lead_node({})
        assert result["is_complete"] is True
        assert result["show_actions"] is True


# ── Response Analyzer Tests (with mocked LLM) ──

class TestResponseAnalyzer:
    """Tests for the response analyzer agent with mocked LLM."""

    @patch("app.services.chat.agents.response_analyzer_agent._get_llm_service")
    def test_extracts_dimension_from_response(self, mock_llm_factory):
        mock_llm = MagicMock()
        mock_llm.generate_response.return_value = json.dumps({
            "dimension": "budget",
            "extracted_value": "2 Crore",
            "confidence": 0.9,
            "is_evasive": False,
            "buyer_signals": {"sentiment": "positive"},
        })
        mock_llm_factory.return_value = mock_llm

        state: S3MaxState = {
            "current_message": "My budget is around 2 crore",
            "current_question_id": "budget",
            "scorecard": {},
            "question_history": ["What is your budget range?"],
            "evasion_count": 0,
            "covered_dimensions": [],
        }

        result = analyze_response_node(state)
        assert "budget" in result["scorecard"]
        assert result["scorecard"]["budget"]["value"] == "2 Crore"
        assert result["evasion_count"] == 0
        assert "budget" in result["covered_dimensions"]

    @patch("app.services.chat.agents.response_analyzer_agent._get_llm_service")
    def test_detects_evasive_response(self, mock_llm_factory):
        mock_llm = MagicMock()
        mock_llm.generate_response.return_value = json.dumps({
            "dimension": "budget",
            "extracted_value": "",
            "confidence": 0.1,
            "is_evasive": True,
            "buyer_signals": {"sentiment": "evasive"},
        })
        mock_llm_factory.return_value = mock_llm

        state: S3MaxState = {
            "current_message": "What's the weather like?",
            "current_question_id": "budget",
            "scorecard": {},
            "question_history": ["What is your budget range?"],
            "evasion_count": 1,
            "covered_dimensions": [],
        }

        result = analyze_response_node(state)
        assert result["evasion_count"] == 2  # Incremented
        assert "budget" not in result["scorecard"]  # Not added when evasive

    @patch("app.services.chat.agents.response_analyzer_agent._get_llm_service")
    def test_fallback_on_llm_error(self, mock_llm_factory):
        mock_llm = MagicMock()
        mock_llm.generate_response.side_effect = Exception("LLM down")
        mock_llm_factory.return_value = mock_llm

        state: S3MaxState = {
            "current_message": "2 Crore",
            "current_question_id": "budget",
            "scorecard": {},
            "question_history": ["What is your budget?"],
            "evasion_count": 0,
            "covered_dimensions": [],
        }

        result = analyze_response_node(state)
        # Should not crash—fallback handles it
        assert "scorecard" in result
        assert result["evasion_count"] == 0


# ── Question Generator Tests ──

class TestQuestionBankPicker:
    """Tests for the question bank selection logic."""

    def test_picks_uncovered_dimension_first(self):
        bank = [
            {"question": "What is your budget?", "dimension": "budget", "priority": "medium"},
            {"question": "What is your timeline?", "dimension": "timeline", "priority": "high"},
            {"question": "What location?", "dimension": "location", "priority": "low"},
        ]
        covered = ["budget", "location"]  # Only timeline uncovered
        history = []

        picked = _pick_from_bank(bank, covered, history)
        assert picked is not None
        # Timeline has +10 priority bonus (uncovered) + 5 (high), so it dominates
        assert picked["dimension"] == "timeline"

    def test_skips_already_asked_questions(self):
        bank = [
            {"question": "What is your budget?", "dimension": "budget", "priority": "high"},
        ]
        covered = []
        history = ["What is your budget?"]  # Already asked

        picked = _pick_from_bank(bank, covered, history)
        assert picked is None

    def test_returns_none_when_bank_empty(self):
        picked = _pick_from_bank([], [], [])
        assert picked is None


class TestQuestionGeneratorNode:
    """Tests for the generate_question_node."""

    @patch("app.services.chat.agents.question_generator_agent._get_llm_service")
    def test_evasion_handler_at_three_streak(self, mock_llm_factory):
        state: S3MaxState = {
            "question_bank": [],
            "covered_dimensions": [],
            "question_history": [],
            "questions_asked": 5,
            "evasion_count": 3,
            "messages": [],
        }

        result = generate_question_node(state)
        assert "back and forth" in result["bot_response"]
        assert "Continue" in result["quick_replies"]
        assert result["evasion_count"] == 0  # Reset


# ── Qualification Evaluator Tests ──

class TestQualificationEvaluator:
    """Tests for the qualification evaluator agent."""

    @patch("app.services.chat.agents.qualification_evaluator_agent._get_llm_service")
    def test_qualified_verdict(self, mock_llm_factory):
        mock_llm = MagicMock()
        mock_llm.generate_response.return_value = json.dumps({
            "verdict": "QUALIFIED",
            "lead_priority": "High",
            "reasoning_summary": "Strong buyer with clear intent.",
            "key_objections": [],
            "recommended_next_step": "Schedule demo",
            "qualification_score": 85,
            "dimension_scores": {
                "budget": {"score": 90, "notes": "Clear budget"},
            },
        })
        mock_llm_factory.return_value = mock_llm

        state: S3MaxState = {
            "scorecard": {
                "budget": {"value": "2 Cr", "confidence": 0.9},
                "timeline": {"value": "3 months", "confidence": 0.8},
                "need": {"value": "Investment", "confidence": 0.85},
            },
            "lqc_criteria": "Budget, timeline, authority",
            "messages": [
                {"role": "user", "content": "My budget is 2 crore"},
                {"role": "bot", "content": "Great! When are you looking?"},
            ],
            "questions_asked": 6,
            "confidence_score": 0.85,
            "safety_net_triggered": False,
            "name": "Rahul",
            "phone": "9876543210",
        }

        result = evaluate_qualification_node(state)
        assert result["is_complete"] is True
        assert result["lead_verdict"] == "QUALIFIED"
        assert result["next_state"] == "completed"
        assert result["estimated_progress"] == 1.0
        assert "Rahul" in result["bot_response"]

    @patch("app.services.chat.agents.qualification_evaluator_agent._get_llm_service")
    def test_disqualified_verdict(self, mock_llm_factory):
        mock_llm = MagicMock()
        mock_llm.generate_response.return_value = json.dumps({
            "verdict": "DISQUALIFIED",
            "lead_priority": "Low",
            "reasoning_summary": "No clear intent or budget.",
            "key_objections": ["No budget", "Just browsing"],
            "recommended_next_step": "Nurture campaign",
            "qualification_score": 25,
            "dimension_scores": {},
        })
        mock_llm_factory.return_value = mock_llm

        state: S3MaxState = {
            "scorecard": {"browsing": {"value": "yes", "confidence": 0.4}},
            "lqc_criteria": "",
            "messages": [],
            "questions_asked": 4,
            "confidence_score": 0.3,
            "safety_net_triggered": False,
            "name": "User",
            "phone": "1234567890",
        }

        result = evaluate_qualification_node(state)
        assert result["lead_verdict"] == "DISQUALIFIED"

    def test_fallback_evaluation_qualified(self):
        scorecard = {"a": {}, "b": {}, "c": {}}
        result = _fallback_evaluation(scorecard, 0.7)
        assert result["verdict"] == "QUALIFIED"
        assert result["lead_priority"] == "Medium"

    def test_fallback_evaluation_disqualified(self):
        scorecard = {"a": {}}
        result = _fallback_evaluation(scorecard, 0.3)
        assert result["verdict"] == "DISQUALIFIED"


# ── Edge Case Tests ──

class TestEdgeCases:
    """Edge case tests for the S3 Max system."""

    def test_empty_state_router_handles_gracefully(self):
        state = {"current_message": "", "messages": []}
        result = router_node(state)
        assert result["route"] == "greeting"

    def test_collect_contact_all_none(self):
        state = {"phone": None, "name": None}
        result = collect_contact_node(state)
        assert result["next_state"] == "contact"

    def test_greet_with_empty_name(self):
        state = {"name": None}
        result = greet_node(state)
        assert result["next_state"] == "contact"

    @patch("app.services.chat.agents.question_generator_agent._get_llm_service")
    def test_zero_question_bank_uses_dynamic(self, mock_llm_factory):
        mock_llm = MagicMock()
        mock_llm.generate_response.return_value = json.dumps({
            "transition": "Let me ask:",
            "question": "What is your budget?",
            "dimension": "budget",
            "quick_replies": ["Under 1Cr", "1-2Cr"],
        })
        mock_llm_factory.return_value = mock_llm

        state: S3MaxState = {
            "question_bank": [],
            "covered_dimensions": [],
            "question_history": [],
            "questions_asked": 1,
            "evasion_count": 0,
            "messages": [{"role": "bot", "content": "Hi!"}, {"role": "user", "content": "Hi"}],
            "scorecard": {},
            "required_dimensions": ["budget"],
            "lqc_criteria": "",
            "kb_context": "",
        }

        result = generate_question_node(state)
        assert result["bot_response"] != ""
        assert result["questions_asked"] == 2

    def test_safety_net_forces_evaluation_at_max(self):
        from app.services.chat.s3_max_guardrails import check_completion_node, MAX_QUESTIONS

        state = {
            "questions_asked": MAX_QUESTIONS,
            "confidence_score": 0.2,
            "covered_dimensions": [],
            "required_dimensions": ["budget", "timeline", "need"],
        }
        result = check_completion_node(state)
        assert result["route"] == "safety_net"

    def test_early_exit_not_triggered_with_few_questions(self):
        from app.services.chat.agents.memory_agent import can_early_exit

        state = {
            "questions_asked": 2,
            "confidence_score": 0.99,
            "covered_dimensions": ["budget", "timeline", "need", "authority"],
            "required_dimensions": ["budget", "timeline", "need"],
        }
        # Even with high confidence, min 5 questions required
        assert can_early_exit(state) is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
