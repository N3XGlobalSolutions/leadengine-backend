"""Question Generator Agent — Generates the next qualification question.

Uses a hybrid strategy:
1. Question Bank (60%): Pick from pre-generated questions
2. Dynamic Follow-up (40%): Generate contextual follow-up based on last answer

Includes guardrails:
- Deduplication check against question_history
- Relevance scoring against uncovered dimensions
- Conversational transition before each new question
"""

import json
import random
from typing import Any, Dict, List

from app.core.logging_config import get_logger
from app.services.chat.agents.graph_state import S3MaxState
from app.utils.localization import get_message, get_language_name

logger = get_logger(__name__)


def _get_llm_service():
    """Lazy import to avoid circular dependencies."""
    # S3_MAX exclusively uses Claude 4.5 Haiku
    from app.services.ai.claude_service import claude_service
    return claude_service


def _pick_from_bank(
    bank: List[Dict[str, Any]],
    covered: List[str],
    history: List[str],
) -> Dict[str, Any] | None:
    """Pick the best unasked question from the question bank.

    Prioritizes:
    1. Questions about uncovered dimensions
    2. High-priority questions
    3. Questions not yet asked
    """
    # Filter out already-asked questions (by semantic similarity)
    history_lower = [q.lower()[:50] for q in history]

    candidates = []
    for q in bank:
        q_text = q.get("question", "")
        q_dimension = q.get("dimension", "")
        
        # Skip if question text is too similar to one already asked
        if any(q_text.lower()[:50] in h or h in q_text.lower()[:50] for h in history_lower if h):
            continue

        # Prioritize uncovered dimensions
        priority_score = 0
        if q_dimension and q_dimension not in covered:
            priority_score += 10
        if q.get("priority") == "high":
            priority_score += 5
        elif q.get("priority") == "medium":
            priority_score += 3

        candidates.append((priority_score, q))

    if not candidates:
        return None

    # Sort by priority and pick from top candidates (with some randomness)
    candidates.sort(key=lambda x: x[0], reverse=True)
    top = candidates[:3]
    return random.choice(top)[1]


def _build_already_answered_summary(state: S3MaxState) -> str:
    """Build a human-readable summary of everything the user has already shared.

    Reads from:
    1. Scorecard (most reliable, structured data)
    2. Full message history (catches RAG-routed turns)
    3. current_message (the ACTIVE turn's user input — critical for in-flight context)
    """
    scorecard = state.get("scorecard", {})
    messages = state.get("messages", [])
    current_message = state.get("current_message", "").strip()

    lines = []

    # 1. From scorecard (most reliable, structured)
    for dim, entry in scorecard.items():
        if isinstance(entry, dict) and entry.get("value"):
            lines.append(f"- {dim}: {entry['value']}")

    # 2. From full message history — all user turns, no cap
    pure_greetings = {"hi", "hello", "hey", "sure", "ok", "okay", "yes", "no", "start"}
    seen_content = set()
    for m in messages:
        if m.get("role") != "user":
            continue
        content = m.get("content", "").strip()
        content_lower = content.lower()
        if content_lower in pure_greetings or len(content) < 3:
            continue
        if content_lower in seen_content:
            continue
        seen_content.add(content_lower)
        # Skip if already captured in scorecard
        already_in_scorecard = any(
            content_lower in str(v.get("value", "")).lower()
            for v in scorecard.values() if isinstance(v, dict)
        )
        if already_in_scorecard:
            continue
        lines.append(f"- (from conversation): {content}")

    # 3. Current message (the live user input this turn — may not be in messages yet)
    if current_message and current_message.lower() not in pure_greetings and len(current_message) >= 3:
        current_lower = current_message.lower()
        if current_lower not in seen_content:
            lines.append(f"- (current message): {current_message}")

    return "\n".join(lines) if lines else "Nothing specific collected yet."


def _generate_dynamic_followup(
    state: S3MaxState,
    llm,
) -> Dict[str, Any]:
    """Generate a contextual follow-up question based on conversation context."""
    scorecard = state.get("scorecard", {})
    covered = state.get("covered_dimensions", [])
    required = state.get("required_dimensions", [])
    lqc = state.get("lqc_criteria", "")
    kb_context = state.get("kb_context", "")
    messages = state.get("messages", [])
    question_history = state.get("question_history", [])

    # Get uncovered dimensions
    uncovered = [d for d in required if d not in covered]

    # Full conversation history — no cap, no truncation
    conversation_text = "\n".join(
        f"{'User' if m.get('role') == 'user' else 'Bot'}: {m.get('content', '')}"
        for m in messages
    )
    # Also append current message if not already in messages
    if current_message := state.get("current_message", "").strip():
        conversation_text += f"\nUser: {current_message}"

    # Rich summary of everything the user has already shared (Option A)
    already_answered = _build_already_answered_summary(state)

    # Structured scorecard data
    collected = {k: v.get("value", "") for k, v in scorecard.items() if isinstance(v, dict)}

    rag_instruction = (
        "IMPORTANT: The user just asked a product question which was ALREADY answered "
        "by the knowledge base. Generate ONLY the next qualification question directly "
        "without re-answering their question."
        if state.get("just_answered_rag")
        else ""
    )

    language = state.get("language", "en")
    language_name = get_language_name(language)

    prompt = f"""You are a professional sales assistant qualifying a lead on behalf of a company.
Your ONLY job is to gather the specific information described in the QUALIFICATION CRITERIA below.
Speak ONLY in {language_name}.
You must NEVER invent topics, ask about scheduling meetings, or discuss anything outside the scope of the QUALIFICATION CRITERIA.

============================
QUALIFICATION CRITERIA (THIS IS YOUR BIBLE — stay strictly within this scope):
{lqc or 'Understand the customer need, budget, and timeline for this product/service.'}
============================

COMPANY / PRODUCT CONTEXT (use this to make questions feel natural and brand-specific):
{kb_context[:600] if kb_context else 'A product/service company. Ask relevant qualifying questions.'}

STRUCTURED DATA ALREADY COLLECTED (from analysis pipeline):
{json.dumps(collected, indent=2) if collected else 'None yet'}

EVERYTHING THE USER HAS SHARED IN THE CONVERSATION (the single most important context for avoiding repetition):
{already_answered}

DIMENSIONS FROM QUALIFICATION CRITERIA NOT YET COVERED: {', '.join(uncovered) if uncovered else 'All key qualification dimensions are covered. Move to close.'}

PREVIOUSLY ASKED BOT QUESTIONS (never repeat these):
{chr(10).join(f'- {q}' for q in question_history[-10:])}

RECENT CONVERSATION (last 8 turns, for tone and context):
{conversation_text}

{rag_instruction}

============================
YOUR TASK: Generate the single best next qualification question.
============================

CRITICAL RULES — violating any of these is a failure:
1. STAY IN SCOPE: Every question you ask must directly relate to gathering information described in the QUALIFICATION CRITERIA. If the criteria doesn't mention scheduling, visits, or calls — DO NOT ask about them.
2. NEVER REPEAT: Do not ask about anything the user has already mentioned anywhere in the conversation history above.
3. DO NOT INVENT TOPICS: Never introduce topics not present in the QUALIFICATION CRITERIA or COMPANY CONTEXT.
4. CONVERSATIONAL TONE: Sound like a knowledgeable, warm human consultant — not a robot or a form.
5. SHORT & FOCUSED: One question per turn. Acknowledge the last answer warmly in 1 sentence, then ask the next question.
6. QUICK REPLIES: Offer 2-4 highly relevant, conversational reply options based on what a real user would say.
7. IF ALL COVERED: If all dimensions are already covered, generate a natural closing message summarizing what was gathered instead of a new question, and set question to an empty string.
8. Respond ONLY in {language_name}.

Return ONLY this JSON:
{{
    "transition": "<warm 1-sentence acknowledgment of their last answer — naturally bridges to the question>",
    "question": "<the actual question to ask, OR empty string if all dimensions covered>",
    "dimension": "<which qualification dimension this question covers>",
    "quick_replies": ["<option1>", "<option2>"]
}}

JSON:"""

    try:
        response = llm.generate_response(prompt)
        start = response.find("{")
        end = response.rfind("}") + 1
        if start != -1 and end > start:
            return json.loads(response[start:end])
    except Exception as e:
        logger.error(f"Dynamic question generation failed: {e}")

    # Fallback
    fallback_dimension = uncovered[0] if uncovered else "additional_info"
    return {
        "transition": "Thanks for sharing that!",
        "question": f"Could you tell me more about your {fallback_dimension}?",
        "dimension": fallback_dimension,
        "quick_replies": [],
    }


def generate_question_node(state: S3MaxState) -> Dict[str, Any]:
    """Generate the next qualification question using hybrid strategy.
    
    60% chance: Pick from question bank
    40% chance: Generate dynamic follow-up
    
    Returns updated state with bot_response, quick_replies, etc.
    """
    bank = state.get("question_bank", [])
    covered = state.get("covered_dimensions", [])
    history = state.get("question_history", [])
    questions_asked = state.get("questions_asked", 0)
    evasion_count = state.get("evasion_count", 0)
    messages = state.get("messages", [])

    llm = _get_llm_service()

    # Handle evasive responses (3+ consecutive)
    if evasion_count >= 3:
        lang = state.get("language", "en")
        evasion_data = get_message(lang, "evasion_fallback")

        return {
            "bot_response": evasion_data["resp"],
            "quick_replies": evasion_data["qr"],
            "evasion_count": 0,  # Reset
            "next_state": "qualifying_agentic",
        }

    # Hybrid strategy: bank vs dynamic
    question_data = None
    use_bank = random.random() < 0.6 and bank

    if use_bank:
        question_data = _pick_from_bank(bank, covered, history)

    if not question_data:
        question_data = _generate_dynamic_followup(state, llm)

    # Extract fields
    transition = question_data.get("transition", "")
    question_text = question_data.get("question", "")
    dimension = question_data.get("dimension", "")
    quick_replies = question_data.get("quick_replies", [])

    # Build bot response with conversational transition
    bot_response = ""
    if state.get("just_answered_rag"):
        # If RAG answered, the RAG answer is the transition. Just append the question.
        bot_response = f"{state.get('bot_response', '')}\n\n{question_text}"
    else:
        if transition and questions_asked > 0:
            bot_response = f"{transition}\n\n{question_text}"
        else:
            bot_response = question_text

    # Update tracking
    new_history = list(history)
    new_history.append(question_text)

    logger.info(
        f"Generated Q#{questions_asked + 1}: dimension={dimension}, "
        f"source={'bank' if use_bank else 'dynamic'}"
    )

    return {
        "bot_response": bot_response,
        "quick_replies": quick_replies,
        "question_history": new_history,
        "questions_asked": questions_asked + 1,
        "current_question_id": dimension,
        "next_state": "qualifying_agentic",
    }
