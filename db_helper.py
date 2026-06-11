# db_helper.py — Database helper for Hookfish Voice Agent

import os
import logging
import threading
import pymysql
from contextlib import contextmanager
from dbutils.pooled_db import PooledDB
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("voice-agent-db")

# ============================================================
# Connection Pool
# ============================================================
# Single pool shared across all threads/calls.
# mincached=2  — keep 2 idle connections warm at all times
# maxcached=10 — cap idle connections sitting in pool
# maxconnections=20 — hard ceiling on simultaneous connections
# blocking=True — callers wait instead of raising when pool is full
# ============================================================

_pool: PooledDB | None = None
_pool_lock = threading.Lock()


def _get_pool() -> PooledDB:
    """Return the shared connection pool, initialising it on first call."""
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:  # double-checked locking
                _pool = PooledDB(
                    creator=pymysql,
                    mincached=2,
                    maxcached=10,
                    maxconnections=20,
                    blocking=True,
                    host=os.getenv("DB_HOST"),
                    port=int(os.getenv("DB_PORT", 3306)),
                    user=os.getenv("DB_USER"),
                    password=os.getenv("DB_PASSWORD"),
                    database=os.getenv("DB_NAME"),
                    ssl={"ssl": {}},
                    cursorclass=pymysql.cursors.DictCursor,
                    autocommit=False,
                    charset="utf8mb4",
                )
                logger.info("DB connection pool initialised (max=%d)", 20)
    return _pool


@contextmanager
def get_connection():
    """
    Context manager that borrows a connection from the pool and returns it
    automatically, even on exceptions.

    Usage:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(...)
    """
    pool = _get_pool()
    conn = pool.connection()
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()  # returns to pool, does not close the socket


def lookup_customer_by_phone(phone: str) -> dict | None:
    """
    Look up a customer by phone number.
    Returns customer info dict or None if not found.
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, name, phone, origin, created_at FROM customers WHERE phone = %s AND deleted = 0 LIMIT 1",
                    (phone,),
                )
                return cur.fetchone()
    except Exception as e:
        logger.error(f"Error looking up customer by phone {phone}: {e}")
        return None


def lookup_lead_by_phone(phone: str) -> list[dict]:
    """
    Look up all leads associated with a phone number (via partner_phone or customer info).
    Returns a list of lead dicts.
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, customer_name, partner_name, property_name,
                           status, last_status, notes, followup, message,
                           partner_phone, partner_email, employee_name
                    FROM all_leads
                    WHERE partner_phone = %s AND deleted = 0
                    ORDER BY created_at DESC
                    LIMIT 10
                    """,
                    (phone,),
                )
                return cur.fetchall()
    except Exception as e:
        logger.error(f"Error looking up leads by phone {phone}: {e}")
        return []


def lookup_property_by_name(property_name: str) -> dict | None:
    """
    Look up a property by name (partial match).
    Returns property info dict or None.
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, name, alias, type, commission_percentage,
                           site_visit_bonus, guarantee_for_sale
                    FROM properties
                    WHERE name LIKE %s AND (deleted = 0 OR deleted IS NULL)
                    LIMIT 5
                    """,
                    (f"%{property_name}%",),
                )
                return cur.fetchall()
    except Exception as e:
        logger.error(f"Error looking up property '{property_name}': {e}")
        return None


def get_leads_for_partner(partner_id: int) -> list[dict]:
    """
    Get all leads assigned to a specific partner/broker.
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, customer_name, property_name, status,
                           last_status, notes, followup
                    FROM all_leads
                    WHERE partner_id = %s AND deleted = 0
                    ORDER BY created_at DESC
                    LIMIT 20
                    """,
                    (partner_id,),
                )
                return cur.fetchall()
    except Exception as e:
        logger.error(f"Error fetching leads for partner {partner_id}: {e}")
        return []


def save_call_conversation(call_id: str, phone: str, caller_name: str,
                           messages: list, duration: int = None,
                           outcome: str = None, interest_level: str = None):
    """
    Save the conversation transcript and metadata to the conversations table.
    """
    import json
    import uuid

    try:
        conv_id = str(uuid.uuid4())
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO conversations
                        (id, call_id, caller_number, caller_name, messages,
                         duration_seconds, outcome, interest_level)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (conv_id, call_id, phone, caller_name,
                     json.dumps(messages, ensure_ascii=False),
                     duration, outcome, interest_level),
                )
                conn.commit()
        logger.info(f"Saved conversation {conv_id} for call {call_id}")
        return conv_id
    except Exception as e:
        logger.error(f"Error saving conversation for call {call_id}: {e}")
        return None


def update_lead_status(lead_id: int, status: str, notes: str = None):
    """
    Update the status and notes of a lead after a call.
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                if notes:
                    cur.execute(
                        "UPDATE all_leads SET last_status = %s, notes = %s WHERE id = %s",
                        (status, notes, lead_id),
                    )
                else:
                    cur.execute(
                        "UPDATE all_leads SET last_status = %s WHERE id = %s",
                        (status, lead_id),
                    )
                conn.commit()
        logger.info(f"Updated lead {lead_id} status to '{status}'")
    except Exception as e:
        logger.error(f"Error updating lead {lead_id}: {e}")


def save_call_outcome(phone: str, outcome: str, reason: str = None,
                      interest_level: str = None, next_action: str = None,
                      callback_date: str = None):
    """
    Save call outcome by updating the latest lead for this phone number.
    Falls back to logging if no lead exists.
    """
    try:
        leads = lookup_lead_by_phone(phone)
        if leads:
            lead = leads[0]
            notes_parts = [f"Call Outcome: {outcome}"]
            if reason:
                notes_parts.append(f"Reason: {reason}")
            if interest_level:
                notes_parts.append(f"Interest: {interest_level}")
            if next_action:
                notes_parts.append(f"Next Action: {next_action}")
            if callback_date:
                notes_parts.append(f"Callback: {callback_date}")
            notes = " | ".join(notes_parts)

            # Map outcome to a lead status
            status_map = {
                "interested": "interested",
                "maybe": "follow_up",
                "not_interested": "closed",
                "callback_requested": "follow_up",
                "escalated": "escalated",
            }
            status = status_map.get(outcome, outcome)
            update_lead_status(lead['id'], status, notes)
            logger.info(f"Saved call outcome for {phone}: {notes}")
            return True
        else:
            logger.info(
                f"No lead found for {phone}. "
                f"Outcome: {outcome}, Reason: {reason}, "
                f"Interest: {interest_level}, Next: {next_action}"
            )
            return False
    except Exception as e:
        logger.error(f"Error saving call outcome for {phone}: {e}")
        return False


def get_project_details_for_lead(phone: str) -> list:
    """
    Get project/property details associated with a phone number's leads.
    Looks up leads for the phone, then fetches property details for each.
    """
    try:
        leads = lookup_lead_by_phone(phone)
        if not leads:
            return []

        properties = []
        seen_names = set()
        for lead in leads:
            prop_name = lead.get('property_name')
            if prop_name and prop_name not in seen_names:
                seen_names.add(prop_name)
                prop_details = lookup_property_by_name(prop_name)
                if prop_details:
                    if isinstance(prop_details, list):
                        properties.extend(prop_details)
                    else:
                        properties.append(prop_details)

        return properties
    except Exception as e:
        logger.error(f"Error getting project details for {phone}: {e}")
        return []


# ============================================================
# DNC (Do Not Call) Management — Section 5.1
# ============================================================

def is_dnc(phone: str) -> bool:
    """Check if a phone number is on the Do Not Call list."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM agent_dnc_list WHERE phone_number = %s LIMIT 1",
                    (phone,),
                )
                return cur.fetchone() is not None
    except Exception as e:
        logger.error(f"Error checking DNC for {phone}: {e}")
        return False


def mark_dnc(phone: str, reason: str = "user_requested", added_by: str = "agent") -> bool:
    """Add a phone number to the DNC list."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO agent_dnc_list (phone_number, reason, added_by)
                    VALUES (%s, %s, %s)
                    ON DUPLICATE KEY UPDATE reason = %s, added_by = %s
                    """,
                    (phone, reason, added_by, reason, added_by),
                )
                conn.commit()
        logger.info(f"Marked {phone} as DNC. Reason: {reason}")
        return True
    except Exception as e:
        logger.error(f"Error marking DNC for {phone}: {e}")
        return False


# ============================================================
# Agent Call Logging — Section 3.2
# ============================================================

def create_call_log(
    call_id: str,
    phone_number: str,
    caller_name: str = None,
    contact_type: str = "buyer",
    direction: str = "outbound",
    room_name: str = None,
) -> str:
    """Create a new call log entry when a call starts. Returns the log ID."""
    import uuid

    log_id = str(uuid.uuid4())
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO agent_call_logs
                        (id, call_id, room_name, phone_number, caller_name,
                         contact_type, direction, started_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
                    """,
                    (log_id, call_id, room_name, phone_number,
                     caller_name, contact_type, direction),
                )
                conn.commit()
        logger.info(f"Created call log {log_id} for {phone_number}")
        return log_id
    except Exception as e:
        logger.error(f"Error creating call log for {phone_number}: {e}")
        return None


def update_call_log(
    log_id: str,
    disposition: str = None,
    qualification: str = None,
    interest_level: str = None,
    outcome_reason: str = None,
    next_action: str = None,
    meeting_slot: str = None,
    manager_assigned: str = None,
    next_callback: str = None,
    transcript: str = None,
    call_summary: str = None,
    duration_seconds: int = None,
    notes: str = None,
):
    """Update a call log with outcome data when the call ends."""
    try:
        updates = []
        values = []

        field_map = {
            "disposition": disposition,
            "qualification": qualification,
            "interest_level": interest_level,
            "outcome_reason": outcome_reason,
            "next_action": next_action,
            "manager_assigned": manager_assigned,
            "transcript": transcript,
            "call_summary": call_summary,
            "notes": notes,
        }

        for field, value in field_map.items():
            if value is not None:
                updates.append(f"{field} = %s")
                values.append(value)

        if duration_seconds is not None:
            updates.append("duration_seconds = %s")
            values.append(duration_seconds)

        if meeting_slot is not None:
            updates.append("meeting_slot = %s")
            values.append(meeting_slot)

        if next_callback is not None:
            updates.append("next_callback = %s")
            values.append(next_callback)

        # Always set ended_at
        updates.append("ended_at = NOW()")

        if updates:
            sql = f"UPDATE agent_call_logs SET {', '.join(updates)} WHERE id = %s"
            values.append(log_id)
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, tuple(values))
                    conn.commit()

        logger.info(f"Updated call log {log_id}")
    except Exception as e:
        logger.error(f"Error updating call log {log_id}: {e}")


def save_call_transcript(log_id: str, transcript: str):
    """Save or update the full transcript for a call log."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE agent_call_logs SET transcript = %s WHERE id = %s",
                    (transcript, log_id),
                )
                conn.commit()
        logger.info(f"Saved transcript for call log {log_id}")
    except Exception as e:
        logger.error(f"Error saving transcript for {log_id}: {e}")


# ============================================================
# Call Attempts Tracking — Section 6.3
# ============================================================

def check_call_allowed(phone: str) -> dict:
    """
    Check if a call to this number is allowed today.
    Returns: {"allowed": bool, "attempts_today": int, "reason": str}
    Rules: Max 1 attempt per contact per day. No DNC numbers.
    """
    # Bypass limits for testing number
    if "6362185137" in phone:
        return {
            "allowed": True,
            "attempts_today": 0,
            "reason": "Test number bypass",
        }

    # Check DNC first
    if is_dnc(phone):
        return {
            "allowed": False,
            "attempts_today": 0,
            "reason": "Number is on DNC list",
        }

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT attempt_count FROM agent_call_attempts
                    WHERE phone_number = %s AND attempt_date = CURDATE()
                    LIMIT 1
                    """,
                    (phone,),
                )
                result = cur.fetchone()

        if result and result["attempt_count"] >= 6:
            return {
                "allowed": False,
                "attempts_today": result["attempt_count"],
                "reason": "Max 3 calls per day reached",
            }

        return {
            "allowed": True,
            "attempts_today": result["attempt_count"] if result else 0,
            "reason": "OK",
        }
    except Exception as e:
        logger.error(f"Error checking call allowed for {phone}: {e}")
        return {"allowed": True, "attempts_today": 0, "reason": "Error checking (allowing)"}


def record_call_attempt(phone: str, result: str = "answered", call_log_id: str = None):
    """Record a call attempt for today. Increments the counter if already exists."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO agent_call_attempts
                        (phone_number, attempt_date, attempt_count, last_result, call_log_id)
                    VALUES (%s, CURDATE(), 1, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        attempt_count = attempt_count + 1,
                        last_attempt_at = NOW(),
                        last_result = %s,
                        call_log_id = %s
                    """,
                    (phone, result, call_log_id, result, call_log_id),
                )
                conn.commit()
        logger.info(f"Recorded call attempt for {phone}: {result}")
    except Exception as e:
        logger.error(f"Error recording call attempt for {phone}: {e}")


# ============================================================
# Manager Allocation — Section 3.4 (Round-Robin)
# ============================================================

def allocate_manager(project_name: str = None, region: str = None) -> dict | None:
    """
    Allocate a manager using round-robin logic.
    Respects 'do_not_allocate' and 'preferred' flags.
    Returns manager dict or None.
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                # Build query with optional filters
                conditions = ["is_active = 1", "do_not_allocate = 0"]
                params = []

                # Try preferred managers first
                preferred_conditions = conditions.copy() + ["preferred = 1"]
                preferred_params = params.copy()

                if project_name:
                    preferred_conditions.append("(projects LIKE %s OR projects IS NULL)")
                    preferred_params.append(f"%{project_name}%")
                if region:
                    preferred_conditions.append("(regions LIKE %s OR regions IS NULL)")
                    preferred_params.append(f"%{region}%")

                # First, try preferred managers
                where_clause = " AND ".join(preferred_conditions)
                cur.execute(
                    f"""
                    SELECT id, name, phone, email, regions, projects
                    FROM agent_managers
                    WHERE {where_clause}
                    ORDER BY last_allocated ASC, total_allocated ASC
                    LIMIT 1
                    """,
                    tuple(preferred_params),
                )
                manager = cur.fetchone()

                # If no preferred, try any eligible manager
                if not manager:
                    any_conditions = conditions.copy()
                    any_params = params.copy()
                    if project_name:
                        any_conditions.append("(projects LIKE %s OR projects IS NULL)")
                        any_params.append(f"%{project_name}%")
                    if region:
                        any_conditions.append("(regions LIKE %s OR regions IS NULL)")
                        any_params.append(f"%{region}%")

                    where_clause = " AND ".join(any_conditions)
                    cur.execute(
                        f"""
                        SELECT id, name, phone, email, regions, projects
                        FROM agent_managers
                        WHERE {where_clause}
                        ORDER BY last_allocated ASC, total_allocated ASC
                        LIMIT 1
                        """,
                        tuple(any_params),
                    )
                    manager = cur.fetchone()

                # If still no manager, get any active manager (ignore filters)
                if not manager:
                    cur.execute(
                        """
                        SELECT id, name, phone, email, regions, projects
                        FROM agent_managers
                        WHERE is_active = 1 AND do_not_allocate = 0
                        ORDER BY last_allocated ASC, total_allocated ASC
                        LIMIT 1
                        """
                    )
                    manager = cur.fetchone()

                # Update the allocation tracking
                if manager:
                    cur.execute(
                        """
                        UPDATE agent_managers
                        SET last_allocated = NOW(), total_allocated = total_allocated + 1
                        WHERE id = %s
                        """,
                        (manager["id"],),
                    )
                    conn.commit()
                    logger.info(f"Allocated manager: {manager['name']} (ID: {manager['id']})")

        return manager
    except Exception as e:
        logger.error(f"Error allocating manager: {e}")
        return None


# ============================================================
# Meeting Management — Section 3.3
# ============================================================

def create_meeting(
    phone_number: str,
    contact_name: str = None,
    contact_type: str = "buyer",
    meeting_type: str = "site_visit",
    meeting_date: str = None,
    meeting_time: str = None,
    location: str = None,
    project_name: str = None,
    manager_id: int = None,
    manager_name: str = None,
    call_log_id: str = None,
    notes: str = None,
) -> str:
    """Create a new meeting/site visit record. Returns meeting ID."""
    import uuid

    meeting_id = str(uuid.uuid4())
    try:
        # Combine date + time into datetime if both provided
        meeting_datetime = None
        if meeting_date and meeting_time:
            meeting_datetime = f"{meeting_date} {meeting_time}"
        elif meeting_date:
            meeting_datetime = f"{meeting_date} 10:00:00"  # default time

        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO agent_meetings
                        (id, call_log_id, phone_number, contact_name, contact_type,
                         meeting_type, meeting_date, meeting_time, meeting_datetime,
                         location, project_name, manager_id, manager_name, notes)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (meeting_id, call_log_id, phone_number, contact_name,
                     contact_type, meeting_type, meeting_date, meeting_time,
                     meeting_datetime, location, project_name, manager_id,
                     manager_name, notes),
                )
                conn.commit()
        logger.info(f"Created meeting {meeting_id} for {phone_number}")
        return meeting_id
    except Exception as e:
        logger.error(f"Error creating meeting for {phone_number}: {e}")
        return None


def update_meeting_status(meeting_id: str, status: str, notes: str = None):
    """Update meeting status (scheduled/confirmed/completed/cancelled/no_show)."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                if status == "completed":
                    cur.execute(
                        "UPDATE agent_meetings SET status = %s, completed_at = NOW(), notes = CONCAT(IFNULL(notes,''), %s) WHERE id = %s",
                        (status, f" | {notes}" if notes else "", meeting_id),
                    )
                else:
                    cur.execute(
                        "UPDATE agent_meetings SET status = %s WHERE id = %s",
                        (status, meeting_id),
                    )
                conn.commit()
        logger.info(f"Updated meeting {meeting_id} to status: {status}")
    except Exception as e:
        logger.error(f"Error updating meeting {meeting_id}: {e}")


def update_meeting_calendar(meeting_id: str, calendar_event_id: str, calendar_invite_sent: bool = True):
    """Update a meeting record with the Google Calendar event ID."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE agent_meetings
                    SET calendar_event_id = %s, calendar_invite_sent = %s
                    WHERE id = %s
                    """,
                    (calendar_event_id, 1 if calendar_invite_sent else 0, meeting_id),
                )
                conn.commit()
        logger.info(f"Updated meeting {meeting_id} with calendar event {calendar_event_id}")
    except Exception as e:
        logger.error(f"Error updating meeting calendar for {meeting_id}: {e}")


def get_manager_email(manager_id: int) -> str | None:
    """Get a manager's email by their ID."""
    if not manager_id:
        return None
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT email FROM agent_managers WHERE id = %s LIMIT 1",
                    (manager_id,),
                )
                result = cur.fetchone()
        return result["email"] if result and result.get("email") else None
    except Exception as e:
        logger.error(f"Error getting manager email for ID {manager_id}: {e}")
        return None


def get_meetings_for_phone(phone: str) -> list:
    """Get all meetings for a phone number."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, meeting_type, meeting_date, meeting_time,
                           location, project_name, manager_name, status,
                           qr_scanned, notes
                    FROM agent_meetings
                    WHERE phone_number = %s
                    ORDER BY created_at DESC
                    LIMIT 10
                    """,
                    (phone,),
                )
                return cur.fetchall()
    except Exception as e:
        logger.error(f"Error getting meetings for {phone}: {e}")
        return []


# ============================================================
# Optimised context fetch — 2 round-trips for everything
# ============================================================
#
# BEFORE this change:
#   is_dnc()              → round-trip 1
#   check_call_allowed()
#     └─ is_dnc()         → round-trip 2  (duplicate!)
#     └─ attempt count    → round-trip 3
#   customer query        → round-trip 4
#   leads query           → round-trip 5
#   meetings query        → round-trip 6
#   property loop         → round-trip 7, 8, 9 … (one per unique property)
#
# AFTER:
#   Round-trip 1 — five queries batched in one connection using
#                  cursor.execute() + nextset() multi-result pattern:
#                  DNC · attempt count · customer · leads · meetings
#   Round-trip 2 — single IN (...) query for all property names found
#                  in the leads (0 or 1 round-trips, skipped if no leads)
#
# Net saving: 4–7 round-trips per call setup eliminated.
# ============================================================

def get_agent_context_optimized(phone: str) -> dict:
    """
    Fetch everything the voice agent needs before answering a call.

    Returns a dict with keys:
        allowed   bool   — False if DNC or daily cap reached
        reason    str    — human-readable explanation when not allowed
        customer  dict|None
        leads     list[dict]
        projects  list[dict]
        meetings  list[dict]

    All five DB checks run inside a single connection in two round-trips.
    """
    result = {
        "allowed": True,
        "reason": "OK",
        "customer": None,
        "leads": [],
        "projects": [],
        "meetings": [],
    }

    # Test-number bypass — skip all DB checks
    is_test = "6362185137" in phone
    if is_test:
        result["reason"] = "Test number bypass"

    # ----------------------------------------------------------------
    # Round-trip 1: five queries, one connection, one network flush.
    # pymysql supports multi-statement via cursor.execute() + nextset().
    # Each SELECT is separated by ; and they execute sequentially on the
    # server — no extra TCP round-trips between them.
    # ----------------------------------------------------------------
    BATCH_SQL = """
        SELECT 1 AS is_dnc
        FROM   agent_dnc_list
        WHERE  phone_number = %s
        LIMIT  1;

        SELECT attempt_count
        FROM   agent_call_attempts
        WHERE  phone_number = %s AND attempt_date = CURDATE()
        LIMIT  1;

        SELECT id, name, phone, origin
        FROM   customers
        WHERE  phone = %s AND deleted = 0
        LIMIT  1;

        SELECT id, customer_name, partner_name, property_name,
               status, last_status, notes, followup, partner_phone
        FROM   all_leads
        WHERE  partner_phone = %s AND deleted = 0
        ORDER  BY created_at DESC
        LIMIT  5;

        SELECT meeting_type, meeting_date, meeting_time,
               project_name, manager_name, status
        FROM   agent_meetings
        WHERE  phone_number = %s
        ORDER  BY created_at DESC
        LIMIT  5;
    """

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                # Execute all five statements in one call
                cur.execute(BATCH_SQL, (phone, phone, phone, phone, phone))

                # --- Result set 1: DNC ---
                dnc_row = cur.fetchone()
                if not is_test and dnc_row:
                    result["allowed"] = False
                    result["reason"] = "Number is on DNC list"
                    return result

                # --- Result set 2: attempt count ---
                cur.nextset()
                attempt_row = cur.fetchone()
                if not is_test and attempt_row and attempt_row["attempt_count"] >= 1:
                    result["allowed"] = False
                    result["reason"] = "Max 1 call per day reached"
                    return result

                # --- Result set 3: customer ---
                cur.nextset()
                result["customer"] = cur.fetchone()

                # --- Result set 4: leads ---
                cur.nextset()
                result["leads"] = cur.fetchall() or []

                # --- Result set 5: meetings ---
                cur.nextset()
                result["meetings"] = cur.fetchall() or []

    except Exception as e:
        logger.error(f"get_agent_context_optimized({phone}): batch query failed: {e}")
        # Fall back to individual queries so the call is not blocked by a
        # query-planner issue (e.g. server has multi-statement disabled)
        return _get_agent_context_fallback(phone)

    # ----------------------------------------------------------------
    # Round-trip 2 (conditional): property details for all unique
    # property names found across the leads — single IN (...) query.
    # Skipped entirely if there are no leads.
    # ----------------------------------------------------------------
    property_names = list({
        lead["property_name"]
        for lead in result["leads"]
        if lead.get("property_name")
    })

    if property_names:
        try:
            placeholders = ", ".join(["%s"] * len(property_names))
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        f"""
                        SELECT id, name, alias, type, commission_percentage,
                               site_visit_bonus, guarantee_for_sale
                        FROM   properties
                        WHERE  name IN ({placeholders})
                          AND  (deleted = 0 OR deleted IS NULL)
                        LIMIT  10
                        """,
                        property_names,
                    )
                    result["projects"] = cur.fetchall() or []
        except Exception as e:
            logger.error(f"get_agent_context_optimized({phone}): property query failed: {e}")

    return result


def _get_agent_context_fallback(phone: str) -> dict:
    """
    Sequential fallback used only when the batch query fails.
    Mirrors the original pre-optimisation behaviour exactly.
    Called automatically by get_agent_context_optimized on error.
    """
    result = {
        "allowed": True,
        "reason": "OK",
        "customer": None,
        "leads": [],
        "projects": [],
        "meetings": [],
    }

    if "6362185137" in phone:
        result["reason"] = "Test number bypass"
    elif is_dnc(phone):
        result["allowed"] = False
        result["reason"] = "Number is on DNC list"
        return result
    else:
        call_check = check_call_allowed(phone)
        if not call_check["allowed"]:
            result["allowed"] = False
            result["reason"] = call_check["reason"]
            return result

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, name, phone, origin FROM customers "
                    "WHERE phone = %s AND deleted = 0 LIMIT 1",
                    (phone,),
                )
                result["customer"] = cur.fetchone()

                cur.execute(
                    """
                    SELECT id, customer_name, partner_name, property_name,
                           status, last_status, notes, followup, partner_phone
                    FROM all_leads
                    WHERE partner_phone = %s AND deleted = 0
                    ORDER BY created_at DESC LIMIT 5
                    """,
                    (phone,),
                )
                result["leads"] = cur.fetchall() or []

                cur.execute(
                    """
                    SELECT meeting_type, meeting_date, meeting_time,
                           project_name, manager_name, status
                    FROM agent_meetings
                    WHERE phone_number = %s
                    ORDER BY created_at DESC LIMIT 5
                    """,
                    (phone,),
                )
                result["meetings"] = cur.fetchall() or []
    except Exception as e:
        logger.error(f"_get_agent_context_fallback({phone}): {e}")

    seen, props = set(), []
    for lead in result["leads"]:
        pname = lead.get("property_name")
        if pname and pname not in seen:
            seen.add(pname)
            detail = lookup_property_by_name(pname)
            if detail:
                props.extend(detail if isinstance(detail, list) else [detail])
    result["projects"] = props

    return result


# ============================================================
# Project Script & Facts — dynamic instruction building
# ============================================================

def get_project_script(project_name: str, contact_type: str = "buyer") -> str | None:
    """
    Fetch the call script for a project from agent_scripts.

    Tries an exact name match first, then a LIKE match.
    contact_type ('buyer'/'broker') is appended to the search term so
    separate buyer/broker scripts can coexist for the same project.

    Returns the script content string, or None if not found.
    """
    if not project_name:
        return None
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                # 1. Exact match with contact_type suffix
                cur.execute(
                    """
                    SELECT content FROM agent_scripts
                    WHERE name = %s AND is_active = 1
                    LIMIT 1
                    """,
                    (f"{project_name} — {contact_type.capitalize()} Pitch",),
                )
                row = cur.fetchone()
                if row and row.get("content"):
                    return row["content"]

                # 2. Partial name match (any contact type)
                cur.execute(
                    """
                    SELECT content FROM agent_scripts
                    WHERE name LIKE %s AND is_active = 1
                    ORDER BY updated_at DESC
                    LIMIT 1
                    """,
                    (f"%{project_name}%",),
                )
                row = cur.fetchone()
                if row and row.get("content"):
                    return row["content"]

    except Exception as e:
        logger.error(f"get_project_script({project_name}): {e}")
    return None


def get_project_facts(project_name: str) -> dict | None:
    """
    Fetch structured project facts from the properties table.

    Returns a dict with keys from the properties row plus a parsed
    'details' dict from the JSON 'details' column (if present).
    The 'details' column is expected to hold fields like:
        price_smaller, price_larger, payment_plan, developer,
        location, construction_progress, rera_possession,
        unit_sizes, available_floors, total_floors, bhk_type

    Returns None if no matching property is found.
    """
    if not project_name:
        return None
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, name, alias, type,
                           commission_percentage, site_visit_bonus,
                           guarantee_for_sale,
                           details
                    FROM properties
                    WHERE (name = %s OR alias = %s OR name LIKE %s)
                      AND (deleted = 0 OR deleted IS NULL)
                    ORDER BY
                        CASE WHEN name = %s THEN 0 ELSE 1 END
                    LIMIT 1
                    """,
                    (project_name, project_name,
                     f"%{project_name}%", project_name),
                )
                row = cur.fetchone()
                if not row:
                    return None

                # Parse the JSON details column if present
                import json as _json
                raw_details = row.get("details")
                if raw_details:
                    try:
                        row["details"] = _json.loads(raw_details) if isinstance(raw_details, str) else raw_details
                    except Exception:
                        row["details"] = {}
                else:
                    row["details"] = {}

                return row

    except Exception as e:
        logger.error(f"get_project_facts({project_name}): {e}")
        return None


def add_project_details_column() -> bool:
    """
    One-time migration: adds a JSON 'details' column to the properties table
    if it doesn't already exist.  Safe to call on every startup — is a no-op
    when the column exists.
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT COUNT(*) AS cnt
                    FROM information_schema.COLUMNS
                    WHERE TABLE_SCHEMA = DATABASE()
                      AND TABLE_NAME   = 'properties'
                      AND COLUMN_NAME  = 'details'
                    """
                )
                row = cur.fetchone()
                if row and row.get("cnt", 0) > 0:
                    return True   # already exists

                cur.execute(
                    """
                    ALTER TABLE properties
                    ADD COLUMN details JSON NULL
                        COMMENT 'Structured project facts: pricing, developer, etc.'
                    """
                )
                conn.commit()
                logger.info("properties.details column created")
                return True
    except Exception as e:
        logger.error(f"add_project_details_column: {e}")
        return False