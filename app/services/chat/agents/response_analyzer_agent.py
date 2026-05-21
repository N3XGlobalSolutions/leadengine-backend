"""Response Analyzer Agent — Extracts structured data from user responses.

Processes user answers to qualification questions and extracts:
- Field values (budget, timeline, location, etc.)
- Buyer signals (urgency, hesitation, comparison shopping)
- Sentiment (positive, neutral, negative, evasive)
"""

import json
from typing import Any, Dict

from app.core.logging_config import get_logger
from app.services.chat.agents.graph_state import S3MaxState

logger = get_logger(__name__)


def _get_llm_service():
    """Lazy import to avoid circular dependencies."""
    from app.services.ai.claude_service import claude_service
    return claude_service


def analyze_response_node(state: S3MaxState) -> Dict[str, Any]:
    """Analyze the user's response to extract structured data.

    Uses LLM to extract:
    1. The actual answer value for the asked dimension
    2. Buyer signals (urgency, budget confidence, etc.)
    3. Whether the response is evasive or off-topic
    """
    message = state.get("current_message", "")
    current_q_id = state.get("current_question_id", "")
    scorecard = dict(state.get("scorecard", {}))
    question_history = state.get("question_history", [])
    evasion_count = state.get("evasion_count", 0)

    # Find the question that was asked
    current_question = ""
    if question_history:
        current_question = question_history[-1]

    llm = _get_llm_service()

    prompt = f"""You are analyzing a user's response to a sales qualification question.

QUESTION THAT WAS ASKED:
{current_question}

USER'S RESPONSE:
{message}

TASK: Analyze this response and return a JSON object with these fields:
{{
    "dimension": "<what aspect this covers: budget, timeline, authority, need, location, preference, experience, etc.>",
    "extracted_value": "<the actual value/answer extracted from the response>",
    "confidence": <float 0.0-1.0 how clear/definitive the answer is>,
    "is_evasive": <true if the user dodged the question or gave a non-answer>,
    "is_disinterested": <true ONLY if user explicitly says they are not interested, want to stop, or no longer want to chat>,
    "buyer_signals": {{
        "urgency": "<none|low|medium|high>",
        "budget_confidence": "<none|low|medium|high>",
        "decision_authority": "<none|low|medium|high>",
        "comparison_shopping": <true|false>,
        "sentiment": "<positive|neutral|negative|evasive>"
    }}
}}

RULES:
- If the user clearly answered, extract the value and set confidence high
- If the user was vague ("maybe", "not sure", "I'll think about it"), set confidence low  
- If the user completely dodged ("what's your address?", "nice weather"), set is_evasive=true
- If the user expresses extreme annoyance, wants to end the chat, or explicitly says they are not interested, set is_disinterested=true
- The dimension should match what the question was about
- Return ONLY valid JSON, nothing else

JSON:"""

    try:
        response = llm.generate_response(prompt)
        
        # Parse JSON from response
        start = response.find("{")
        end = response.rfind("}") + 1
        if start != -1 and end > start:
            analysis = json.loads(response[start:end])
        else:
            logger.warning(f"Failed to parse analysis JSON: {response[:100]}")
            analysis = {
                "dimension": current_q_id or "unknown",
                "extracted_value": message,
                "confidence": 0.5,
                "is_evasive": False,
                "buyer_signals": {"sentiment": "neutral"},
            }
    except Exception as e:
        logger.error(f"Response analysis failed: {e}")
        analysis = {
            "dimension": current_q_id or "unknown",
            "extracted_value": message,
            "confidence": 0.5,
            "is_evasive": False,
            "buyer_signals": {"sentiment": "neutral"},
        }

    # Update evasion counter
    is_evasive = analysis.get("is_evasive", False)
    is_disinterested = analysis.get("is_disinterested", False)
    new_evasion_count = evasion_count + 1 if is_evasive else 0

    # Update scorecard with extracted data
    dimension = analysis.get("dimension", current_q_id or "unknown")
    if not is_evasive and not is_disinterested and analysis.get("extracted_value"):
        scorecard[dimension] = {
            "value": analysis["extracted_value"],
            "confidence": analysis.get("confidence", 0.5),
            "source_question": current_question,
            "raw_answer": message,
        }

    # Update covered_dimensions
    covered = list(state.get("covered_dimensions", []))
    if dimension and dimension not in covered and not is_evasive:
        covered.append(dimension)

    return {
        "scorecard": scorecard,
        "covered_dimensions": covered,
        "evasion_count": new_evasion_count,
        "is_disinterested": is_disinterested,
    }
