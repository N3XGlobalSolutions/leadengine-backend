"""Inbound (website chat) call booking routes: visitor booking, client settings and bookings list."""

from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel

from app.core.logging_config import get_logger
from app.models.user import UserResponse
from app.services import tenant_service
from app.services.booking import inbound_booking_service
from app.services.booking.inbound_booking_service import BookingError
from app.utils.dependencies import get_current_user, validate_tenant_access

logger = get_logger(__name__)

# Public (chat widget) endpoints - same tenant resolution as POST /api/chat
router = APIRouter(prefix="/api/chat/booking", tags=["Chat Booking"])

# Authenticated (dashboard) endpoints
tenant_router = APIRouter(prefix="/api/tenants", tags=["Chat Booking"])


class BookSlotRequest(BaseModel):
    conversation_id: str
    start_time: str  # ISO 8601 with offset, exactly as returned by /slots
    tenant_id: Optional[str] = None


def _resolve_tenant(tenant_id: Optional[str], api_key: Optional[str]) -> str:
    """Resolve the tenant from the widget API key if sent, otherwise from tenant_id."""
    if api_key:
        tenant_from_key = tenant_service.validate_api_key(api_key)
        if not tenant_from_key:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")
        return tenant_from_key
    if not tenant_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="tenant_id is required")
    if not tenant_service.get_tenant(tenant_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
    return tenant_id


def _run(action: str, fn, *args):
    """Run a booking service call, mapping errors to HTTP responses."""
    try:
        return fn(*args)
    except BookingError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Booking] {action} failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Something went wrong with booking. Please try again.",
        )


# Plain `def` endpoints: FastAPI runs them in a worker thread, so the blocking
# DynamoDB / Google Calendar calls don't stall the event loop.

@router.get("/availability")
def get_availability(
    tenant_id: Optional[str] = Query(None),
    api_key: Optional[str] = Header(None, alias="X-API-Key"),
):
    """Dates a visitor can book (working days within the tenant's booking window)."""
    resolved = _resolve_tenant(tenant_id, api_key)
    return _run("availability", inbound_booking_service.get_availability, resolved)


@router.get("/slots")
def get_slots(
    date: str = Query(..., description="YYYY-MM-DD in the tenant's timezone"),
    tenant_id: Optional[str] = Query(None),
    api_key: Optional[str] = Header(None, alias="X-API-Key"),
):
    """Free time slots for one date."""
    resolved = _resolve_tenant(tenant_id, api_key)
    return _run("slots", inbound_booking_service.get_slots, resolved, date)


@router.post("/book")
def book_slot(
    request: BookSlotRequest,
    api_key: Optional[str] = Header(None, alias="X-API-Key"),
):
    """Book a slot for the visitor of a finished chat."""
    resolved = _resolve_tenant(request.tenant_id, api_key)
    return _run("book", inbound_booking_service.book_slot, resolved, request.conversation_id, request.start_time)


@tenant_router.get("/{tenant_id}/bookings")
async def list_bookings(
    tenant_id: str,
    upcoming_only: bool = Query(True),
    current_user: UserResponse = Depends(get_current_user),
):
    """Calls booked by website-chat visitors. Upcoming: soonest first; all: oldest first."""
    await validate_tenant_access(tenant_id, current_user)
    return _run("list bookings", inbound_booking_service.list_bookings, tenant_id, upcoming_only)


@tenant_router.get("/{tenant_id}/booking-settings")
async def get_booking_settings(
    tenant_id: str,
    current_user: UserResponse = Depends(get_current_user),
):
    """The client's bookable call slots for website-chat visitors (defaults if never saved)."""
    await validate_tenant_access(tenant_id, current_user)
    return _run("get booking settings", inbound_booking_service.get_settings, tenant_id)


@tenant_router.put("/{tenant_id}/booking-settings")
async def update_booking_settings(
    tenant_id: str,
    data: Dict[str, Any] = Body(...),
    current_user: UserResponse = Depends(get_current_user),
):
    """Save the client's booking settings. Invalid values return 400 with a readable message."""
    await validate_tenant_access(tenant_id, current_user)
    return _run("update booking settings", inbound_booking_service.update_settings, tenant_id, data)
