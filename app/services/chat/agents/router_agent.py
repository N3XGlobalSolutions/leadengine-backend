"""Router Agent — Classifies incoming user messages.

Determines the appropriate route for each message:
- greeting: First interaction or conversation restart
- contact: Phone/name collection
- response: Answer to a qualification question  
- question: User asking about the product/service
- off_topic: Abuse or irrelevant messages
"""

import re
from typing import Dict, Any

from app.core.logging_config import get_logger
from app.services.chat.agents.graph_state import S3MaxState
from app.services.ai.claude_service import claude_service

logger = get_logger(__name__)

# Phone patterns (reused from existing state machines)
PHONE_PATTERNS = [
    r"[6-9]\d{9}",           # Indian mobile: 9876543210
    r"\+91[-.\s]?[6-9]\d{9}",  # Indian with code: +91 9876543210
]

# Question indicators
QUESTION_INDICATORS = [
    "?", "what is", "what are", "what's", "how much", "how many",
    "how do", "how can", "how is", "how does", "when is", "when can",
    "where is", "where can", "why is", "why do", "can you", "can i",
    "could you", "do you", "does it", "is there", "are there",
    "tell me about", "tell me", "explain", "describe",
    "i want to know", "what about", "how about",
    "more information", "more details", "more info", "know more",
    "learn more", "share more", "send me", "send details",
    "i need details", "i need info", "give me details",
    "brochure", "catalog", "price list", "pricing",
]

# Greeting patterns
GREETING_PATTERNS = [
    "hi", "hello", "hey", "good morning", "good afternoon",
    "good evening", "start", "begin", "let's start",
]

# Non-name words that appear after "i'm" in trigger phrases — not actual names
_NON_NAME_WORDS = {
    "interested", "looking", "glad", "happy", "here", "ready",
    "just", "not", "trying", "excited", "curious", "a", "the",
    "an", "in", "on", "at", "from", "with", "for", "ok", "okay",
}


def extract_phone(text: str) -> str | None:
    """Extract phone number from text."""
    for pattern in PHONE_PATTERNS:
        match = re.search(pattern, text)
        if match:
            phone = re.sub(r"[^\d+]", "", match.group())
            if phone.startswith("+91"):
                phone = phone[3:]
            if len(phone) == 10 and phone[0] in "6789":
                return phone
    return None


def extract_name(text: str) -> str | None:
    """Extract name from text using common patterns.
    
    Only matches patterns where the captured text looks like an actual
    person name (1-3 capitalized words, not generic phrases).
    """
    name_patterns = [
        r"(?:my name is|this is|call me)\s+([A-Za-z]+(?:\s+[A-Za-z]+)?)",
        r"(?:i am|i'm)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)",  # Only matches capitalized names after i am/i'm
        r"^([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)$",  # Just a capitalized name on its own line
    ]
    for pattern in name_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            name = match.group(1).strip()
            first_word = name.split()[0].lower()
            # Reject if it's a non-name word or too short
            if (
                len(name) >= 2
                and first_word not in ("hi", "hello", "hey", "yes", "no")
                and first_word not in _NON_NAME_WORDS
            ):
                return name
    return None


def is_user_question(text: str) -> bool:
    """Detect if the user is asking a question about the product/service."""
    text_lower = text.lower().strip()
    return any(indicator in text_lower for indicator in QUESTION_INDICATORS)


def is_greeting(text: str) -> bool:
    """Detect if the message is a greeting."""
    text_lower = text.lower().strip().rstrip("!.")
    return text_lower in GREETING_PATTERNS or len(text_lower) <= 3


def is_initial_trigger(text: str) -> bool:
    """Detect if this is the hidden start trigger message sent by the frontend.

    Frontend sends: "Hello! I'm interested in {companyName}."
    This should be treated as a greeting, not a contact data message.
    """
    text_lower = text.lower().strip()
    return (
        text_lower.startswith("hello!")
        or text_lower.startswith("hello,")
        or ("interested in" in text_lower and len(text_lower) < 80)
    )

def classify_contact_intent(message: str) -> str:
    """Classify the user intent when trying to collect contact info using a few-shot LLM approach."""
    prompt = f"""Classify the user's intent when asked for their phone number. Focus on the actual intent.
Categorize strictly into one of these 3 outputs:
SKEPTICAL: User is hesitant, privacy-conscious, or asking WHY we need the number. 
PRODUCT_QUESTION: User is asking a legitimate question about the company, services, or properties.
GENERAL: User is just saying hello, making a general comment, or providing their name.

Examples:
User: "tell me about your company" -> PRODUCT_QUESTION
User: "why do you need my number?" -> SKEPTICAL
User: "I don't share my info online" -> SKEPTICAL
User: "what properties do you have?" -> PRODUCT_QUESTION
User: "Sure, my name is John" -> GENERAL

Message: "{message}"

Output ONLY the category name (SKEPTICAL, PRODUCT_QUESTION, or GENERAL). Nothing else."""

    try:
        response = claude_service.generate_response(prompt).strip().upper()
        if "PRODUCT_QUESTION" in response:
            return "PRODUCT_QUESTION"
        elif "SKEPTICAL" in response:
            return "SKEPTICAL"
        else:
            return "GENERAL"
    except Exception as e:
        logger.error(f"Intent classification failed: {e}")
        return "GENERAL"

def router_node(state: S3MaxState) -> Dict[str, Any]:
    """Classify the incoming message and set the route.
    
    Routing logic:
    1. Initial trigger message? → greeting (skip contact extraction)
    2. No phone yet? → Check if message contains phone → contact
    3. Has phone but no name? → Check if message has name → contact
    4. First message or greeting? → greeting
    5. User asking a question? → question
    6. Otherwise → response (answer to qualification question)
    """
    message = state.get("current_message", "").strip()
    phone = state.get("phone")
    name = state.get("name")
    messages = state.get("messages", [])
    
    # Empty message edge case
    if not message:
        return {"route": "greeting"}

    # Contact consent interception: if consent was asked but not yet answered,
    # capture the user's reply and route back to the consent node.
    # EXCEPTION: if the user is asking a product question, answer it via RAG first,
    # then the graph will re-ask consent after answering.
    if state.get("contact_consent_asked") and not state.get("contact_consent_answer"):
        # Prioritize product/service questions over consent capture
        if is_user_question(message):
            logger.info("Consent pending but user asked a product question — routing to RAG first")
            return {"route": "question"}

        msg_lower = message.lower()
        if any(w in msg_lower for w in ["yes", "sure", "okay", "ok", "yeah", "yep", "of course", "absolutely"]):
            consent = "yes"
        elif any(w in msg_lower for w in ["no", "nope", "don't", "do not", "not", "never", "nah"]):
            consent = "no"
        else:
            consent = "maybe"
        logger.info(f"Contact consent captured: {consent!r} from message: {message!r}")
        return {"route": "contact_consent", "contact_consent_answer": consent}
    
    # 0. The hidden "start" trigger from the frontend — always treat as greeting
    #    This prevents name extraction from "Hello! I'm interested in {CompanyName}."
    if is_initial_trigger(message) and len(messages) <= 1:
        return {"route": "greeting"}
    
    # 1. Check for phone number in message
    extracted_phone = extract_phone(message)
    if extracted_phone and not phone:
        extracted_name = extract_name(message)
        return {
            "route": "contact",
            "phone": extracted_phone,
            "name": extracted_name or name,
        }
    
    # 2. If we don't have phone yet, check if it's a valid knowledge base question or skepticism
    if not phone:
        intent = classify_contact_intent(message)
        extracted_name = extract_name(message)
        name_to_return = extracted_name or name

        if intent == "PRODUCT_QUESTION":
            return {
                "route": "question",
                "name": name_to_return
            }
        elif intent == "SKEPTICAL":
            return {
                "route": "contact",
                "name": name_to_return,
                "is_skeptical_intent": True
            }

        # GENERAL intent - Keep asking for contact
        if extracted_name:
            return {
                "route": "contact",
                "name": extracted_name,
            }
        return {"route": "contact"}
    
    # 3. If phone collected but no name, try to extract
    if phone and not name:
        extracted_name = extract_name(message)
        if extracted_name:
            return {
                "route": "contact",
                "name": extracted_name,
            }
        # If they haven't provided name after phone, treat their next message
        # as a potential name or just continue to qualification
        # (name isn't strictly mandatory like phone)

    # 4. First message / greeting — ONLY if we don't have full contact info yet
    #    Once phone+name are collected, NEVER send back to greeting (even for 'yes', 'ok', etc.)
    if not (phone and name):
        if len(messages) <= 1 or is_greeting(message):
            return {"route": "greeting"}
    
    # 5. User asking about the product
    if is_user_question(message):
        return {"route": "question"}
    
    # 6. Default: treat as a response to the current qualification question
    return {"route": "response"}
