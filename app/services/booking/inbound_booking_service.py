"""Call booking for website chat (inbound bot) visitors.

After a visitor finishes the qualification chat and clicks "Yes, schedule a call", the chat
widget shows a date + time-slot picker. Slots come from the client's inbound booking settings
(set on the inbound bot's Settings tab): working days and hours, call length, gap between calls,
timezone and booking window. Already-booked calls are removed from the free slots.

This is fully separate from the outbound/WhatsApp calendar. DynamoDB items (single table):
  INBOUND_BOOKING_SETTINGS#<tenant> / CONFIG          - settings
  INBOUND_BOOKING#<booking_id>      / METADATA        - one booked call
      GSI1PK = TENANT#<tenant>#INBOUND_BOOKINGS, GSI1SK = BOOKING#<start UTC ISO>#<booking_id>
  INBOUND_BOOKING_SLOT#<tenant>     / <start UTC ISO> - lock so a slot can't be booked twice
"""

import re
import uuid
import zoneinfo
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from botocore.exceptions import ClientError
from pydantic import ValidationError

from app.core.dynamodb import get_dynamodb_table
from app.core.logging_config import get_logger
from app.models.booking import WEEKDAYS, InboundBookingSettings

logger = get_logger(__name__)

SOURCE = "website_chat"
STATUS_BOOKED = "BOOKED"
_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class BookingError(Exception):
    """A booking problem to show to the visitor/admin, with an HTTP status code."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


# ── Settings ──────────────────────────────────────────────────────────

def _settings_key(tenant_id: str) -> Dict[str, str]:
    return {"PK": f"INBOUND_BOOKING_SETTINGS#{tenant_id}", "SK": "CONFIG"}


def _minutes(hhmm: str) -> int:
    hours, minutes = map(int, hhmm.split(":"))
    return hours * 60 + minutes


def validate_settings(s: InboundBookingSettings) -> Optional[str]:
    """Return a readable error message, or None if the settings are valid."""
    try:
        zoneinfo.ZoneInfo(s.timezone)
    except Exception:
        return f"Unknown timezone '{s.timezone}'."
    if not s.working_days:
        return "Pick at least one working day."
    if any(d not in WEEKDAYS for d in s.working_days):
        return "Working days must be weekday names like 'monday'."
    if not _HHMM.match(s.working_hours_start) or not _HHMM.match(s.working_hours_end):
        return "Working hours must be in HH:MM format (24-hour)."
    if _minutes(s.working_hours_end) <= _minutes(s.working_hours_start):
        return "Working hours end must be after start."
    if not 5 <= s.meeting_duration <= 480:
        return "Call length must be between 5 and 480 minutes."
    if s.meeting_duration > _minutes(s.working_hours_end) - _minutes(s.working_hours_start):
        return "Call length is longer than the working hours - no slot would fit."
    if not 0 <= s.buffer <= 240:
        return "Gap between calls must be between 0 and 240 minutes."
    if not 1 <= s.booking_days_ahead <= 90:
        return "Booking window must be between 1 and 90 days."
    return None


def get_settings(tenant_id: str) -> InboundBookingSettings:
    """The tenant's settings, or the defaults if never saved (or if stored values are invalid)."""
    item = get_dynamodb_table().get_item(Key=_settings_key(tenant_id)).get("Item")
    if not item:
        return InboundBookingSettings(tenant_id=tenant_id)
    try:
        settings = InboundBookingSettings(
            tenant_id=tenant_id,
            enabled=bool(item.get("enabled", True)),
            timezone=item.get("timezone", "Asia/Kolkata"),
            working_days=list(item.get("working_days") or WEEKDAYS[:5]),
            working_hours_start=item.get("working_hours_start", "09:00"),
            working_hours_end=item.get("working_hours_end", "18:00"),
            meeting_duration=int(item.get("meeting_duration", 30)),
            buffer=int(item.get("buffer", 0)),
            booking_days_ahead=int(item.get("booking_days_ahead", 14)),
        )
    except (ValidationError, TypeError, ValueError) as e:
        logger.error(f"[Booking] Stored settings for tenant {tenant_id} are unreadable, using defaults: {e}")
        return InboundBookingSettings(tenant_id=tenant_id)
    if validate_settings(settings):
        logger.error(f"[Booking] Stored settings for tenant {tenant_id} are invalid, using defaults")
        return InboundBookingSettings(tenant_id=tenant_id)
    return settings


def update_settings(tenant_id: str, data: Dict[str, Any]) -> InboundBookingSettings:
    """Merge `data` onto the current settings, validate, and save."""
    current = get_settings(tenant_id).model_dump()
    current.update({k: v for k, v in data.items() if k in current and k != "tenant_id"})
    try:
        settings = InboundBookingSettings(**current)
    except ValidationError as e:
        field = ".".join(str(p) for p in e.errors()[0].get("loc", [])) or "a field"
        raise BookingError(f"Invalid value for {field}.")
    settings.tenant_id = tenant_id
    # Normalise days: lowercase, no duplicates, Monday-first order
    days = {str(d).strip().lower() for d in settings.working_days}
    settings.working_days = [d for d in WEEKDAYS if d in days] + sorted(days - set(WEEKDAYS))

    error = validate_settings(settings)
    if error:
        raise BookingError(error)

    get_dynamodb_table().put_item(Item={
        **_settings_key(tenant_id),
        "tenant_id": tenant_id,
        "enabled": settings.enabled,
        "timezone": settings.timezone,
        "working_days": settings.working_days,
        "working_hours_start": settings.working_hours_start,
        "working_hours_end": settings.working_hours_end,
        "meeting_duration": settings.meeting_duration,
        "buffer": settings.buffer,
        "booking_days_ahead": settings.booking_days_ahead,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })
    return settings


# ── Bookings storage ──────────────────────────────────────────────────

def _utc_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _query_bookings(tenant_id: str, start: Optional[datetime] = None, end: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Booking items for a tenant, oldest first, optionally limited to start times in [start, end]."""
    table = get_dynamodb_table()
    lower = f"BOOKING#{_utc_iso(start)}" if start else "BOOKING#"
    upper = f"BOOKING#{_utc_iso(end)}~" if end else "BOOKING#~"
    kwargs: Dict[str, Any] = {
        "IndexName": "GSI1",
        "KeyConditionExpression": "GSI1PK = :pk AND GSI1SK BETWEEN :lo AND :hi",
        "ExpressionAttributeValues": {":pk": f"TENANT#{tenant_id}#INBOUND_BOOKINGS", ":lo": lower, ":hi": upper},
    }
    items: List[Dict[str, Any]] = []
    while True:
        res = table.query(**kwargs)
        items.extend(res.get("Items", []))
        if "LastEvaluatedKey" not in res:
            return items
        kwargs["ExclusiveStartKey"] = res["LastEvaluatedKey"]


def _claim_slot(tenant_id: str, start: datetime, booking_id: str) -> bool:
    """Atomically reserve a start time; False if another booking already holds it."""
    try:
        get_dynamodb_table().put_item(
            Item={"PK": f"INBOUND_BOOKING_SLOT#{tenant_id}", "SK": _utc_iso(start), "booking_id": booking_id},
            ConditionExpression="attribute_not_exists(PK)",
        )
        return True
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            return False
        raise


def _release_slot(tenant_id: str, start: datetime) -> None:
    get_dynamodb_table().delete_item(Key={"PK": f"INBOUND_BOOKING_SLOT#{tenant_id}", "SK": _utc_iso(start)})


# ── Slots ─────────────────────────────────────────────────────────────

def _tz(settings: InboundBookingSettings) -> zoneinfo.ZoneInfo:
    return zoneinfo.ZoneInfo(settings.timezone)


def _day_window(settings: InboundBookingSettings, d: date) -> Tuple[datetime, datetime]:
    tz = _tz(settings)
    sh, sm = map(int, settings.working_hours_start.split(":"))
    eh, em = map(int, settings.working_hours_end.split(":"))
    return datetime.combine(d, time(sh, sm), tzinfo=tz), datetime.combine(d, time(eh, em), tzinfo=tz)


def _free_slots(tenant_id: str, settings: InboundBookingSettings, d: date) -> List[str]:
    """Free slot start times on a date (ISO with offset), excluding past times and booked calls."""
    if WEEKDAYS[d.weekday()] not in settings.working_days:
        return []
    day_start, day_end = _day_window(settings, d)
    duration = timedelta(minutes=settings.meeting_duration)
    gap = timedelta(minutes=settings.buffer)

    busy = []
    for item in _query_bookings(tenant_id, day_start - timedelta(days=1), day_end + timedelta(days=1)):
        if item.get("status") == STATUS_BOOKED:
            busy.append((datetime.fromisoformat(item["start_time"]), datetime.fromisoformat(item["end_time"])))

    now = datetime.now(timezone.utc)
    slots = []
    slot = day_start
    while slot + duration <= day_end:
        slot_end = slot + duration
        clashes = any(slot < b_end + gap and slot_end > b_start - gap for b_start, b_end in busy)
        if slot > now and not clashes:
            slots.append(slot.isoformat())
        slot += duration
    return slots


def get_availability(tenant_id: str) -> Dict[str, Any]:
    """Bookable dates: working days within the booking window that still have room for a call."""
    settings = get_settings(tenant_id)
    result = {
        "enabled": settings.enabled,
        "timezone": settings.timezone,
        "meeting_duration": settings.meeting_duration,
        "days_ahead": settings.booking_days_ahead,
        "dates": [],
    }
    if not settings.enabled:
        return result

    now = datetime.now(_tz(settings))
    duration = timedelta(minutes=settings.meeting_duration)
    for offset in range(settings.booking_days_ahead):
        d = now.date() + timedelta(days=offset)
        if WEEKDAYS[d.weekday()] not in settings.working_days:
            continue
        if offset == 0 and now + duration > _day_window(settings, d)[1]:
            continue  # today is already over
        result["dates"].append(d.isoformat())
    return result


def get_slots(tenant_id: str, date_str: str) -> Dict[str, Any]:
    """Free time slots for one date in the booking window."""
    settings = get_settings(tenant_id)
    if not settings.enabled:
        raise BookingError("Online booking is turned off.")
    try:
        d = date.fromisoformat(date_str)
    except ValueError:
        raise BookingError("Invalid date. Use YYYY-MM-DD.")
    today = datetime.now(_tz(settings)).date()
    if d < today or d >= today + timedelta(days=settings.booking_days_ahead):
        raise BookingError("This date is outside the booking window.")
    return {
        "date": d.isoformat(),
        "timezone": settings.timezone,
        "meeting_duration": settings.meeting_duration,
        "slots": _free_slots(tenant_id, settings, d),
    }


# ── Booking ───────────────────────────────────────────────────────────

def _format_when(start: datetime, tz: zoneinfo.ZoneInfo) -> str:
    """e.g. 'Tuesday, 25 November 2026 at 10:30 AM' (no platform-specific strftime flags)."""
    local = start.astimezone(tz)
    clock = local.strftime("%I:%M %p").lstrip("0")
    return f"{local:%A}, {local.day} {local:%B %Y} at {clock}"


def book_slot(tenant_id: str, conversation_id: str, start_time_str: str) -> Dict[str, Any]:
    """Book a call for the visitor of a finished chat, and record it on their lead."""
    # Imported here to avoid circular imports at module load
    from app.services import conversation_service, lead_service

    settings = get_settings(tenant_id)
    if not settings.enabled:
        raise BookingError("Online booking is turned off.")

    conversation = conversation_service.get_conversation(conversation_id)
    if not conversation or conversation.tenant_id != tenant_id:
        raise BookingError("Chat not found.", 404)

    lead = lead_service.get_conversation_lead(conversation_id)
    if not lead:
        raise BookingError("Please finish the chat before booking a call.")
    existing = (lead.metadata or {}).get("call_booking")
    if existing and existing.get("status") == STATUS_BOOKED:
        raise BookingError("A call is already booked for this chat.", 409)

    try:
        start = datetime.fromisoformat(start_time_str)
    except ValueError:
        raise BookingError("Invalid start time.")
    if start.tzinfo is None:
        raise BookingError("Start time must include a timezone offset.")

    tz = _tz(settings)
    taken_msg = "Sorry, that time was just taken. Please pick another slot."
    # Re-check against live availability so a stale picker can't book a taken or past slot
    free = _free_slots(tenant_id, settings, start.astimezone(tz).date())
    if not any(datetime.fromisoformat(s) == start for s in free):
        raise BookingError(taken_msg, 409)

    booking_id = str(uuid.uuid4())
    if not _claim_slot(tenant_id, start, booking_id):
        raise BookingError(taken_msg, 409)

    start_local = start.astimezone(tz)
    end_local = start_local + timedelta(minutes=settings.meeting_duration)
    name = lead.name or "Website visitor"
    phone = lead.phone or ""
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        get_dynamodb_table().put_item(Item={
            "PK": f"INBOUND_BOOKING#{booking_id}",
            "SK": "METADATA",
            "GSI1PK": f"TENANT#{tenant_id}#INBOUND_BOOKINGS",
            "GSI1SK": f"BOOKING#{_utc_iso(start)}#{booking_id}",
            "booking_id": booking_id,
            "tenant_id": tenant_id,
            "lead_id": lead.lead_id,
            "conversation_id": conversation_id,
            "start_time": start_local.isoformat(),
            "end_time": end_local.isoformat(),
            "timezone": settings.timezone,
            "attendee_name": name,
            "attendee_phone": phone,
            "status": STATUS_BOOKED,
            "source": SOURCE,
            "created_at": now_iso,
        })
    except Exception:
        _release_slot(tenant_id, start)
        raise

    booking = {
        "meeting_id": booking_id,
        "start_time": start_local.isoformat(),
        "end_time": end_local.isoformat(),
        "timezone": settings.timezone,
        "meet_link": None,
        "status": STATUS_BOOKED,
        "source": SOURCE,
        "booked_at": now_iso,
    }
    when = _format_when(start_local, tz)
    message = f"Your call is booked for {when} ({settings.timezone}). Our team will call you then."

    # The call is booked at this point; failures below are logged, not shown to the visitor
    try:
        metadata = dict(lead.metadata or {})
        metadata["call_requested"] = True
        metadata["call_booking"] = booking
        lead_service.update_lead_details(lead.lead_id, {"metadata": metadata})
    except Exception as e:
        logger.error(f"[Booking] Booking {booking_id} saved but lead {lead.lead_id} update failed: {e}")
    try:
        conversation_service.add_message(conversation_id, role="user", content=f"Book a call on {when}")
        conversation_service.add_message(conversation_id, role="assistant", content=message)
    except Exception as e:
        logger.error(f"[Booking] Booking {booking_id} saved but chat history update failed: {e}")

    logger.info(f"[Booking] Lead {lead.lead_id} booked a call at {start_local.isoformat()} (tenant {tenant_id})")
    return {"booking": booking, "message": message}


def list_bookings(tenant_id: str, upcoming_only: bool = True) -> List[Dict[str, Any]]:
    """Booked calls for a tenant. Upcoming: soonest first. All: oldest first, past included."""
    now = datetime.now(timezone.utc)
    items = _query_bookings(tenant_id, start=now - timedelta(days=1) if upcoming_only else None)
    result = []
    for item in items:
        if item.get("status") != STATUS_BOOKED:
            continue
        if upcoming_only and datetime.fromisoformat(item["end_time"]) < now:
            continue
        result.append({
            "meeting_id": item["booking_id"],
            "lead_id": item.get("lead_id"),
            "conversation_id": item.get("conversation_id"),
            "start_time": item["start_time"],
            "end_time": item["end_time"],
            "timezone": item.get("timezone"),
            "attendee_name": item.get("attendee_name"),
            "attendee_phone": item.get("attendee_phone"),
            "meet_link": None,
            "status": item.get("status"),
            "source": item.get("source", SOURCE),
        })
    return result
