# google_calendar.py — Google Calendar Integration for Hookfish Voice Agent
# =========================================================================
# Handles:
#   - Service Account authentication with Google Calendar API
#   - Creating calendar events (site visits, meetings)
#   - Checking availability for a given time slot
#   - Sending calendar invites to attendees
#
# Setup:
#   1. Create a Google Cloud project → Enable "Google Calendar API"
#   2. Create a Service Account → Download JSON key file
#   3. Share your Google Calendar with the service account email
#      (the email looks like: xxx@project-id.iam.gserviceaccount.com)
#   4. Set env vars: GOOGLE_CALENDAR_CREDENTIALS_FILE, GOOGLE_CALENDAR_ID
# =========================================================================

import os
import logging
import json
from datetime import datetime, timedelta
from dateutil import parser as dateutil_parser
from dateutil.relativedelta import relativedelta
import pytz

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("voice-agent-calendar")

# ============================================================
# Configuration
# ============================================================

# Path to the service account credentials JSON file
CREDENTIALS_FILE = os.getenv(
    "GOOGLE_CALENDAR_CREDENTIALS_FILE",
    os.path.join(os.path.dirname(__file__), "google_credentials.json"),
)

# The calendar ID to use (default: primary calendar of the service account)
# For a shared calendar, this would be the calendar's email address
CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID", "primary")

# Timezone for events
TIMEZONE = os.getenv("GOOGLE_CALENDAR_TIMEZONE", "Asia/Kolkata")

# Default meeting duration in minutes
DEFAULT_MEETING_DURATION = int(os.getenv("GOOGLE_CALENDAR_DEFAULT_DURATION", "60"))

# Scopes required for calendar access
SCOPES = ["https://www.googleapis.com/auth/calendar"]


# ============================================================
# Authentication
# ============================================================

_service = None  # Cached service instance


def get_calendar_service():
    """
    Authenticate using a Service Account and return the Google Calendar API service.
    Caches the service instance for reuse.
    """
    global _service

    if _service is not None:
        return _service

    if not os.path.exists(CREDENTIALS_FILE):
        logger.error(
            f"Google Calendar credentials file not found: {CREDENTIALS_FILE}. "
            f"Please download the service account JSON key and place it at this path, "
            f"or set GOOGLE_CALENDAR_CREDENTIALS_FILE env var."
        )
        return None

    try:
        credentials = service_account.Credentials.from_service_account_file(
            CREDENTIALS_FILE, scopes=SCOPES
        )
        _service = build("calendar", "v3", credentials=credentials)
        logger.info("Google Calendar service initialized successfully.")
        return _service
    except Exception as e:
        logger.error(f"Failed to initialize Google Calendar service: {e}")
        return None


# ============================================================
# Date/Time Parsing (Hindi-English natural language)
# ============================================================

HINDI_DAY_MAP = {
    "aaj": 0,
    "kal": 1,
    "parso": 2,
    "parson": 2,
    "today": 0,
    "tomorrow": 1,
    "day after tomorrow": 2,
}

HINDI_TIME_MAP = {
    "subah": "10:00",
    "dopahar": "13:00",
    "shaam": "17:00",
    "raat": "20:00",
    "morning": "10:00",
    "afternoon": "13:00",
    "evening": "17:00",
    "night": "20:00",
}

WEEKDAY_MAP = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
    "somvar": 0, "mangalvar": 1, "budhvar": 2, "guruvar": 3,
    "shukravar": 4, "shanivar": 5, "ravivar": 6,
    "mon": 0, "tue": 1, "wed": 2, "thu": 3,
    "fri": 4, "sat": 5, "sun": 6,
}


def parse_meeting_datetime(
    date_str: str = "",
    time_str: str = "",
    reference_date: datetime = None,
) -> tuple[datetime, datetime]:
    """
    Parse natural language date/time (Hindi or English) into start and end datetimes.

    Returns:
        (start_datetime, end_datetime) in IST timezone.
        Falls back to tomorrow 10:00 AM if parsing fails.
    """
    tz = pytz.timezone(TIMEZONE)
    now = reference_date or datetime.now(tz)

    # ---- Parse Date ----
    parsed_date = None
    date_lower = date_str.strip().lower() if date_str else ""

    # Check Hindi/English relative days
    for key, offset in HINDI_DAY_MAP.items():
        if key in date_lower:
            parsed_date = now.date() + timedelta(days=offset)
            break

    # Check weekday names
    if not parsed_date:
        for day_name, day_num in WEEKDAY_MAP.items():
            if day_name in date_lower:
                days_ahead = day_num - now.weekday()
                if days_ahead <= 0:
                    days_ahead += 7
                parsed_date = now.date() + timedelta(days=days_ahead)
                break

    # Try dateutil parser for formats like "15 march", "2026-03-20", "march 15"
    if not parsed_date and date_lower:
        try:
            parsed_date = dateutil_parser.parse(date_lower, fuzzy=True).date()
            # If the parsed date is in the past, assume next year
            if parsed_date < now.date():
                parsed_date = parsed_date.replace(year=parsed_date.year + 1)
        except (ValueError, TypeError):
            pass

    # Default to tomorrow if we couldn't parse
    if not parsed_date:
        parsed_date = now.date() + timedelta(days=1)
        logger.warning(f"Could not parse date '{date_str}', defaulting to tomorrow: {parsed_date}")

    # ---- Parse Time ----
    parsed_time = None
    time_lower = time_str.strip().lower() if time_str else ""

    # Check Hindi time words
    for key, default_time in HINDI_TIME_MAP.items():
        if key in time_lower:
            h, m = map(int, default_time.split(":"))
            parsed_time = datetime.min.replace(hour=h, minute=m).time()
            break

    # Try extracting a numeric time (e.g., "5 baje", "3 PM", "10:00")
    if not parsed_time and time_lower:
        import re

        # Match patterns like "3 PM", "3PM", "15:00", "5 baje"
        time_patterns = [
            r"(\d{1,2}):(\d{2})\s*(am|pm)?",        # 10:00, 3:30 PM
            r"(\d{1,2})\s*(am|pm)",                  # 3 PM, 3PM
            r"(\d{1,2})\s*baje",                     # 5 baje
        ]

        for pattern in time_patterns:
            match = re.search(pattern, time_lower, re.IGNORECASE)
            if match:
                groups = match.groups()
                hour = int(groups[0])
                minute = int(groups[1]) if len(groups) > 1 and groups[1] and groups[1].isdigit() else 0
                ampm = groups[-1] if groups[-1] and groups[-1].lower() in ("am", "pm") else None

                if ampm and ampm.lower() == "pm" and hour < 12:
                    hour += 12
                elif ampm and ampm.lower() == "am" and hour == 12:
                    hour = 0
                elif not ampm and hour < 7:
                    # Assume PM for small numbers without AM/PM (e.g., "5 baje" = 5 PM)
                    hour += 12

                parsed_time = datetime.min.replace(hour=min(hour, 23), minute=minute).time()
                break

    # Default to 10:00 AM
    if not parsed_time:
        from datetime import time as dt_time

        parsed_time = dt_time(10, 0)
        if time_lower:
            logger.warning(f"Could not parse time '{time_str}', defaulting to 10:00 AM")

    # ---- Combine ----
    start_dt = tz.localize(datetime.combine(parsed_date, parsed_time))
    end_dt = start_dt + timedelta(minutes=DEFAULT_MEETING_DURATION)

    return start_dt, end_dt


# ============================================================
# Core Calendar Functions
# ============================================================

def create_calendar_event(
    summary: str,
    start_datetime: datetime,
    end_datetime: datetime,
    description: str = "",
    location: str = "",
    attendees: list[str] = None,
    send_notifications: bool = True,
) -> dict | None:
    """
    Create a Google Calendar event.

    Args:
        summary: Event title (e.g., "Site Visit - Manikya Project - Rajesh Kumar")
        start_datetime: Start datetime (timezone-aware)
        end_datetime: End datetime (timezone-aware)
        description: Event description / notes
        location: Physical location of the meeting
        attendees: List of email addresses to invite
        send_notifications: Whether to send email invites to attendees

    Returns:
        Dict with event details (id, htmlLink, etc.) or None on failure.
    """
    service = get_calendar_service()
    if not service:
        logger.error("Cannot create event: Calendar service not available.")
        return None

    event_body = {
        "summary": summary,
        "start": {
            "dateTime": start_datetime.isoformat(),
            "timeZone": TIMEZONE,
        },
        "end": {
            "dateTime": end_datetime.isoformat(),
            "timeZone": TIMEZONE,
        },
        "reminders": {
            "useDefault": False,
            "overrides": [
                {"method": "popup", "minutes": 60},   # 1 hour before
                {"method": "popup", "minutes": 15},   # 15 min before
            ],
        },
    }

    if description:
        event_body["description"] = description

    if location:
        event_body["location"] = location

    if attendees:
        event_body["attendees"] = [{"email": email} for email in attendees]

    try:
        event = (
            service.events()
            .insert(
                calendarId=CALENDAR_ID,
                body=event_body,
                sendUpdates="all" if send_notifications and attendees else "none",
            )
            .execute()
        )

        logger.info(
            f"Calendar event created: {event.get('id')} | "
            f"Link: {event.get('htmlLink')}"
        )

        return {
            "event_id": event.get("id"),
            "html_link": event.get("htmlLink"),
            "status": event.get("status"),
            "summary": event.get("summary"),
            "start": event.get("start", {}).get("dateTime"),
            "end": event.get("end", {}).get("dateTime"),
        }

    except HttpError as e:
        logger.error(f"Google Calendar API error creating event: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error creating calendar event: {e}")
        return None


def check_availability(
    start_datetime: datetime,
    end_datetime: datetime,
) -> dict:
    """
    Check if the given time slot is available on the calendar.

    Returns:
        {
            "available": bool,
            "conflicts": list of conflicting event summaries,
            "suggestion": str (next available slot if busy)
        }
    """
    service = get_calendar_service()
    if not service:
        return {"available": True, "conflicts": [], "suggestion": None}

    try:
        events_result = (
            service.events()
            .list(
                calendarId=CALENDAR_ID,
                timeMin=start_datetime.isoformat(),
                timeMax=end_datetime.isoformat(),
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )

        events = events_result.get("items", [])

        if not events:
            return {"available": True, "conflicts": [], "suggestion": None}

        conflicts = [
            {
                "summary": evt.get("summary", "Busy"),
                "start": evt.get("start", {}).get("dateTime"),
                "end": evt.get("end", {}).get("dateTime"),
            }
            for evt in events
        ]

        # Suggest the slot after the last conflict
        last_conflict_end = events[-1].get("end", {}).get("dateTime")
        suggestion = None
        if last_conflict_end:
            try:
                suggested_start = dateutil_parser.parse(last_conflict_end)
                suggestion = suggested_start.strftime("%I:%M %p")
            except Exception:
                pass

        return {
            "available": False,
            "conflicts": conflicts,
            "suggestion": suggestion,
        }

    except HttpError as e:
        logger.error(f"Google Calendar API error checking availability: {e}")
        return {"available": True, "conflicts": [], "suggestion": None}
    except Exception as e:
        logger.error(f"Error checking availability: {e}")
        return {"available": True, "conflicts": [], "suggestion": None}


def cancel_calendar_event(event_id: str) -> bool:
    """Cancel/delete a calendar event by its ID."""
    service = get_calendar_service()
    if not service:
        return False

    try:
        service.events().delete(
            calendarId=CALENDAR_ID,
            eventId=event_id,
            sendUpdates="all",
        ).execute()
        logger.info(f"Calendar event {event_id} cancelled.")
        return True
    except HttpError as e:
        logger.error(f"Error cancelling calendar event {event_id}: {e}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error cancelling event {event_id}: {e}")
        return False


def update_calendar_event(
    event_id: str,
    summary: str = None,
    start_datetime: datetime = None,
    end_datetime: datetime = None,
    description: str = None,
    location: str = None,
    status: str = None,
) -> dict | None:
    """Update an existing calendar event."""
    service = get_calendar_service()
    if not service:
        return None

    try:
        # Get the existing event first
        event = (
            service.events()
            .get(calendarId=CALENDAR_ID, eventId=event_id)
            .execute()
        )

        if summary:
            event["summary"] = summary
        if start_datetime:
            event["start"] = {
                "dateTime": start_datetime.isoformat(),
                "timeZone": TIMEZONE,
            }
        if end_datetime:
            event["end"] = {
                "dateTime": end_datetime.isoformat(),
                "timeZone": TIMEZONE,
            }
        if description:
            event["description"] = description
        if location:
            event["location"] = location
        if status:
            event["status"] = status

        updated_event = (
            service.events()
            .update(
                calendarId=CALENDAR_ID,
                eventId=event_id,
                body=event,
                sendUpdates="all",
            )
            .execute()
        )

        logger.info(f"Calendar event {event_id} updated.")
        return {
            "event_id": updated_event.get("id"),
            "html_link": updated_event.get("htmlLink"),
            "status": updated_event.get("status"),
        }

    except HttpError as e:
        logger.error(f"Error updating calendar event {event_id}: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error updating event {event_id}: {e}")
        return None


# ============================================================
# High-Level Helper: Schedule a Meeting
# ============================================================

def schedule_meeting_on_calendar(
    contact_name: str,
    contact_phone: str,
    contact_type: str,
    meeting_type: str,
    date_str: str,
    time_str: str,
    project_name: str = "",
    location: str = "",
    manager_name: str = "",
    manager_email: str = "",
    notes: str = "",
) -> dict:
    """
    High-level function to schedule a meeting on Google Calendar.
    Parses natural language date/time, checks availability, and creates the event.

    Returns:
        {
            "success": bool,
            "event_id": str or None,
            "html_link": str or None,
            "start": str,
            "end": str,
            "available": bool,
            "message": str,
        }
    """
    # 1. Parse date/time
    start_dt, end_dt = parse_meeting_datetime(date_str, time_str)

    logger.info(
        f"Scheduling meeting: {contact_name} ({contact_type}) | "
        f"{meeting_type} | {start_dt} to {end_dt}"
    )

    # 2. Check availability
    availability = check_availability(start_dt, end_dt)

    if not availability["available"]:
        conflict_names = [c["summary"] for c in availability["conflicts"]]
        suggestion = availability.get("suggestion")
        msg = f"Time slot is busy (conflicts: {', '.join(conflict_names)})."
        if suggestion:
            msg += f" Next available: {suggestion}."

        logger.warning(f"Time slot unavailable for {contact_name}: {msg}")

        return {
            "success": False,
            "event_id": None,
            "html_link": None,
            "start": start_dt.isoformat(),
            "end": end_dt.isoformat(),
            "available": False,
            "message": msg,
        }

    # 3. Build event details
    type_label = {
        "site_visit": "Site Visit",
        "office_meeting": "Office Meeting",
        "call_back": "Call Back",
    }.get(meeting_type, meeting_type.replace("_", " ").title())

    summary = f"{type_label} - {contact_name}"
    if project_name:
        summary += f" - {project_name}"

    description_parts = [
        f"Contact: {contact_name}",
        f"Phone: {contact_phone}",
        f"Type: {contact_type.title()}",
        f"Meeting: {type_label}",
    ]
    if project_name:
        description_parts.append(f"Project: {project_name}")
    if manager_name:
        description_parts.append(f"Manager: {manager_name}")
    if notes:
        description_parts.append(f"Notes: {notes}")

    description_parts.append(f"\nScheduled by Hookfish Voice Agent")
    description = "\n".join(description_parts)

    # Attendees (manager email if available)
    attendees = []
    if manager_email:
        attendees.append(manager_email)

    # 4. Create the event
    event = create_calendar_event(
        summary=summary,
        start_datetime=start_dt,
        end_datetime=end_dt,
        description=description,
        location=location or (f"{project_name} Site" if project_name else ""),
        attendees=attendees if attendees else None,
        send_notifications=True,
    )

    if event:
        return {
            "success": True,
            "event_id": event["event_id"],
            "html_link": event["html_link"],
            "start": start_dt.strftime("%d %b %Y %I:%M %p"),
            "end": end_dt.strftime("%d %b %Y %I:%M %p"),
            "available": True,
            "message": f"Meeting scheduled: {start_dt.strftime('%d %b %Y at %I:%M %p')}",
        }
    else:
        return {
            "success": False,
            "event_id": None,
            "html_link": None,
            "start": start_dt.isoformat(),
            "end": end_dt.isoformat(),
            "available": True,
            "message": "Failed to create calendar event. Meeting saved in database only.",
        }
