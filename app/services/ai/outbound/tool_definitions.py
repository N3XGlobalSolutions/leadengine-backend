"""Tool definitions registered for the Outbound Sales Agent reasoning loop."""

from datetime import datetime, date
import zoneinfo
from typing import Any, Dict, List, Optional
import uuid

from app.services.ai.outbound.tool_registry import outbound_tool_registry
from app.services.ai.outbound.conversation_pipeline import OutboundAISession
from app.services.ai.outbound_rag_service import outbound_rag_service
from app.services.outbound.outbound_product_service import outbound_product_service
from app.services.outbound.outbound_db_service import outbound_db_service
from app.services.outbound.providers.google_calendar_provider import get_calendar_provider
from app.models.outbound.outbound_entities import OutboundCalendarMeeting, ConversationStage
from app.core.logging_config import get_logger

logger = get_logger(__name__)

# Tool results are sent back to the model, so keep them small.
MAX_KNOWLEDGE_RESULTS = 3
MAX_KNOWLEDGE_CHARS = 700
MAX_LIST_PRODUCTS = 10
MAX_SEARCH_PRODUCTS = 8
MAX_DESCRIPTION_CHARS = 600
MAX_IMAGES_PER_PRODUCT = 3

# Calendar writes must never be retried automatically (a retry could double-book).
NO_RETRY = {"max_attempts": 1}


def _clip(text: Optional[str], limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + " …"


def _product_summary(p) -> Dict[str, Any]:
    return {
        "product_id": p.product_id,
        "name": p.name,
        "price": float(p.price),
        "currency": p.currency,
        "category": p.category,
        "short_description": _clip(p.short_description, 200),
    }


def _remember_product(session: OutboundAISession, product) -> None:
    if session.memory is not None and product.name not in session.memory.interested_products:
        session.memory.interested_products.append(product.name)


def _resolve_product(session: OutboundAISession, ref: Optional[str]):
    """Find a product of this tenant by ID or by (fuzzy) name, so the model can use
    the names it sees in the catalog index without an extra list_products hop."""
    ref = (ref or "").strip()
    if not ref:
        return None
    product = outbound_product_service.get_product(ref)
    if product and product.tenant_id == session.tenant_id:
        return product

    catalog = session.products or outbound_product_service.list_products(session.tenant_id)
    needle = ref.lower()
    exact = [p for p in catalog if p.name.lower() == needle]
    if exact:
        return exact[0]
    contains = [p for p in catalog if needle in p.name.lower() or p.name.lower() in needle]
    if contains:
        return contains[0]
    words = set(needle.replace("-", " ").split())
    scored = sorted(
        ((len(words & set(f"{p.name} {p.category}".lower().replace("-", " ").split())), p) for p in catalog),
        key=lambda pair: pair[0], reverse=True,
    )
    return scored[0][1] if scored and scored[0][0] > 0 else None


PRODUCT_REF_SCHEMA = {
    "type": "object",
    "properties": {
        "product": {"type": "string", "description": "Product name (as shown in the catalog) or product ID."}
    },
    "required": ["product"]
}


# ── 1. KNOWLEDGE SEARCH TOOL ──────────────────────────────────────────

@outbound_tool_registry.register(
    name="search_company_knowledge",
    description=(
        "Search the company's knowledge base (uploaded documents, FAQs, policies) for facts about the "
        "company, its services, process, fees, locations or policies. Use only when the customer asks "
        "about these and the answer is not already in the conversation or reference information."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "A specific search query rephrasing the customer's question."
            }
        },
        "required": ["query"]
    },
    category="knowledge"
)
async def search_company_knowledge(session: OutboundAISession, query: str) -> Dict[str, Any]:
    """Query Qdrant collection for grounded answers."""
    results = outbound_rag_service.search(
        query, session.tenant_id, limit=MAX_KNOWLEDGE_RESULTS, usage=session.token_usage
    )

    # Keep for URL allow-listing in the response validator
    session.retrieved_knowledge.extend(results)

    if not results:
        return {
            "success": True,
            "results": [],
            "note": "No matching information in the knowledge base. Don't guess; offer to check with the team."
        }

    return {
        "success": True,
        "results": [{"source": r["source"], "text": _clip(r["text"], MAX_KNOWLEDGE_CHARS)} for r in results]
    }


# ── 2. PRODUCT CATALOG TOOLS ──────────────────────────────────────────

@outbound_tool_registry.register(
    name="list_products",
    description="List the available products with names, prices and IDs. Use when the customer asks what products or plans are offered.",
    input_schema={
        "type": "object",
        "properties": {}
    },
    category="catalog"
)
async def list_products(session: OutboundAISession) -> Dict[str, Any]:
    """Retrieve catalog products list."""
    prods = outbound_product_service.list_products(session.tenant_id)[:MAX_LIST_PRODUCTS]
    session.selected_products.extend(prods)

    return {
        "success": True,
        "products": [_product_summary(p) for p in prods]
    }


@outbound_tool_registry.register(
    name="search_products",
    description="Find products by keyword, category or price range. Use when the customer asks about a specific kind of product or a budget.",
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search text matching tags or descriptors."},
            "category": {"type": "string", "description": "Target category classification."},
            "max_price": {"type": "number", "description": "Maximum budget ceiling."},
            "min_price": {"type": "number", "description": "Minimum budget floor."}
        }
    },
    category="catalog"
)
async def search_products(
    session: OutboundAISession,
    query: Optional[str] = None,
    category: Optional[str] = None,
    max_price: Optional[float] = None,
    min_price: Optional[float] = None
) -> Dict[str, Any]:
    """Filter catalog products list."""
    prods = outbound_product_service.search_products(
        session.tenant_id, query, category, max_price, min_price
    )[:MAX_SEARCH_PRODUCTS]
    session.selected_products.extend(prods)

    return {
        "success": True,
        "results": [_product_summary(p) for p in prods]
    }


@outbound_tool_registry.register(
    name="get_product_details",
    description="Get full details (features, specifications, price) of one product. Use when the customer asks about a specific product.",
    input_schema=PRODUCT_REF_SCHEMA,
    category="catalog"
)
async def get_product_details(session: OutboundAISession, product: str = "", product_id: str = "") -> Dict[str, Any]:
    """Load specific product specifications."""
    found = _resolve_product(session, product or product_id)
    if not found:
        return {"success": False, "error": "Product not found in the catalog."}
    product = found

    session.selected_products.append(product)
    _remember_product(session, product)

    return {
        "success": True,
        "product": {
            "product_id": product.product_id,
            "name": product.name,
            "description": _clip(product.description, MAX_DESCRIPTION_CHARS),
            "price": float(product.price),
            "currency": product.currency,
            "features": product.features[:10],
            "benefits": product.benefits[:10],
            "specifications": product.specifications,
            "category": product.category,
            "brochure_pdf": product.brochure_pdf
        }
    }


@outbound_tool_registry.register(
    name="send_product_images",
    description=(
        "Send real photos of one product to the customer on WhatsApp (as image messages after your reply). "
        "Use when the customer asks to see photos, pics or images of a product."
    ),
    input_schema=PRODUCT_REF_SCHEMA,
    category="catalog"
)
async def send_product_images(session: OutboundAISession, product: str = "", product_id: str = "") -> Dict[str, Any]:
    """Queue the product's images as WhatsApp image messages."""
    found = _resolve_product(session, product or product_id)
    if not found:
        return {"success": False, "error": "Product not found in the catalog."}

    images = [url for url in (found.images + found.gallery) if url][:MAX_IMAGES_PER_PRODUCT]
    if not images:
        return {"success": False, "error": f"No photos have been added for {found.name} yet. Offer the details instead."}

    # Don't resend the same photos unless the customer explicitly asks for photos again.
    from app.services.ai.outbound.tool_router import MEDIA_REQUEST_RE
    already_sent = session.memory.preferences.setdefault("media_sent", []) if session.memory is not None else []
    if found.product_id in already_sent and not MEDIA_REQUEST_RE.search(session.incoming_text or ""):
        return {"success": False, "error": f"Photos of {found.name} were already sent earlier in this chat. Don't send them again."}
    if session.memory is not None and found.product_id not in already_sent:
        already_sent.append(found.product_id)

    session.selected_products.append(found)
    _remember_product(session, found)
    for i, url in enumerate(images):
        session.outgoing_media.append({
            "type": "image",
            "link": url,
            "caption": found.name if i == 0 else "",
        })
    return {
        "success": True,
        "product_name": found.name,
        "images_queued": len(images),
        "note": "The photos will be sent right after your message. Mention them briefly; don't paste links.",
    }


@outbound_tool_registry.register(
    name="send_brochure",
    description="Send the product's brochure PDF to the customer on WhatsApp. Use only when the customer asks for a brochure or documents.",
    input_schema=PRODUCT_REF_SCHEMA,
    category="catalog"
)
async def send_brochure(session: OutboundAISession, product: str = "", product_id: str = "") -> Dict[str, Any]:
    """Queue the product brochure as a WhatsApp document message."""
    found = _resolve_product(session, product or product_id)
    if not found:
        return {"success": False, "error": "Product not found in the catalog."}

    if not found.brochure_pdf:
        return {"success": False, "error": f"No brochure is available for {found.name}."}

    session.selected_products.append(found)
    _remember_product(session, found)
    safe_name = "".join(c if c.isalnum() or c in " -_" else "" for c in found.name).strip() or "brochure"
    session.outgoing_media.append({
        "type": "document",
        "link": found.brochure_pdf,
        "filename": f"{safe_name}.pdf",
        "caption": f"{found.name} brochure",
    })
    return {
        "success": True,
        "product_name": found.name,
        "note": "The brochure PDF will be sent right after your message. Don't paste the link.",
    }


# ── 3. GOOGLE CALENDAR / SCHEDULING TOOLS ─────────────────────────────

@outbound_tool_registry.register(
    name="check_calendar",
    description="Get available call slots for a date (YYYY-MM-DD). Use when the customer wants to book or move a call.",
    input_schema={
        "type": "object",
        "properties": {
            "date_str": {"type": "string", "description": "Target date formatted as YYYY-MM-DD."}
        },
        "required": ["date_str"]
    },
    category="calendar"
)
async def check_calendar(session: OutboundAISession, date_str: str) -> Dict[str, Any]:
    """Query provider for available times on a specific date."""
    try:
        target_date = date.fromisoformat(date_str)
    except ValueError:
        return {"success": False, "error": "Invalid date format. Use YYYY-MM-DD."}

    provider = get_calendar_provider(session.calendar_settings)
    slots = provider.get_available_slots(session.calendar_settings, target_date)
    
    return {
        "success": True,
        "date": date_str,
        "timezone": session.calendar_settings.timezone,
        "slots": slots
    }


@outbound_tool_registry.register(
    name="book_meeting",
    description="Book a call in an available slot after the customer confirmed the time and gave their email. Creates the meeting link.",
    input_schema={
        "type": "object",
        "properties": {
            "start_time_iso": {"type": "string", "description": "ISO start datetime string (e.g. 2026-06-30T10:00:00+05:30)."},
            "attendee_email": {"type": "string", "description": "Prospect attendee email address."}
        },
        "required": ["start_time_iso", "attendee_email"]
    },
    category="calendar",
    retry_policy=NO_RETRY,
)
async def book_meeting(session: OutboundAISession, start_time_iso: str, attendee_email: str) -> Dict[str, Any]:
    """Commit event via Google Calendar Provider."""
    try:
        # Normalize timezone offsets (like +05:30)
        dt = datetime.fromisoformat(start_time_iso)
    except ValueError:
        return {"success": False, "error": "Invalid datetime ISO format."}

    provider = get_calendar_provider(session.calendar_settings)
    
    # Load lead metadata details for event description
    lead_name = session.lead_name or "Prospect Client"
    summary = f"Outbound AI Discovery Call with {lead_name}"
    description = f"Sales discovery call scheduled automatically by LeadEngine Outbound AI Agent.\nProspect email: {attendee_email}"
    
    result = provider.book_meeting(
        settings=session.calendar_settings,
        start_time=dt,
        attendee_name=lead_name,
        attendee_email=attendee_email,
        attendee_phone=session.sender_phone,
        summary=summary,
        description=description
    )

    if result.get("status") == "booked":
        # Create internal booking record
        meeting_id = str(uuid.uuid4())
        meeting = OutboundCalendarMeeting(
            meeting_id=meeting_id,
            tenant_id=session.tenant_id,
            lead_id=session.lead_id,
            conversation_id=session.conversation_id,
            event_id=result["event_id"],
            summary=summary,
            description=description,
            start_time=datetime.fromisoformat(result["start_time"]),
            end_time=datetime.fromisoformat(result["end_time"]),
            timezone=session.calendar_settings.timezone,
            attendee_name=lead_name,
            attendee_email=attendee_email,
            attendee_phone=session.sender_phone,
            meet_link=result.get("meet_link"),
            status="BOOKED"
        )
        outbound_db_service.save_meeting(meeting)

        # Update memory state variables
        session.stage = ConversationStage.MEETING_BOOKED
        if session.memory is not None:
            session.memory.meeting_status = "BOOKED"
            session.memory.conversation_stage = ConversationStage.MEETING_BOOKED

        return {
            "success": True,
            "meeting_id": meeting_id,
            "start_time": result["start_time"],
            "meet_link": result.get("meet_link")
        }

    return {"success": False, "error": "Booking request failed."}


@outbound_tool_registry.register(
    name="cancel_meeting",
    description="Cancel a booked call by its meeting ID. Use only when the customer asks to cancel.",
    input_schema={
        "type": "object",
        "properties": {
            "meeting_id": {"type": "string", "description": "Target meeting ID."}
        },
        "required": ["meeting_id"]
    },
    category="calendar",
    retry_policy=NO_RETRY,
)
async def cancel_meeting(session: OutboundAISession, meeting_id: str) -> Dict[str, Any]:
    """Delete event details via provider."""
    meeting = outbound_db_service.get_meeting(meeting_id)
    if not meeting or meeting.tenant_id != session.tenant_id:
        return {"success": False, "error": "Meeting record not found."}

    provider = get_calendar_provider(session.calendar_settings)
    cancelled = provider.cancel_meeting(session.calendar_settings, meeting.event_id)
    
    if cancelled:
        meeting.status = "CANCELLED"
        outbound_db_service.save_meeting(meeting)

        # Update memory
        session.stage = ConversationStage.FOLLOW_UP
        if session.memory is not None:
            session.memory.meeting_status = "CANCELLED"
            session.memory.conversation_stage = ConversationStage.FOLLOW_UP

        return {"success": True, "message": "Meeting cancelled successfully."}
        
    return {"success": False, "error": "Cancellation failed."}


@outbound_tool_registry.register(
    name="reschedule_meeting",
    description="Move a booked call to a new available time. Use only when the customer asks to reschedule.",
    input_schema={
        "type": "object",
        "properties": {
            "meeting_id": {"type": "string", "description": "Target meeting ID."},
            "new_start_time_iso": {"type": "string", "description": "ISO start datetime string (e.g. 2026-06-30T14:00:00+05:30)."}
        },
        "required": ["meeting_id", "new_start_time_iso"]
    },
    category="calendar",
    retry_policy=NO_RETRY,
)
async def reschedule_meeting(session: OutboundAISession, meeting_id: str, new_start_time_iso: str) -> Dict[str, Any]:
    """Reschedule event via provider."""
    meeting = outbound_db_service.get_meeting(meeting_id)
    if not meeting or meeting.tenant_id != session.tenant_id:
        return {"success": False, "error": "Meeting record not found."}

    try:
        dt = datetime.fromisoformat(new_start_time_iso)
    except ValueError:
        return {"success": False, "error": "Invalid ISO datetime format."}

    provider = get_calendar_provider(session.calendar_settings)
    result = provider.reschedule_meeting(session.calendar_settings, meeting.event_id, dt)
    
    if result.get("status") == "rescheduled":
        meeting.start_time = datetime.fromisoformat(result["start_time"])
        meeting.end_time = datetime.fromisoformat(result["end_time"])
        meeting.meet_link = result.get("meet_link")
        meeting.status = "RESCHEDULED"
        outbound_db_service.save_meeting(meeting)

        # Update memory
        session.stage = ConversationStage.MEETING_BOOKED
        if session.memory is not None:
            session.memory.meeting_status = "RESCHEDULED"
            session.memory.conversation_stage = ConversationStage.MEETING_BOOKED

        return {
            "success": True,
            "meeting_id": meeting_id,
            "start_time": result["start_time"],
            "meet_link": result.get("meet_link")
        }

    return {"success": False, "error": "Rescheduling failed."}
