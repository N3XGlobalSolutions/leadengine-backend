"""Decide, per customer message, which tools the sales agent may use.

Sending every tool on every turn makes the model reach for product search on
questions like "tell me about your company", and every tool spec costs prompt
tokens. Here we expose only the tool groups the message actually needs:

- knowledge: company questions, when the pre-fetched reference info found nothing
- catalog:   the customer asks about products / prices / plans, or is following
             up on a product discussion from the previous turn
- calendar:  the customer wants to book / move / cancel a call, and a real
             calendar is connected (never offer fake bookings)
"""

import re
from typing import List, Optional

from app.models.outbound.outbound_entities import OutboundCalendarSettings
from app.core.logging_config import get_logger

logger = get_logger(__name__)

KNOWLEDGE_TOOLS = ["search_company_knowledge"]
CATALOG_TOOLS = [
    "list_products",
    "search_products",
    "get_product_details",
    "send_product_images",
    "send_brochure",
]
CALENDAR_TOOLS = ["check_calendar", "book_meeting", "cancel_meeting", "reschedule_meeting"]

PRODUCT_INTENT_RE = re.compile(
    r"\b(products?|catalog(?:ue)?|items?|price|prices|pricing|priced|cost|costs|"
    r"how much|rates?|fees?|plans?|packages?|models?|variants?|brochures?|"
    r"images?|imgs?|photos?|pics?|pictures?|snaps?|picz|videos?|specs?|specifications?|"
    r"configs?|configurations?|buy|purchase|order|stock|options?|range|collection)\b",
    re.IGNORECASE,
)

# Asking to see something: always a catalog request when products exist.
MEDIA_REQUEST_RE = re.compile(r"\b(images?|imgs?|photos?|pics?|pictures?|snaps?|brochures?|videos?|show me|send me)\b", re.IGNORECASE)

_WORD_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = {
    "the", "and", "for", "with", "pro", "plus", "new", "best", "set", "kit", "pack", "series",
    "edition", "model", "type", "size", "one", "two", "all", "our", "your",
}

SCHEDULE_INTENT_RE = re.compile(
    r"\b(meeting|meet|call|schedule|scheduled|reschedule|book|booking|appointment|"
    r"slots?|availability|demo|cancel|tomorrow|today|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"\d{1,2}(?::\d{2})?\s?(?:am|pm))\b",
    re.IGNORECASE,
)

# Greetings, acknowledgements and one-word replies never need tools or knowledge lookups.
SMALL_TALK_RE = re.compile(
    r"^\s*(hi+|hello+|hey+|hai|ok(?:ay)?|k+|yes|yeah|yep|ya|no|nope|nah|sure|thanks?|"
    r"thank you|thanku|thx|ty|good (?:morning|afternoon|evening|night)|bye|great|cool|"
    r"fine|nice|done|alright|hmm+)\W*$",
    re.IGNORECASE,
)


def is_small_talk(text: str) -> bool:
    return bool(SMALL_TALK_RE.match(text or ""))


def needs_knowledge_lookup(text: str) -> bool:
    """True when the message is substantive enough to be worth a knowledge-base search."""
    text = (text or "").strip()
    if not text or is_small_talk(text):
        return False
    if "?" in text:
        return True
    return len(text.split()) >= 3


def calendar_connected(calendar_settings: Optional[OutboundCalendarSettings]) -> bool:
    """A real Google Calendar is configured (otherwise bookings would be simulated/fake)."""
    if not calendar_settings:
        return False
    try:
        from app.services.outbound.providers.google_calendar_provider import GOOGLE_LIBS_AVAILABLE
    except Exception:
        return False
    return bool(
        GOOGLE_LIBS_AVAILABLE
        and calendar_settings.google_client_id
        and calendar_settings.google_client_secret
        and calendar_settings.google_refresh_token
    )


def _recent_tool_names(session, last_n: int = 6) -> List[str]:
    names: List[str] = []
    for msg in session.history[-last_n:]:
        for call in msg.tool_calls or []:
            name = call.get("name")
            if name:
                names.append(name)
    return names


def _last_assistant_text(session) -> str:
    # history[-1] is the current customer message; look before it.
    for msg in reversed(session.history[:-1]):
        if msg.role == "assistant" and (msg.content or "").strip():
            return msg.content
    return ""


def _tenant_products(session) -> list:
    """The tenant's active catalog (cached on the session for this turn)."""
    if session.products:
        return session.products
    try:
        from app.services.outbound.outbound_product_service import outbound_product_service
        session.products = [p for p in outbound_product_service.list_products(session.tenant_id)
                            if getattr(p, "status", "ACTIVE") == "ACTIVE"]
    except Exception as e:
        logger.warning(f"[ToolRouter] Could not load product catalog for {session.tenant_id}: {e}")
        session.products = []
    return session.products


def _tenant_has_products(session) -> bool:
    return len(_tenant_products(session)) > 0


def catalog_terms(products) -> set:
    """Distinctive words from product names, categories and tags ("pc", "gaming", "b.ed"...)."""
    terms = set()
    for p in products:
        text = " ".join([p.name or "", p.category or ""] + list(getattr(p, "tags", []) or [])
                        + list(getattr(p, "search_keywords", []) or [])).lower()
        for word in _WORD_RE.findall(text):
            if len(word) >= 2 and word not in _STOPWORDS:
                terms.add(word)
    return terms


def mentions_catalog(text: str, products) -> bool:
    words = set(_WORD_RE.findall((text or "").lower()))
    return bool(words & catalog_terms(products))


def _previous_user_text(session) -> str:
    for msg in reversed(session.history[:-1]):
        if msg.role == "user" and (msg.content or "").strip():
            return msg.content
    return ""


def select_tools(session) -> List[str]:
    """Return the names of tools the agent may call for the current message."""
    settings = session.agent_settings
    if not settings:
        return []

    text = session.incoming_text or ""
    small_talk = is_small_talk(text)

    selected: List[str] = []
    recent_tools = _recent_tool_names(session)
    last_reply = _last_assistant_text(session)

    # Knowledge: the pipeline pre-fetches reference info for substantive messages.
    # Only offer the search tool when that found nothing, so the model can retry
    # with a better query instead of us paying for a tool hop on every question.
    if settings.enable_knowledge_search and needs_knowledge_lookup(text):
        if not session.retrieved_knowledge:
            selected.extend(KNOWLEDGE_TOOLS)

    # Catalog: product intent in this message, a product/category name from the
    # tenant's catalog ("pc", "gaming laptop"), or a follow-up to a product
    # discussion ("yes send it", "I need pc pics" after asking about products).
    if settings.enable_product_search:
        catalog_followup = any(name in CATALOG_TOOLS for name in recent_tools)
        if catalog_followup or not small_talk:
            products = _tenant_products(session)
            if products:
                wants_products = catalog_followup or bool(
                    PRODUCT_INTENT_RE.search(text)
                    or MEDIA_REQUEST_RE.search(text)
                    or mentions_catalog(text, products)
                    or PRODUCT_INTENT_RE.search(_previous_user_text(session))
                    or mentions_catalog(last_reply, products)
                )
                if wants_products:
                    selected.extend(CATALOG_TOOLS)

    # Calendar: explicit scheduling intent in this message, or the customer is
    # answering our scheduling question ("yes" to "shall I book a call?").
    # Only with a real calendar connected, so we never send fake meeting links.
    if settings.enable_scheduling and calendar_connected(session.calendar_settings):
        wants_schedule = (
            bool(SCHEDULE_INTENT_RE.search(text))
            or bool(SCHEDULE_INTENT_RE.search(last_reply))
            or any(name in CALENDAR_TOOLS for name in recent_tools)
        )
        if wants_schedule:
            selected.extend(CALENDAR_TOOLS)

    return selected
