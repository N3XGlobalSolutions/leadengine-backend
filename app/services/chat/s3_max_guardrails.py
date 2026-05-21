"""S3 Max Guardrails — Strict enforcement rules for agentic qualification.

Enforces:
- Question count limits (min 3, max 20)
- Deduplication
- Dimension coverage requirements
- Minimum info threshold
- Evasion handling
- LLM-based natural conversation completion detection
"""

from typing import Any, Dict, Literal

from pydantic import BaseModel

from app.core.logging_config import get_logger
from app.services.chat.agents.graph_state import S3MaxState
from app.services.chat.agents.memory_agent import (
    can_early_exit,
)

logger = get_logger(__name__)

# Hard limits
MIN_QUESTIONS = 3
MAX_QUESTIONS = 20
MAX_EVASION_STREAK = 3

# After this many qualification questions, start checking for natural conversation end
NATURAL_END_CHECK_THRESHOLD = 4


class _ConversationEndDecision(BaseModel):
    """Strict Pydantic model for Claude's conversation-end decision.

    Literal["YES", "NO"] ensures only exactly those two strings are accepted.
    Any other output from Claude raises a ValidationError, which we safely catch.
    """
    decision: Literal["YES", "NO"]


def _detect_conversation_completion(state: S3MaxState) -> bool:
    """Use Claude to detect if the conversation has reached a natural conclusion.

    This handles cases where the user gives vague answers (e.g. 'just exploring',
    'open to suggestions') that never fill the scorecard, but the conversation
    has still gathered useful context and the user is signaling they're done.

    Detects patterns like:
    - User asking to send details / listings / brochures
    - User saying thank you / goodbye / close
    - User declining further questions
    - User giving very short non-committal answers repeatedly
    - User expressing they have no more questions
    """
    from app.services.ai.claude_service import claude_service

    messages = state.get("messages", [])
    current_message = state.get("current_message", "").strip()
    questions_asked = state.get("questions_asked", 0)

    # Build the last few turns of conversation
    recent = messages[-8:] if len(messages) > 8 else messages
    convo_lines = []
    for m in recent:
        role = "User" if m.get("role") == "user" else "Bot"
        convo_lines.append(f"{role}: {m.get('content', '')}")
    if current_message:
        convo_lines.append(f"User: {current_message}")
    convo_text = "\n".join(convo_lines)

    prompt = f"""Analyze this sales qualification conversation and determine if it should END now.

CONVERSATION (last few turns):
{convo_text}

Questions asked so far: {questions_asked}

Should this conversation END? Answer YES if ANY of these are true:
1. The user has asked the bot to send details/listings/info and the bot acknowledged
2. The user said thank you, goodbye, close, done, or similar closing phrases
3. The user is declining further engagement (e.g., "I'll reach out if interested")
4. The user has been giving very short, non-committal answers for 3+ consecutive turns (e.g., "just exploring", "not sure", "open") AND has answered at least 4 questions
5. The conversation has naturally concluded — both sides seem done

Answer ONLY with valid JSON in exactly this format — nothing else:
{{"decision": "YES"}} or {{"decision": "NO"}}"""

    try:
        raw = claude_service.generate_response(prompt)
        # Extract JSON object from the response (strips surrounding text/code fences)
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start == -1 or end <= start:
            logger.warning("Conversation completion check: no JSON found in response, defaulting to NO")
            return False
        parsed = _ConversationEndDecision.model_validate_json(raw[start:end])
        is_complete = parsed.decision == "YES"
        if is_complete:
            logger.info(f"LLM detected natural conversation completion at Q#{questions_asked}")
        return is_complete
    except Exception as e:
        logger.warning(f"Conversation completion detection failed (defaulting to continue): {e}")
        return False


def check_completion_node(state: S3MaxState) -> Dict[str, Any]:
    """Central guardrail check — determines if qualification should continue or evaluate.

    Returns route decision:
    - "continue": Ask another question
    - "early_exit": Confidence high enough for early evaluation
    - "safety_net": Hit max questions, must evaluate now
    """
    questions_asked = state.get("questions_asked", 0)
    confidence = state.get("confidence_score", 0.0)

    # 1. DISINTEREST EXIT: User explicitly rejected or asked to stop
    if state.get("is_disinterested", False):
        logger.info("User explicitly expressed disinterest. Triggering early chat closure.")
        return {"route": "early_exit"}

    # 2. HARD MINIMUM: Must ask at least MIN_QUESTIONS
    if questions_asked < MIN_QUESTIONS:
        logger.debug(f"Below minimum questions ({questions_asked}/{MIN_QUESTIONS})")
        return {"route": "continue"}

    # 3. SAFETY NET: Force evaluation at MAX_QUESTIONS
    if questions_asked >= MAX_QUESTIONS:
        logger.info(f"Safety net triggered at {questions_asked} questions")
        return {
            "route": "safety_net",
            "safety_net_triggered": True,
        }

    # 4. EARLY EXIT: High confidence after enough questions
    if can_early_exit(state):
        logger.info(
            f"Early exit at Q#{questions_asked} with confidence {confidence:.2f}"
        )
        return {"route": "early_exit"}

    # 5. NORMAL COMPLETION: All required dimensions covered + good confidence
    covered = state.get("covered_dimensions", [])
    required = state.get("required_dimensions", [])
    if len(covered) >= len(required) and confidence >= 0.7 and questions_asked >= 5:
        logger.info(
            f"Normal completion: all {len(required)} dimensions covered, "
            f"confidence={confidence:.2f}"
        )
        return {"route": "early_exit"}

    # 6. NATURAL CONVERSATION END: After enough questions, use LLM to detect
    #    if the user is done — handles vague/exploratory conversations that
    #    never fill the scorecard but have reached a natural conclusion.
    if questions_asked >= NATURAL_END_CHECK_THRESHOLD:
        if _detect_conversation_completion(state):
            logger.info(
                f"Natural conversation end detected at Q#{questions_asked}, "
                f"triggering evaluation with whatever data was collected"
            )
            return {"route": "early_exit"}

    # 7. CONTINUE: Need more info
    return {"route": "continue"}


def completion_decision(state: S3MaxState) -> str:
    """Route decision function for the conditional edge after check_completion."""
    return state.get("route", "continue")
