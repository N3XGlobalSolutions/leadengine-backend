"""Tool definitions registered for the Outbound Sales Agent reasoning loop."""

from datetime import datetime, date
import zoneinfo
from typing import Any, Dict, List
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


# ── 1. KNOWLEDGE SEARCH TOOL ──────────────────────────────────────────

@outbound_tool_registry.register(
    name="search_company_knowledge",
    description="Search company files, Q&A records, pricing sheets, and FAQs for customer queries.",
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Specific query keyword or question regarding business facts."
            }
        },
        "required": ["query"]
    },
    category="knowledge"
)
async def search_company_knowledge(session: OutboundAISession, query: str) -> Dict[str, Any]:
    """Query Qdrant collection for grounded answers."""
    results = outbound_rag_service.search(query, session.tenant_id, limit=3)
    
    # Store retrieved knowledge blocks in session context for BuildPromptStep
    session.retrieved_knowledge.extend(results)
    
    return {
        "success": True,
        "results": [{"source": r["source"], "text": r["text"]} for r in results]
    }


# ── 2. PRODUCT CATALOG TOOLS ──────────────────────────────────────────

@outbound_tool_registry.register(
    name="list_products",
    description="List names, prices, categories, and IDs of all available products.",
    input_schema={
        "type": "object",
        "properties": {}
    },
    category="catalog"
)
async def list_products(session: OutboundAISession) -> Dict[str, Any]:
    """Retrieve catalog products list."""
    prods = outbound_product_service.list_products(session.tenant_id)
    session.selected_products.extend(prods)
    
    return {
        "success": True,
        "products": [
            {
                "product_id": p.product_id,
                "name": p.name,
                "short_description": p.short_description,
                "price": float(p.price),
                "currency": p.currency,
                "category": p.category
            }
            for p in prods
        ]
    }


@outbound_tool_registry.register(
    name="search_products",
    description="Filter catalog products by keyword, category, or price limits.",
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
    )
    session.selected_products.extend(prods)
    
    return {
        "success": True,
        "results": [
            {
                "product_id": p.product_id,
                "name": p.name,
                "price": float(p.price),
                "currency": p.currency,
                "category": p.category,
                "short_description": p.short_description
            }
            for p in prods
        ]
    }


@outbound_tool_registry.register(
    name="get_product_details",
    description="Get complete details, features, specifications, and brochure documents of a single product.",
    input_schema={
        "type": "object",
        "properties": {
            "product_id": {"type": "string", "description": "Target product ID."}
        },
        "required": ["product_id"]
    },
    category="catalog"
)
async def get_product_details(session: OutboundAISession, product_id: str) -> Dict[str, Any]:
    """Load specific product specifications."""
    product = outbound_product_service.get_product(product_id)
    if not product or product.tenant_id != session.tenant_id:
        return {"success": False, "error": "Product not found."}
        
    session.selected_products.append(product)
    
    # Track product interest in Memory
    if product.name not in session.memory.interested_products:
        session.memory.interested_products.append(product.name)

    return {
        "success": True,
        "product": {
            "product_id": product.product_id,
            "name": product.name,
            "description": product.description,
            "price": float(product.price),
            "currency": product.currency,
            "features": product.features,
            "benefits": product.benefits,
            "specifications": product.specifications,
            "category": product.category,
            "brochure_pdf": product.brochure_pdf
        }
    }


@outbound_tool_registry.register(
    name="send_product_images",
    description="Simulate dispatching gallery image URLs of the product to the customer.",
    input_schema={
        "type": "object",
        "properties": {
            "product_id": {"type": "string", "description": "Target product ID."}
        },
        "required": ["product_id"]
    },
    category="catalog"
)
async def send_product_images(session: OutboundAISession, product_id: str) -> Dict[str, Any]:
    """Return image list for AI display."""
    product = outbound_product_service.get_product(product_id)
    if not product or product.tenant_id != session.tenant_id:
        return {"success": False, "error": "Product not found."}
        
    images = product.images + product.gallery
    return {
        "success": True,
        "product_name": product.name,
        "images": images if images else ["https://via.placeholder.com/600x400.png?text=Product+Image"]
    }


@outbound_tool_registry.register(
    name="send_brochure",
    description="Simulate sharing the product brochure PDF link with the customer.",
    input_schema={
        "type": "object",
        "properties": {
            "product_id": {"type": "string", "description": "Target product ID."}
        },
        "required": ["product_id"]
    },
    category="catalog"
)
async def send_brochure(session: OutboundAISession, product_id: str) -> Dict[str, Any]:
    """Retrieve product brochure link."""
    product = outbound_product_service.get_product(product_id)
    if not product or product.tenant_id != session.tenant_id:
        return {"success": False, "error": "Product not found."}

    brochure = product.brochure_pdf or "https://via.placeholder.com/brochure.pdf"
    return {
        "success": True,
        "product_name": product.name,
        "brochure_pdf": brochure
    }


# ── 3. GOOGLE CALENDAR / SCHEDULING TOOLS ─────────────────────────────

@outbound_tool_registry.register(
    name="check_calendar",
    description="Fetch available booking slots for a target date (format: YYYY-MM-DD).",
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
    description="Book a slot on Google Calendar. Triggers Meet URL creation.",
    input_schema={
        "type": "object",
        "properties": {
            "start_time_iso": {"type": "string", "description": "ISO start datetime string (e.g. 2026-06-30T10:00:00+05:30)."},
            "attendee_email": {"type": "string", "description": "Prospect attendee email address."}
        },
        "required": ["start_time_iso", "attendee_email"]
    },
    category="calendar"
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
    lead_name = getattr(session.conversation, "name", "") or "Prospect Client"
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
        session.memory.meeting_status = "BOOKED"
        session.stage = ConversationStage.MEETING_BOOKED
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
    description="Cancel a scheduled meeting using its internal meeting ID.",
    input_schema={
        "type": "object",
        "properties": {
            "meeting_id": {"type": "string", "description": "Target meeting ID."}
        },
        "required": ["meeting_id"]
    },
    category="calendar"
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
        session.memory.meeting_status = "CANCELLED"
        session.stage = ConversationStage.FOLLOW_UP
        session.memory.conversation_stage = ConversationStage.FOLLOW_UP
        
        return {"success": True, "message": "Meeting cancelled successfully."}
        
    return {"success": False, "error": "Cancellation failed."}


@outbound_tool_registry.register(
    name="reschedule_meeting",
    description="Reschedule an existing meeting to a new date and time.",
    input_schema={
        "type": "object",
        "properties": {
            "meeting_id": {"type": "string", "description": "Target meeting ID."},
            "new_start_time_iso": {"type": "string", "description": "ISO start datetime string (e.g. 2026-06-30T14:00:00+05:30)."}
        },
        "required": ["meeting_id", "new_start_time_iso"]
    },
    category="calendar"
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
        session.memory.meeting_status = "RESCHEDULED"
        session.stage = ConversationStage.MEETING_BOOKED
        session.memory.conversation_stage = ConversationStage.MEETING_BOOKED
        
        return {
            "success": True,
            "meeting_id": meeting_id,
            "start_time": result["start_time"],
            "meet_link": result.get("meet_link")
        }

    return {"success": False, "error": "Rescheduling failed."}
