"""Interfaces and abstractions for external system integrations."""

from abc import ABC, abstractmethod
from datetime import datetime, date
from typing import Any, Dict, List, Optional
from app.models.outbound.outbound_entities import OutboundCalendarSettings


class CalendarProvider(ABC):
    """Abstract interface for meeting booking and scheduling calendars."""

    @abstractmethod
    def get_available_slots(self, settings: OutboundCalendarSettings, target_date: date) -> List[str]:
        """Fetch available slots matching tenant timezone, working hours, and busy blocks.

        Returns list of ISO strings representing available slot start times.
        """
        pass

    @abstractmethod
    def book_meeting(
        self,
        settings: OutboundCalendarSettings,
        start_time: datetime,
        attendee_name: str,
        attendee_email: str,
        attendee_phone: str,
        summary: str,
        description: str,
    ) -> Dict[str, Any]:
        """Book a meeting on Google Calendar and return details, event ID, and Meet link."""
        pass

    @abstractmethod
    def cancel_meeting(self, settings: OutboundCalendarSettings, event_id: str) -> bool:
        """Cancel a booked meeting on Google Calendar."""
        pass

    @abstractmethod
    def reschedule_meeting(
        self, settings: OutboundCalendarSettings, event_id: str, new_start_time: datetime
    ) -> Dict[str, Any]:
        """Reschedule a booked meeting, updating times and Meet links."""
        pass

    @abstractmethod
    def test_connection(self, settings: OutboundCalendarSettings) -> bool:
        """Test OAuth credentials health."""
        pass
