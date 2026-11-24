"""Models for inbound (website chat) call booking."""

from typing import List, Optional

from pydantic import BaseModel, Field

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


class InboundBookingSettings(BaseModel):
    """A client's bookable call slots for website-chat visitors.

    Set on the inbound bot's Settings tab. Independent of the outbound/WhatsApp calendar.
    Range checks live in inbound_booking_service.validate_settings so the API can return
    readable 400 messages.
    """

    tenant_id: Optional[str] = None
    enabled: bool = True                      # False: the chat skips the calendar; the team calls the visitor
    timezone: str = "Asia/Kolkata"            # IANA timezone the working hours are in
    working_days: List[str] = Field(default_factory=lambda: WEEKDAYS[:5])
    working_hours_start: str = "09:00"        # HH:MM, 24h
    working_hours_end: str = "18:00"          # HH:MM, 24h
    meeting_duration: int = 30                # minutes per call (slots start every N minutes)
    buffer: int = 0                           # minutes kept free before and after each booked call
    booking_days_ahead: int = 14              # how many days ahead visitors can book
