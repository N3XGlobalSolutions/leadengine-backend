"""S3 Max LangGraph — Main StateGraph wiring all agents together.

This is the core orchestration graph that routes messages through:
Router → (Greet | Collect Contact | Analyze → Memory → Check → Generate/Evaluate | RAG)
"""

from typing import Any, Dict
import json

from langgraph.graph import END, StateGraph

from app.core.logging_config import get_logger
from app.services.chat.agents.graph_state import S3MaxState
from app.services.chat.agents.router_agent import router_node
from app.services.chat.agents.response_analyzer_agent import analyze_response_node
from app.services.chat.agents.memory_agent import update_memory_node
from app.services.chat.agents.question_generator_agent import generate_question_node
from app.services.chat.agents.qualification_evaluator_agent import evaluate_qualification_node
from app.services.chat.s3_max_guardrails import check_completion_node, completion_decision

logger = get_logger(__name__)


# ── Greeting Node ──

def _generate_greeting(name: str, kb_context: str, company_name: str = "") -> str:
    """Generate a natural, LLM-crafted opening message.

    Always asks for name+phone (or just phone if name is known).
    Falls back to a simple human-like string if Claude is unavailable.
    """
    from app.services.ai.claude_service import claude_service
    import random

    company = company_name.strip() if company_name else ""
    company_label = f"at {company}" if company else ""
    company_hint = (kb_context or "") or f"{company or 'a sales'} company"

    if name:
        prompt = f"""You are a friendly sales assistant {company_label}opening a chat with a customer named {name}.

Company: {company or "our company"}
Context: {company_hint}

Write a single short message (1-2 sentences, max 30 words) that:
1. Warmly acknowledges {name} by name{f' and mentions {company}' if company else ''}
2. Naturally asks for their phone number so the team can follow up
3. Sounds like a real human, not a bot — casual, warm, no corporate jargon

Just write the message, no quotes, no explanation."""
        fallbacks = [
            f"Hey {name}! 😊 Could you drop your phone number so the {company or 'our'} team can reach out?",
            f"Nice to connect, {name}! 🤝 What's the best number to reach you on?",
            f"Thanks for reaching out, {name}! Share your number and we'll get back to you shortly.",
        ]
    else:
        prompt = f"""You are a friendly sales assistant {company_label}opening a chat with a new customer.

Company: {company or "our company"}
Context: {company_hint}

Write a single short message (1-2 sentences, max 35 words) that:
1. Gives a warm, casual greeting{f' — mention {company} naturally' if company else ''}
2. Asks for the customer's name AND phone number
3. Sounds like a genuine human, not a scripted bot
4. No "I'd love to help", no "As an AI", no corporate jargon

Just write the message, no quotes, no explanation."""
        fallbacks = [
            f"Hey! 👋 Welcome to {company}. To get started, what's your name and phone number?" if company else "Hey! 👋 To get started, what's your name and phone number?",
            f"Hi! 😊 Quick one — what's your name and best number? {company + ' ' if company else ''}will be in touch shortly.",
            f"Hey, welcome{' to ' + company if company else ''}! What's your name and the best number to reach you?",
        ]

    try:
        response = claude_service.generate_response(prompt)
        text = response.strip().strip('"').strip("'")
        if text and len(text) > 10:
            return text
    except Exception as e:
        logger.warning(f"Greeting generation failed, using fallback: {e}")

    return random.choice(fallbacks)


def greet_node(state: S3MaxState) -> Dict[str, Any]:
    """Generate greeting and ask for contact info.

    If phone is already collected, do NOT ask for it again — instead
    transition warmly into the qualification phase.
    """
    name = state.get("name", "") or ""
    phone = state.get("phone")
    kb_context = state.get("kb_context", "") or ""
    company_name = state.get("company_name", "") or ""

    # If we already have both contact details, skip the greeting entirely
    if phone and name:
        return {
            "bot_response": (
                f"Great, {name}! 🎉 I'd love to understand your needs better "
                f"so I can help you find the perfect solution. Let me ask you a few questions!"
            ),
            "next_state": "qualifying_agentic",
            "quick_replies": [],
        }

    greeting = _generate_greeting(name, kb_context, company_name)

    return {
        "bot_response": greeting,
        "next_state": "contact",
        "quick_replies": [],
    }



def collect_contact_node(state: S3MaxState) -> Dict[str, Any]:
    """Handle contact info collection (phone is mandatory).
    
    Detects when the user is skeptical or asking 'why' and responds
    with an empathetic, trust-building LLM response instead of a
    robotic script.
    """
    phone = state.get("phone")
    name = state.get("name")
    message = state.get("current_message", "")

    if phone and name:
        # Both collected — transition to qualification
        return {
            "bot_response": (
                f"Thanks, {name}! 🎉 Great to meet you.\n\n"
                f"I'd love to understand your needs better so I can help you "
                f"find the perfect solution. Let me ask you a few questions!"
            ),
            "next_state": "qualifying_agentic",
            "quick_replies": [],
        }

    if phone and not name:
        # Phone but no name — ask for name
        return {
            "bot_response": (
                "Thank you for sharing your number! 📱\n\n"
                "And what should I call you?"
            ),
            "next_state": "contact",
            "quick_replies": [],
        }

    # User hasn't provided phone yet — check if they were routed here due to skepticism
    is_skeptical = state.get("is_skeptical_intent", False)
    if message and is_skeptical:
        # Use LLM to generate a warm, honest, human-like trust response
        try:
            # Execute tool call using the Claude model
            # S3_MAX exclusively uses Claude 4.5 Haiku
            from app.services.ai.claude_service import claude_service

            lqc = state.get("lqc_criteria", "")

            prompt = f"""You are a friendly, honest AI assistant for a real estate/sales company. 
A potential customer named "{name or 'the user'}" is hesitant to share their phone number.
They said: "{message}"

Your job is to respond in an incredibly warm, genuine, and HUMAN-LIKE tone that:
1. Empathizes and completely validates their hesitation.
2. Explains WHO you are naturally (an automated assistant built to pair them with the perfect expert).
3. Transparently tells them WHY you need the number (just to follow up personally with exactly what they want - absolutely no spam).
4. Highlights that their privacy is strictly protected.

Context about the service: {lqc[:300] if lqc else "helping customers find the right property/product"}

Keep the response EXTREMELY SHORT (1-2 very short sentences max), warm, and genuinely human. No lists. No corporate tone. 
Write as if you're a friendly person chatting on WhatsApp. Use a friendly emoji once if appropriate. End with a gentle, pressure-free ask to share it."""

            response = claude_service.generate_response(prompt)
            if response and len(response.strip()) > 20:
                return {
                    "bot_response": response.strip(),
                    "next_state": "contact",
                    "quick_replies": [],
                }
        except Exception:
            pass  # Fall through to default if LLM fails

    # Default — either not skeptical, or LLM failed/returned too short a response
    kb_context = state.get("kb_context", "") or ""
    company_name = state.get("company_name", "") or ""
    return {
        "bot_response": _generate_greeting(name, kb_context, company_name),
        "next_state": "contact",
        "quick_replies": [],
    }



def _extract_info_from_rag_turn(state: S3MaxState, user_message: str) -> Dict[str, Any]:
    """Option C: Lightweight LLM extraction of qualification data from a RAG-routed message.

    When a user message is routed to RAG (bypassing analyze_response), we still
    want to capture any qualification data they revealed. This runs a cheap,
    focused extraction and merges results into the scorecard.
    """
    from app.services.ai.claude_service import claude_service

    scorecard = dict(state.get("scorecard", {}))
    covered = list(state.get("covered_dimensions", []))
    current_question = state.get("question_history", [])[-1] if state.get("question_history") else ""

    prompt = f"""Extract qualification data a user revealed in this message. Be very strict: only extract if clearly stated.

The bot had just asked: "{current_question or 'a qualification question'}"
User said: "{user_message}"

Return a JSON object with ONLY the dimensions explicitly mentioned. Leave out anything not clearly stated.
Possible dimensions: budget, timeline, need, property_requirements, location, call_consent, authority, motivation

Examples:
- "only for investment" -> {{"need": {{"value": "investment only", "confidence": 0.9}}}}
- "3-6 months" -> {{"timeline": {{"value": "3-6 months", "confidence": 0.95}}}}
- "set up a chat" -> {{}} (this is a request, not qualification data)
- "open to suggestions" -> {{"property_requirements": {{"value": "open to all types", "confidence": 0.7}}}}
- "commercial spaces" -> {{"property_requirements": {{"value": "commercial spaces", "confidence": 0.9}}}}
- "250K" -> {{"budget": {{"value": "up to $250K / ₹250K", "confidence": 0.9}}}}

Return ONLY valid JSON (empty object if nothing to extract):"""

    try:
        response = claude_service.generate_response(prompt)
        start = response.find("{")
        end = response.rfind("}") + 1
        if start == -1 or end <= start:
            return {}

        extracted = json.loads(response[start:end])
        updates: Dict[str, Any] = {}

        for dim, entry in extracted.items():
            if isinstance(entry, dict) and entry.get("value") and dim not in scorecard:
                scorecard[dim] = entry
                if dim not in covered:
                    covered.append(dim)

        if scorecard != state.get("scorecard", {}):
            updates["scorecard"] = scorecard
            updates["covered_dimensions"] = covered
            logger.info(f"RAG turn extraction added: {list(extracted.keys())}")

        return updates
    except Exception as e:
        logger.warning(f"RAG turn extraction failed (non-critical): {e}")
        return {}


# ── RAG Answer Node ──

def _has_sufficient_rag_knowledge(answer: str, sources: list) -> bool:
    """Check if the RAG answer is based on actual knowledge or is a generic fallback."""
    if not sources:
        return False
    # Detect generic fallback phrases that indicate no real knowledge was found
    fallback_indicators = [
        "don't see that info",
        "don't have that information",
        "no relevant information",
        "not in my knowledge",
        "i don't have specific",
        "couldn't find specific",
        "i'm not sure about",
        "get someone from the team",
        "can get someone",
    ]
    answer_lower = answer.lower()
    return not any(indicator in answer_lower for indicator in fallback_indicators)


def _refine_rag_answer_with_context(raw_answer: str, state: S3MaxState) -> str:
    """Post-process a RAG answer through Claude with full conversation context.

    This prevents the RAG answer from re-asking for information the user
    already provided (budget, location, timeline, etc.).
    """
    from app.services.ai.claude_service import claude_service

    messages = state.get("messages", [])
    scorecard = state.get("scorecard", {})
    current_message = state.get("current_message", "")
    name = state.get("name", "")

    # Build conversation context
    convo_lines = []
    for m in messages[-12:]:  # Last 12 messages for context
        role = "User" if m.get("role") == "user" else "Bot"
        convo_lines.append(f"{role}: {m.get('content', '')}")
    convo_text = "\n".join(convo_lines)

    # Build collected data summary
    collected = {k: v.get("value", "") for k, v in scorecard.items() if isinstance(v, dict) and v.get("value")}

    prompt = f"""You are refining a knowledge base answer for a customer chat. The raw answer below may contain redundant questions that the user ALREADY answered. Your job is to clean it up.

RAW KNOWLEDGE BASE ANSWER:
{raw_answer}

USER'S CURRENT MESSAGE: {current_message}

DATA ALREADY COLLECTED FROM THIS USER:
{json.dumps(collected, indent=2) if collected else 'None'}

RECENT CONVERSATION:
{convo_text}

RULES:
1. Keep the factual content from the raw answer — it's from the knowledge base and is accurate.
2. REMOVE any questions asking for budget, location, timeline, property type, or anything the user already shared above.
3. If the raw answer asks for info already provided, replace that part with a natural acknowledgment like "With your $X budget..." or "For your timeline of X months...".
4. Keep it short (2-3 sentences max), friendly, and conversational.
5. Address the user as "{name}" if appropriate.
6. Do NOT add any new information not in the raw answer.

Refined answer:"""

    try:
        refined = claude_service.generate_response(prompt)
        return refined.strip() if refined.strip() else raw_answer
    except Exception as e:
        logger.warning(f"RAG answer refinement failed (using raw): {e}")
        return raw_answer


def answer_rag_node(state: S3MaxState) -> Dict[str, Any]:
    """Answer user's product/service question using RAG.

    Three-layer processing:
    1. Get raw answer from RAG knowledge base
    2. If RAG has no relevant knowledge → graceful handoff to team + close chat
    3. If RAG has knowledge → refine answer with conversation context to avoid re-asking
    4. Run Option C extraction to save any qualification data
    """
    from app.services.ai.rag_service import rag_service

    query = state.get("current_message", "")
    tenant_id = state.get("tenant_id", "")
    name = state.get("name", "")

    try:
        answer, sources = rag_service.answer_query_simple(query, tenant_id, use_claude=True)
    except Exception as e:
        logger.error(f"RAG query failed: {e}")
        answer = ""
        sources = []

    # === Layer 1: Insufficient knowledge detection ===
    if not _has_sufficient_rag_knowledge(answer, sources):
        name_insert = f", {name}" if name else ""
        handoff_msg = (
            f"That's a great question{name_insert}! I don't have the specific details "
            f"on that right now, but our team absolutely can help. "
            f"I'll make sure someone reaches out to you with the exact information you need. 😊"
        )

        # If phone is not collected yet, still try to get it before closing
        if not state.get("phone"):
            handoff_msg += (
                "\n\nBefore I connect you with the team, could you share "
                "your phone number so they can reach you directly?"
            )
            return {
                "bot_response": handoff_msg,
                "kb_context": "",
                "just_answered_rag": True,
            }

        # Phone already collected → hand off to team but continue qualification
        # NOTE: Do NOT set is_disinterested here. The user is actively asking
        # questions, which is the opposite of disinterested. Setting it would
        # cause check_completion to prematurely end the conversation.
        return {
            "bot_response": handoff_msg,
            "kb_context": "",
            "just_answered_rag": True,
        }

    # === Layer 2: Refine RAG answer with conversation context ===
    answer = _refine_rag_answer_with_context(answer, state)

    # If phone is not collected yet, append a gentle nudge
    if not state.get("phone"):
        name_insert = f" {name}" if name else ""
        answer += f"\n\nBy the way{name_insert}, to help you better, could you share your phone number with me?"

    # === Layer 3: Option C extraction ===
    scorecard_updates = _extract_info_from_rag_turn(state, query)

    return {
        "bot_response": answer,
        "kb_context": answer[:500],
        "just_answered_rag": True,
        **scorecard_updates,
    }


# ── Create Lead Node ──

def create_lead_node(state: S3MaxState) -> Dict[str, Any]:
    """Finalize the lead — this is a passthrough to mark completion.
    
    Actual lead creation happens in the state machine adapter (chat.py),
    not inside the graph, because it needs DynamoDB access.
    """
    return {
        "is_complete": True,
        "show_actions": True,
    }


# ── Routing Functions ──

def route_decision(state: S3MaxState) -> str:
    """Route decision function for the conditional edge after router."""
    route = state.get("route", "greeting")
    
    # Map routes to node names
    route_map = {
        "greeting": "greet",
        "contact": "collect_contact",
        "response": "analyze_response",
        "question": "answer_rag",
        "off_topic": "greet",
        "contact_consent": "contact_consent",
    }
    return route_map.get(route, "greet")


def post_rag_decision(state: S3MaxState) -> str:
    """After RAG answer, decide what to do next.

    Priority:
    1. If consent was asked but not yet answered (user asked a RAG question during
       consent phase) → re-surface the consent question after answering.
    2. If qualifying with phone collected → generate next question.
    3. Otherwise → END (wait for user).
    """
    phone = state.get("phone")

    # Re-ask consent if it was pending when the user detoured into RAG
    if state.get("contact_consent_asked") and not state.get("contact_consent_answer"):
        return "contact_consent"

    # Only generate question if we're in qualification (phone collected)
    questions_asked = state.get("questions_asked", 0)
    if phone and questions_asked > 0:
        return "generate_question"
    return END


# ── Contact Consent Node ──

def contact_consent_node(state: S3MaxState) -> Dict[str, Any]:
    """Ask user for permission to contact them after all qualification questions.

    Two-phase node:
    1. First pass (consent not yet answered): Generate consent question via Claude
       and surface quick replies [Yes, No, Maybe].
       If a RAG answer already exists in bot_response (user asked a product question
       before consenting), APPEND the consent question rather than overwriting it.
    2. Second pass (consent answer present in state): Return empty update so the
       conditional edge can route straight to evaluate_qualification.
    """
    from app.services.ai.claude_service import claude_service

    # Second pass — answer already captured by router, just let the edge route
    if state.get("contact_consent_answer"):
        return {}

    # First pass — generate the consent question
    name = state.get("name", "") or ""
    company_name = state.get("company_name", "") or ""
    company_label = f" from {company_name}" if company_name else ""

    prompt = f"""You are a friendly sales assistant{company_label}. You just finished a brief qualification chat with {name or 'the customer'}.

Now you need to ask them ONE simple question: whether they are comfortable being contacted by the sales team.

Write a single short closing message (1-2 sentences, max 30 words) that:
1. Thanks them warmly for their time
2. Naturally asks if the team can contact them — phrase it as a question
3. Sounds human and casual, NOT corporate
4. Keep it very short — they're almost done!

Just write the message, no quotes, no explanation."""

    try:
        response = claude_service.generate_response(prompt)
        text = response.strip().strip('"').strip("'")
        if not text or len(text) < 10:
            raise ValueError("Response too short")
    except Exception as e:
        logger.warning(f"Consent message generation failed, using fallback: {e}")
        name_insert = f", {name}" if name else ""
        text = f"Thanks for chatting{name_insert}! 😊 Would it be okay if our team reaches out to you?"

    # If the RAG node already produced a bot_response this turn (user asked a product
    # question while consent was pending), append the consent ask instead of overwriting.
    existing_response = (state.get("bot_response") or "").strip()
    if existing_response:
        final_response = f"{existing_response}\n\n{text}"
    else:
        final_response = text

    return {
        "bot_response": final_response,
        "quick_replies": ["Yes", "No", "Maybe"],
        "contact_consent_asked": True,
    }


def consent_decision(state: S3MaxState) -> str:
    """Route after contact_consent_node:
    - If answer captured → move to evaluate_qualification
    - Otherwise → END (wait for user to reply)
    """
    if state.get("contact_consent_answer"):
        return "evaluate_qualification"
    return END



def build_s3_max_graph() -> StateGraph:
    """Construct the S3 Max LangGraph StateGraph.

    Flow:
    router → (greet | collect_contact | analyze_response | answer_rag)
    analyze_response → update_memory → check_completion → (generate_question | evaluate)
    answer_rag → generate_question (if qualifying) | END
    evaluate → create_lead → END
    """
    graph = StateGraph(S3MaxState)

    # Add nodes
    graph.add_node("router", router_node)
    graph.add_node("greet", greet_node)
    graph.add_node("collect_contact", collect_contact_node)
    graph.add_node("analyze_response", analyze_response_node)
    graph.add_node("update_memory", update_memory_node)
    graph.add_node("check_completion", check_completion_node)
    graph.add_node("generate_question", generate_question_node)
    graph.add_node("evaluate_qualification", evaluate_qualification_node)
    graph.add_node("answer_rag", answer_rag_node)
    graph.add_node("create_lead", create_lead_node)
    graph.add_node("contact_consent", contact_consent_node)

    # Entry point
    graph.set_entry_point("router")

    # Router → conditional dispatch
    graph.add_conditional_edges(
        "router",
        route_decision,
        {
            "greet": "greet",
            "collect_contact": "collect_contact",
            "analyze_response": "analyze_response",
            "answer_rag": "answer_rag",
            "contact_consent": "contact_consent",
        },
    )

    # Terminal nodes (return response to user)
    graph.add_edge("greet", END)
    graph.add_edge("collect_contact", END)

    # Qualification pipeline
    graph.add_edge("analyze_response", "update_memory")
    graph.add_edge("update_memory", "check_completion")

    # Check completion → contact consent → evaluate
    graph.add_conditional_edges(
        "check_completion",
        completion_decision,
        {
            "continue": "generate_question",
            "early_exit": "contact_consent",
            "safety_net": "contact_consent",
        },
    )

    # Contact consent → evaluate (if answered) or END (wait for reply)
    graph.add_conditional_edges(
        "contact_consent",
        consent_decision,
        {
            "evaluate_qualification": "evaluate_qualification",
            END: END,
        },
    )

    # Terminal after question generation
    graph.add_edge("generate_question", END)

    # RAG → re-ask consent (if pending) OR generate question OR END
    graph.add_conditional_edges(
        "answer_rag",
        post_rag_decision,
        {
            "contact_consent": "contact_consent",
            "generate_question": "generate_question",
            END: END,
        },
    )

    # Evaluation → lead creation
    graph.add_edge("evaluate_qualification", "create_lead")
    graph.add_edge("create_lead", END)

    return graph


# ── Compiled Graph Singleton ──

_compiled_graph = None


def get_s3_max_graph():
    """Get compiled S3 Max graph (singleton)."""
    global _compiled_graph
    if _compiled_graph is None:
        graph = build_s3_max_graph()
        _compiled_graph = graph.compile()
    return _compiled_graph


def reset_s3_max_graph():
    """Force-rebuild the compiled graph (useful after hot-reload / testing)."""
    global _compiled_graph
    _compiled_graph = None
