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
#
# Required env vars:
#   GOOGLE_CALENDAR_CREDENTIALS_FILE  — absolute path to the service account JSON
#   GOOGLE_CALENDAR_ID                — calendar ID (e.g. xxx@group.calendar.google.com)
#
# Optional env vars:
#   GOOGLE_CALENDAR_ENABLED           — set to "false" to disable calendar entirely
#                                       (agent runs, meetings saved to DB only)
#   GOOGLE_CALENDAR_TIMEZONE          — default: Asia/Kolkata
#   GOOGLE_CALENDAR_DEFAULT_DURATION  — default meeting duration in minutes (default: 60)
# =========================================================================

import os
import logging
import json
import threading
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

# Set GOOGLE_CALENDAR_ENABLED=false to run without calendar (meetings saved to DB only)
CALENDAR_ENABLED = os.getenv("GOOGLE_CALENDAR_ENABLED", "true").strip().lower() != "false"

CREDENTIALS_FILE = os.getenv("GOOGLE_CALENDAR_CREDENTIALS_FILE", "").strip()
CALENDAR_ID      = os.getenv("GOOGLE_CALENDAR_ID", "").strip()
TIMEZONE         = os.getenv("GOOGLE_CALENDAR_TIMEZONE", "Asia/Kolkata")
DEFAULT_MEETING_DURATION = int(os.getenv("GOOGLE_CALENDAR_DEFAULT_DURATION", "60"))
SCOPES = ["https://www.googleapis.com/auth/calendar"]

# Required fields inside the service account JSON
_REQUIRED_SA_FIELDS = ("type", "project_id", "private_key_id", "private_key", "client_email")


# ============================================================
# Startup validation
# ============================================================

class CalendarConfigError(RuntimeError):
    """Raised at startup when calendar configuration is incomplete or invalid."""


def validate_calendar_config() -> None:
    """
    Validate all calendar configuration at startup.

    Raises CalendarConfigError with a precise, actionable message for each
    problem found.  Call order matters — we stop at the first failure so the
    operator gets one clear thing to fix at a time.

    Does nothing if GOOGLE_CALENDAR_ENABLED=false.
    """
    if not CALENDAR_ENABLED:
        logger.info("Google Calendar disabled via GOOGLE_CALENDAR_ENABLED=false. Skipping validation.")
        return

    errors = []

    # ── 1. Credentials file path is set ──────────────────────────
    if not CREDENTIALS_FILE:
        errors.append(
            "GOOGLE_CALENDAR_CREDENTIALS_FILE is not set.\n"
            "  Fix: export GOOGLE_CALENDAR_CREDENTIALS_FILE=/absolute/path/to/service_account.json\n"
            "  Or:  set GOOGLE_CALENDAR_ENABLED=false to run without calendar."
        )

    # ── 2. Credentials file exists on disk ───────────────────────
    elif not os.path.isfile(CREDENTIALS_FILE):
        errors.append(
            f"GOOGLE_CALENDAR_CREDENTIALS_FILE points to a file that does not exist:\n"
            f"  Path: {CREDENTIALS_FILE}\n"
            f"  Fix: Download the service account JSON key from Google Cloud Console\n"
            f"       and place it at the path above, or update the env var."
        )

    else:
        # ── 3. File is valid JSON ─────────────────────────────────
        try:
            with open(CREDENTIALS_FILE) as f:
                sa_data = json.load(f)
        except json.JSONDecodeError as exc:
            errors.append(
                f"GOOGLE_CALENDAR_CREDENTIALS_FILE is not valid JSON:\n"
                f"  Path:  {CREDENTIALS_FILE}\n"
                f"  Error: {exc}\n"
                f"  Fix:   Re-download the service account key from Google Cloud Console."
            )
            sa_data = {}

        # ── 4. JSON contains required service-account fields ──────
        if sa_data:
            missing = [k for k in _REQUIRED_SA_FIELDS if not sa_data.get(k)]
            if missing:
                errors.append(
                    f"Service account JSON is missing required fields: {missing}\n"
                    f"  Path: {CREDENTIALS_FILE}\n"
                    f"  Fix:  Re-download the service account key from Google Cloud Console."
                )

            # ── 5. File is actually a service account (not an OAuth client) ──
            elif sa_data.get("type") != "service_account":
                errors.append(
                    f"GOOGLE_CALENDAR_CREDENTIALS_FILE contains a '{sa_data.get('type')}' credential.\n"
                    f"  Expected: service_account\n"
                    f"  Fix:  Go to Google Cloud Console → IAM & Admin → Service Accounts\n"
                    f"        and download the JSON key for a service account, not an OAuth 2.0 client."
                )

    # ── 6. Calendar ID is set ─────────────────────────────────────
    if not CALENDAR_ID:
        errors.append(
            "GOOGLE_CALENDAR_ID is not set.\n"
            "  Fix: export GOOGLE_CALENDAR_ID=your-calendar@group.calendar.google.com\n"
            "       Find it in Google Calendar → Settings → (your calendar) → Calendar ID.\n"
            "  Note: Do NOT use 'primary' — the service account's primary calendar is\n"
            "        not the shared Hookfish calendar."
        )

    # ── 7. Calendar ID looks like a real shared calendar ─────────
    elif CALENDAR_ID == "primary":
        errors.append(
            "GOOGLE_CALENDAR_ID is set to 'primary'.\n"
            "  The service account's primary calendar is NOT the shared company calendar.\n"
            "  Fix: export GOOGLE_CALENDAR_ID=your-calendar@group.calendar.google.com\n"
            "       Find it in Google Calendar → Settings → (your calendar) → Calendar ID."
        )

    if errors:
        divider = "\n" + "─" * 60 + "\n"
        message = divider.join(errors)
        raise CalendarConfigError(
            f"\n{'=' * 60}\n"
            f"  Google Calendar configuration error(s) found at startup\n"
            f"{'=' * 60}\n"
            f"{message}\n"
            f"{'=' * 60}"
        )

    logger.info(
        f"Google Calendar config validated OK — "
        f"calendar={CALENDAR_ID}, credentials={CREDENTIALS_FILE}"
    )


# ============================================================
# Authentication
# ============================================================

_service = None
_service_lock = threading.Lock()
_config_validated = False


def get_calendar_service():
    """
    Return a cached Google Calendar API service client.

    Thread-safe via double-checked locking.
    Runs validate_calendar_config() on first call so any config problem
    surfaces immediately with a clear message rather than an obscure auth
    failure mid-call.

    Returns None if CALENDAR_ENABLED=false or if credentials are invalid
    (error already logged).  Callers should treat None as "calendar
    unavailable — save to DB only".
    """
    global _service, _config_validated

    if not CALENDAR_ENABLED:
        return None

    if _service is not None:
        return _service

    with _service_lock:
        if _service is not None:
            return _service

        # Validate config once on first real use
        if not _config_validated:
            try:
                validate_calendar_config()
                _config_validated = True
            except CalendarConfigError as exc:
                # Log the full multi-line error so it's visible in the worker log
                logger.error(str(exc))
                return None

        try:
            credentials = service_account.Credentials.from_service_account_file(
                CREDENTIALS_FILE, scopes=SCOPES
            )
            _service = build("calendar", "v3", credentials=credentials)
            logger.info("Google Calendar service initialised successfully.")
            return _service
        except Exception as exc:
            logger.error(
                f"Failed to build Google Calendar service client.\n"
                f"  Credentials: {CREDENTIALS_FILE}\n"
                f"  Error:       {exc}\n"
                f"  Common causes:\n"
                f"    - Service account does not have the Calendar API enabled\n"
                f"    - Calendar has not been shared with the service account email\n"
                f"    - Private key in the JSON has been revoked"
            )
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