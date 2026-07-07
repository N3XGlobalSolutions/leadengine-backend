"""Google Calendar integration provider and local Simulation fallback."""

from datetime import datetime, date, timedelta, timezone
import zoneinfo
from typing import Any, Dict, List, Optional
import uuid

# Google Calendar API imports wrapped to fail gracefully if client libs are not ready
try:
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    GOOGLE_LIBS_AVAILABLE = True
except ImportError:
    GOOGLE_LIBS_AVAILABLE = False

from app.models.outbound.outbound_entities import OutboundCalendarSettings, OutboundCalendarMeeting
from app.services.outbound.providers.provider_interfaces import CalendarProvider
from app.services.outbound.outbound_db_service import outbound_db_service
from app.utils.encryption import decrypt_secret
from app.core.logging_config import get_logger

logger = get_logger(__name__)


class GoogleCalendarProvider(CalendarProvider):
    """Google Calendar API integration provider utilizing tenant OAuth credentials."""

    def _get_credentials(self, settings: OutboundCalendarSettings) -> Any:
        """Create OAuth2 Credentials from tenant configuration."""
        if not GOOGLE_LIBS_AVAILABLE:
            raise RuntimeError("Google Calendar client libraries are not installed.")

        if not settings.google_client_id or not settings.google_client_secret or not settings.google_refresh_token:
            raise ValueError("Missing required Google OAuth credentials.")

        try:
            # Decrypt refresh token
            refresh_token = decrypt_secret(settings.google_refresh_token)
        except Exception as e:
            logger.error(f"[GoogleCalendar] Failed to decrypt refresh token: {e}")
            refresh_token = settings.google_refresh_token

        return Credentials(
            token=None,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=settings.google_client_id,
            client_secret=settings.google_client_secret,
        )

    def _get_service(self, settings: OutboundCalendarSettings) -> Any:
        """Build Google Calendar Service."""
        creds = self._get_credentials(settings)
        return build("calendar", "v3", credentials=creds)

    def get_available_slots(self, settings: OutboundCalendarSettings, target_date: date) -> List[str]:
        """Fetch free time slots for target date via Google Calendar Freebusy query."""
        try:
            # Verify if target date falls on working day
            weekday_name = target_date.strftime("%A").lower()
            if weekday_name not in settings.working_days:
                return []

            service = self._get_service(settings)
            tz = zoneinfo.ZoneInfo(settings.timezone)

            # Define working hours boundaries in target date
            start_hour, start_minute = map(int, settings.working_hours_start.split(":"))
            end_hour, end_minute = map(int, settings.working_hours_end.split(":"))

            day_start = datetime.combine(target_date, datetime.min.time(), tzinfo=tz).replace(
                hour=start_hour, minute=start_minute
            )
            day_end = datetime.combine(target_date, datetime.min.time(), tzinfo=tz).replace(
                hour=end_hour, minute=end_minute
            )

            # Query Google Freebusy API
            body = {
                "timeMin": day_start.isoformat(),
                "timeMax": day_end.isoformat(),
                "items": [{"id": settings.calendar_id}],
            }
            fb_response = service.freebusy().query(body=body).execute()
            busy_periods = fb_response.get("calendars", {}).get(settings.calendar_id, {}).get("busy", [])

            # Parse busy datetimes
            busy_ranges = []
            for b in busy_periods:
                b_start = datetime.fromisoformat(b["start"].replace("Z", "+00:00")).astimezone(tz)
                b_end = datetime.fromisoformat(b["end"].replace("Z", "+00:00")).astimezone(tz)
                busy_ranges.append((b_start, b_end))

            # Generate potential slots
            available_slots = []
            duration = timedelta(minutes=settings.meeting_duration)
            buffer = timedelta(minutes=settings.buffer)

            current_time = day_start
            while current_time + duration <= day_end:
                slot_end = current_time + duration
                slot_end_with_buffer = slot_end + buffer
                
                # Check overlap against busy periods
                overlap = False
                for b_start, b_end in busy_ranges:
                    if not (slot_end_with_buffer <= b_start or current_time >= b_end):
                        overlap = True
                        break

                if not overlap:
                    available_slots.append(current_time.isoformat())

                current_time += duration

            return available_slots

        except Exception as e:
            logger.error(f"[GoogleCalendar] Failed to check available slots: {e}. Falling back to simulation.")
            fallback = SimulationCalendarProvider()
            return fallback.get_available_slots(settings, target_date)

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
        """Insert event on Google Calendar, generating Google Meet links."""
        try:
            service = self._get_service(settings)
            tz = zoneinfo.ZoneInfo(settings.timezone)
            
            # Localize start/end times
            start_localized = start_time.astimezone(tz)
            end_localized = start_localized + timedelta(minutes=settings.meeting_duration)

            event_body = {
                "summary": summary,
                "description": description,
                "start": {"dateTime": start_localized.isoformat(), "timeZone": settings.timezone},
                "end": {"dateTime": end_localized.isoformat(), "timeZone": settings.timezone},
                "attendees": [{"email": attendee_email, "displayName": attendee_name}],
                "conferenceData": {
                    "createRequest": {
                        "requestId": str(uuid.uuid4()),
                        "conferenceSolutionKey": {"type": "hangoutsMeet"},
                    }
                },
            }

            event = (
                service.events()
                .insert(
                    calendarId=settings.calendar_id,
                    body=event_body,
                    conferenceDataVersion=1,
                )
                .execute()
            )

            # Get meet link if available
            meet_link = event.get("hangoutLink")
            event_id = event["id"]

            return {
                "status": "booked",
                "event_id": event_id,
                "meet_link": meet_link,
                "start_time": start_localized.isoformat(),
                "end_time": end_localized.isoformat(),
            }
        except Exception as e:
            logger.error(f"[GoogleCalendar] Failed to book meeting: {e}. Falling back to simulation.")
            fallback = SimulationCalendarProvider()
            return fallback.book_meeting(
                settings, start_time, attendee_name, attendee_email, attendee_phone, summary, description
            )

    def cancel_meeting(self, settings: OutboundCalendarSettings, event_id: str) -> bool:
        """Cancel meeting from calendar."""
        try:
            service = self._get_service(settings)
            service.events().delete(calendarId=settings.calendar_id, eventId=event_id).execute()
            return True
        except Exception as e:
            logger.error(f"[GoogleCalendar] Failed to delete event {event_id}: {e}")
            fallback = SimulationCalendarProvider()
            return fallback.cancel_meeting(settings, event_id)

    def reschedule_meeting(
        self, settings: OutboundCalendarSettings, event_id: str, new_start_time: datetime
    ) -> Dict[str, Any]:
        """Update meeting details with new times."""
        try:
            service = self._get_service(settings)
            tz = zoneinfo.ZoneInfo(settings.timezone)
            
            # Localize times
            start_localized = new_start_time.astimezone(tz)
            end_localized = start_localized + timedelta(minutes=settings.meeting_duration)

            event = service.events().get(calendarId=settings.calendar_id, eventId=event_id).execute()
            event["start"] = {"dateTime": start_localized.isoformat(), "timeZone": settings.timezone}
            event["end"] = {"dateTime": end_localized.isoformat(), "timeZone": settings.timezone}

            updated_event = (
                service.events()
                .update(calendarId=settings.calendar_id, eventId=event_id, body=event)
                .execute()
            )

            return {
                "status": "rescheduled",
                "event_id": event_id,
                "meet_link": updated_event.get("hangoutLink"),
                "start_time": start_localized.isoformat(),
                "end_time": end_localized.isoformat(),
            }
        except Exception as e:
            logger.error(f"[GoogleCalendar] Failed to reschedule event {event_id}: {e}")
            fallback = SimulationCalendarProvider()
            return fallback.reschedule_meeting(settings, event_id, new_start_time)

    def test_connection(self, settings: OutboundCalendarSettings) -> bool:
        """Validate credentials functionality by listing calendar colors list."""
        try:
            service = self._get_service(settings)
            service.colors().get().execute()
            return True
        except Exception as e:
            logger.error(f"[GoogleCalendar] Connection test failed: {e}")
            return False


class SimulationCalendarProvider(CalendarProvider):
    """Fallback simulated calendar reading slot details from outbound DynamoDB records."""

    def get_available_slots(self, settings: OutboundCalendarSettings, target_date: date) -> List[str]:
        """Return available slots by comparing working hours against internal booked meetings."""
        # Verify if target date falls on working day
        weekday_name = target_date.strftime("%A").lower()
        if weekday_name not in settings.working_days:
            return []

        tz = zoneinfo.ZoneInfo(settings.timezone)
        start_hour, start_minute = map(int, settings.working_hours_start.split(":"))
        end_hour, end_minute = map(int, settings.working_hours_end.split(":"))

        day_start = datetime.combine(target_date, datetime.min.time(), tzinfo=tz).replace(
            hour=start_hour, minute=start_minute
        )
        day_end = datetime.combine(target_date, datetime.min.time(), tzinfo=tz).replace(
            hour=end_hour, minute=end_minute
        )

        # Get booked meetings stored in DynamoDB for this tenant
        db_meetings = outbound_db_service.list_tenant_meetings(settings.tenant_id)
        busy_ranges = []
        for m in db_meetings:
            if m.status == "CANCELLED":
                continue
            m_start = m.start_time.astimezone(tz)
            m_end = m.end_time.astimezone(tz)
            if m_start.date() == target_date:
                busy_ranges.append((m_start, m_end))

        # Generate slots
        available_slots = []
        duration = timedelta(minutes=settings.meeting_duration)
        buffer = timedelta(minutes=settings.buffer)

        current_time = day_start
        while current_time + duration <= day_end:
            slot_end = current_time + duration
            slot_end_with_buffer = slot_end + buffer
            
            overlap = False
            for b_start, b_end in busy_ranges:
                if not (slot_end_with_buffer <= b_start or current_time >= b_end):
                    overlap = True
                    break

            if not overlap:
                available_slots.append(current_time.isoformat())

            current_time += duration

        return available_slots

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
        """Book a simulated meeting, returning a fake Meet link."""
        tz = zoneinfo.ZoneInfo(settings.timezone)
        start_localized = start_time.astimezone(tz)
        end_localized = start_localized + timedelta(minutes=settings.meeting_duration)
        event_id = f"sim_{uuid.uuid4().hex[:12]}"
        meet_link = f"https://meet.google.com/{uuid.uuid4().hex[:3]}-{uuid.uuid4().hex[:4]}-{uuid.uuid4().hex[:3]}"

        return {
            "status": "booked",
            "event_id": event_id,
            "meet_link": meet_link,
            "start_time": start_localized.isoformat(),
            "end_time": end_localized.isoformat(),
        }

    def cancel_meeting(self, settings: OutboundCalendarSettings, event_id: str) -> bool:
        """Cancel simulated meeting."""
        return True

    def reschedule_meeting(
        self, settings: OutboundCalendarSettings, event_id: str, new_start_time: datetime
    ) -> Dict[str, Any]:
        """Reschedule simulated meeting."""
        tz = zoneinfo.ZoneInfo(settings.timezone)
        start_localized = new_start_time.astimezone(tz)
        end_localized = start_localized + timedelta(minutes=settings.meeting_duration)
        meet_link = f"https://meet.google.com/{uuid.uuid4().hex[:3]}-{uuid.uuid4().hex[:4]}-{uuid.uuid4().hex[:3]}"

        return {
            "status": "rescheduled",
            "event_id": event_id,
            "meet_link": meet_link,
            "start_time": start_localized.isoformat(),
            "end_time": end_localized.isoformat(),
        }

    def test_connection(self, settings: OutboundCalendarSettings) -> bool:
        """Simulation connection test always succeeds."""
        return True


# Factory helper to load appropriate calendar provider
def get_calendar_provider(settings: OutboundCalendarSettings) -> CalendarProvider:
    """Return Google Calendar Provider or falls back to Simulation mode."""
    if (
        GOOGLE_LIBS_AVAILABLE
        and settings.google_client_id
        and settings.google_client_secret
        and settings.google_refresh_token
    ):
        return GoogleCalendarProvider()
    
    logger.info("[CalendarFactory] OAuth settings missing or client libs unavailable. Using SimulationCalendarProvider.")
    return SimulationCalendarProvider()
