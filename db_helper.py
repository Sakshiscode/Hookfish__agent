# db_helper.py — Database helper for Hookfish Voice Agent

import os
import logging
import pymysql
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("voice-agent-db")


def get_connection():
    """Create and return a new database connection."""
    return pymysql.connect(
        host=os.getenv("DB_HOST"),
        port=int(os.getenv("DB_PORT")),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        database=os.getenv("DB_NAME"),
        ssl={"ssl": {}},
        cursorclass=pymysql.cursors.DictCursor,  # Return rows as dicts
    )


def lookup_customer_by_phone(phone: str) -> dict | None:
    """
    Look up a customer by phone number.
    Returns customer info dict or None if not found.
    """
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, phone, origin, created_at FROM customers WHERE phone = %s AND deleted = 0 LIMIT 1",
                (phone,),
            )
            result = cur.fetchone()
        conn.close()
        return result
    except Exception as e:
        logger.error(f"Error looking up customer by phone {phone}: {e}")
        return None


def lookup_lead_by_phone(phone: str) -> list[dict]:
    """
    Look up all leads associated with a phone number (via partner_phone or customer info).
    Returns a list of lead dicts.
    """
    try:
        conn = get_connection()
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
            results = cur.fetchall()
        conn.close()
        return results
    except Exception as e:
        logger.error(f"Error looking up leads by phone {phone}: {e}")
        return []


def lookup_property_by_name(property_name: str) -> dict | None:
    """
    Look up a property by name (partial match).
    Returns property info dict or None.
    """
    try:
        conn = get_connection()
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
            results = cur.fetchall()
        conn.close()
        return results
    except Exception as e:
        logger.error(f"Error looking up property '{property_name}': {e}")
        return None


def get_leads_for_partner(partner_id: int) -> list[dict]:
    """
    Get all leads assigned to a specific partner/broker.
    """
    try:
        conn = get_connection()
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
            results = cur.fetchall()
        conn.close()
        return results
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
        conn = get_connection()
        with conn.cursor() as cur:
            conv_id = str(uuid.uuid4())
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
        conn.close()
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
        conn = get_connection()
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
        conn.close()
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
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM agent_dnc_list WHERE phone_number = %s LIMIT 1",
                (phone,),
            )
            result = cur.fetchone()
        conn.close()
        return result is not None
    except Exception as e:
        logger.error(f"Error checking DNC for {phone}: {e}")
        return False


def mark_dnc(phone: str, reason: str = "user_requested", added_by: str = "agent") -> bool:
    """Add a phone number to the DNC list."""
    try:
        conn = get_connection()
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
        conn.close()
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
        conn = get_connection()
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
        conn.close()
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
        conn = get_connection()
        with conn.cursor() as cur:
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
                cur.execute(sql, tuple(values))
                conn.commit()

        conn.close()
        logger.info(f"Updated call log {log_id}")
    except Exception as e:
        logger.error(f"Error updating call log {log_id}: {e}")


def save_call_transcript(log_id: str, transcript: str):
    """Save or update the full transcript for a call log."""
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE agent_call_logs SET transcript = %s WHERE id = %s",
                (transcript, log_id),
            )
            conn.commit()
        conn.close()
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
        conn = get_connection()
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
        conn.close()

        if result and result["attempt_count"] >= 1:
            return {
                "allowed": False,
                "attempts_today": result["attempt_count"],
                "reason": "Max 1 call per day reached",
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
        conn = get_connection()
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
        conn.close()
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
        conn = get_connection()
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

        conn.close()
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

        conn = get_connection()
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
        conn.close()
        logger.info(f"Created meeting {meeting_id} for {phone_number}")
        return meeting_id
    except Exception as e:
        logger.error(f"Error creating meeting for {phone_number}: {e}")
        return None


def update_meeting_status(meeting_id: str, status: str, notes: str = None):
    """Update meeting status (scheduled/confirmed/completed/cancelled/no_show)."""
    try:
        conn = get_connection()
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
        conn.close()
        logger.info(f"Updated meeting {meeting_id} to status: {status}")
    except Exception as e:
        logger.error(f"Error updating meeting {meeting_id}: {e}")


def update_meeting_calendar(meeting_id: str, calendar_event_id: str, calendar_invite_sent: bool = True):
    """Update a meeting record with the Google Calendar event ID."""
    try:
        conn = get_connection()
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
        conn.close()
        logger.info(f"Updated meeting {meeting_id} with calendar event {calendar_event_id}")
    except Exception as e:
        logger.error(f"Error updating meeting calendar for {meeting_id}: {e}")


def get_manager_email(manager_id: int) -> str | None:
    """Get a manager's email by their ID."""
    if not manager_id:
        return None
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT email FROM agent_managers WHERE id = %s LIMIT 1",
                (manager_id,),
            )
            result = cur.fetchone()
        conn.close()
        return result["email"] if result and result.get("email") else None
    except Exception as e:
        logger.error(f"Error getting manager email for ID {manager_id}: {e}")
        return None


def get_meetings_for_phone(phone: str) -> list:
    """Get all meetings for a phone number."""
    try:
        conn = get_connection()
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
            results = cur.fetchall()
        conn.close()
        return results
    except Exception as e:
        logger.error(f"Error getting meetings for {phone}: {e}")
        return []
