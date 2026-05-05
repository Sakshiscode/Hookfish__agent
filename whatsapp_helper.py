# whatsapp_helper.py — WhatsApp Business API Integration for Hookfish Voice Agent
# ==================================================================================
# Uses Meta's WhatsApp Cloud API to send meeting/site visit details to contacts.
#
# Setup:
#   1. Create a Meta Developer account → https://developers.facebook.com
#   2. Create a Business App → Add "WhatsApp" product
#   3. In WhatsApp > API Setup:
#      - Get your Phone Number ID
#      - Get your permanent access token (System User token recommended)
#   4. Create Message Templates in WhatsApp Manager:
#      - Template: "meeting_confirmation" (for site visits / meetings)
#      - Template: "project_details" (for sending project brochure info)
#   5. Set env vars: WHATSAPP_TOKEN, WHATSAPP_PHONE_NUMBER_ID
#
# Note: For testing, you can use the temporary token from Meta's API Setup page.
#       For production, create a System User and generate a permanent token.
# ==================================================================================

import os
import logging
import json
import requests
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("voice-agent-whatsapp")

# ============================================================
# Configuration
# ============================================================

# Meta WhatsApp Cloud API base URL
WHATSAPP_API_VERSION = "v21.0"
WHATSAPP_API_BASE = f"https://graph.facebook.com/{WHATSAPP_API_VERSION}"

# Your WhatsApp Business Account Phone Number ID (from Meta Developer Console)
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")

# Permanent Access Token (System User token recommended for production)
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "")

# Business display name (shown in messages)
BUSINESS_NAME = os.getenv("WHATSAPP_BUSINESS_NAME", "Hookfish")

# Default country code for phone numbers
DEFAULT_COUNTRY_CODE = "91"


# ============================================================
# Phone Number Formatting
# ============================================================

def format_phone_for_whatsapp(phone: str) -> str:
    """
    Format a phone number for WhatsApp API (must be in international format without '+').
    Examples:
        +919876543210 → 919876543210
        09876543210   → 919876543210
        9876543210    → 919876543210
    """
    if not phone:
        return ""

    # Remove all non-digit characters
    digits = "".join(c for c in phone if c.isdigit())

    # If starts with 0, remove it
    if digits.startswith("0"):
        digits = digits[1:]

    # If 10 digits, assume Indian number and prepend country code
    if len(digits) == 10:
        digits = DEFAULT_COUNTRY_CODE + digits

    return digits


# ============================================================
# Core WhatsApp API Functions
# ============================================================

def _get_headers() -> dict:
    """Get authorization headers for WhatsApp API."""
    return {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }


def _send_whatsapp_request(payload: dict) -> dict:
    """
    Send a request to the WhatsApp Cloud API.
    Returns the API response as a dict.
    """
    if not WHATSAPP_TOKEN:
        logger.error("WHATSAPP_TOKEN is not set. Cannot send WhatsApp message.")
        return {"success": False, "error": "WHATSAPP_TOKEN not configured"}

    if not WHATSAPP_PHONE_NUMBER_ID:
        logger.error("WHATSAPP_PHONE_NUMBER_ID is not set. Cannot send WhatsApp message.")
        return {"success": False, "error": "WHATSAPP_PHONE_NUMBER_ID not configured"}

    url = f"{WHATSAPP_API_BASE}/{WHATSAPP_PHONE_NUMBER_ID}/messages"

    try:
        response = requests.post(url, headers=_get_headers(), json=payload, timeout=30)
        result = response.json()

        if response.status_code == 200:
            message_id = result.get("messages", [{}])[0].get("id", "")
            logger.info(f"WhatsApp message sent successfully. Message ID: {message_id}")
            return {
                "success": True,
                "message_id": message_id,
                "response": result,
            }
        else:
            error_msg = result.get("error", {}).get("message", "Unknown error")
            error_code = result.get("error", {}).get("code", "")
            logger.error(f"WhatsApp API error ({error_code}): {error_msg}")
            return {
                "success": False,
                "error": error_msg,
                "error_code": error_code,
                "response": result,
            }

    except requests.exceptions.Timeout:
        logger.error("WhatsApp API request timed out")
        return {"success": False, "error": "Request timed out"}
    except requests.exceptions.RequestException as e:
        logger.error(f"WhatsApp API request failed: {e}")
        return {"success": False, "error": str(e)}


# ============================================================
# Template-Based Messages (Recommended for Business API)
# ============================================================

def send_template_message(
    to_phone: str,
    template_name: str,
    language_code: str = "en",
    components: list = None,
) -> dict:
    """
    Send a pre-approved WhatsApp template message.

    Args:
        to_phone: Recipient phone number (any format)
        template_name: Name of the approved template
        language_code: Language code (e.g., 'en', 'hi')
        components: Template components (header, body, button parameters)

    Returns:
        API response dict with success status
    """
    formatted_phone = format_phone_for_whatsapp(to_phone)

    payload = {
        "messaging_product": "whatsapp",
        "to": formatted_phone,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": language_code},
        },
    }

    if components:
        payload["template"]["components"] = components

    logger.info(f"Sending template '{template_name}' to {formatted_phone}")
    return _send_whatsapp_request(payload)


# ============================================================
# Free-Form Text Messages (Only after user initiates / 24hr window)
# ============================================================

def send_text_message(to_phone: str, message: str, preview_url: bool = False) -> dict:
    """
    Send a free-form text message via WhatsApp.

    Note: This only works within the 24-hour messaging window
    (i.e., after the user has messaged you first, or replied to a template).

    Args:
        to_phone: Recipient phone number (any format)
        message: The text message to send
        preview_url: Whether to show URL previews in the message

    Returns:
        API response dict with success status
    """
    formatted_phone = format_phone_for_whatsapp(to_phone)

    payload = {
        "messaging_product": "whatsapp",
        "to": formatted_phone,
        "type": "text",
        "text": {
            "preview_url": preview_url,
            "body": message,
        },
    }

    logger.info(f"Sending text message to {formatted_phone}")
    return _send_whatsapp_request(payload)


# ============================================================
# Interactive Messages (Buttons, Lists)
# ============================================================

def send_interactive_buttons(
    to_phone: str,
    body_text: str,
    buttons: list[dict],
    header_text: str = "",
    footer_text: str = "",
) -> dict:
    """
    Send an interactive message with action buttons.

    Args:
        to_phone: Recipient phone number
        body_text: Main message body
        buttons: List of button dicts, each with 'id' and 'title' (max 3 buttons)
        header_text: Optional header text
        footer_text: Optional footer text

    Returns:
        API response dict
    """
    formatted_phone = format_phone_for_whatsapp(to_phone)

    interactive = {
        "type": "button",
        "body": {"text": body_text},
        "action": {
            "buttons": [
                {
                    "type": "reply",
                    "reply": {"id": btn["id"], "title": btn["title"][:20]},
                }
                for btn in buttons[:3]  # Max 3 buttons
            ]
        },
    }

    if header_text:
        interactive["header"] = {"type": "text", "text": header_text}

    if footer_text:
        interactive["footer"] = {"text": footer_text}

    payload = {
        "messaging_product": "whatsapp",
        "to": formatted_phone,
        "type": "interactive",
        "interactive": interactive,
    }

    logger.info(f"Sending interactive buttons to {formatted_phone}")
    return _send_whatsapp_request(payload)


# ============================================================
# High-Level Functions: Meeting & Schedule Messages
# ============================================================

def send_meeting_confirmation(
    to_phone: str,
    contact_name: str,
    meeting_type: str,
    meeting_date: str,
    meeting_time: str,
    project_name: str = "",
    location: str = "",
    manager_name: str = "",
    manager_phone: str = "",
    notes: str = "",
) -> dict:
    """
    Send meeting/site visit confirmation details via WhatsApp.

    This first tries to send a pre-approved template message.
    If the template is not available, it falls back to a free-form text message.

    Args:
        to_phone: Contact's phone number
        contact_name: Contact's name
        meeting_type: Type of meeting (site_visit, office_meeting, call_back)
        meeting_date: Meeting date (e.g., '15 March 2026')
        meeting_time: Meeting time (e.g., '10:00 AM')
        project_name: Project/property name
        location: Meeting location
        manager_name: Assigned manager's name
        manager_phone: Manager's phone number
        notes: Additional notes

    Returns:
        API response dict with success status and message details
    """
    # ---- Build the message text ----
    type_label = {
        "site_visit": "🏠 Site Visit",
        "office_meeting": "🏢 Office Meeting",
        "call_back": "📞 Call Back",
    }.get(meeting_type, f"📋 {meeting_type.replace('_', ' ').title()}")

    lines = [
        f"🎉 *{type_label} Confirmed!*",
        "",
        f"Hello {contact_name}! Your meeting has been scheduled.",
        "",
        f"📅 *Date:* {meeting_date}",
        f"⏰ *Time:* {meeting_time}",
    ]

    if project_name:
        lines.append(f"🏗️ *Project:* {project_name}")

    if location:
        lines.append(f"📍 *Location:* {location}")

    if manager_name:
        lines.append("")
        lines.append(f"👤 *Your Contact Person:*")
        lines.append(f"   {manager_name}")
        if manager_phone:
            lines.append(f"   📱 {manager_phone}")

    lines.extend([
        "",
        "─────────────────",
        f"Powered by {BUSINESS_NAME}",
    ])

    if notes:
        lines.insert(-2, f"\n📝 *Note:* {notes}")

    message_text = "\n".join(lines)

    # ---- Attempt 1: Try template message (works outside 24hr window) ----
    try:
        template_result = send_template_message(
            to_phone=to_phone,
            template_name="meeting_confirmation",
            language_code="en",
            components=[
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": contact_name},
                        {"type": "text", "text": type_label},
                        {"type": "text", "text": f"{meeting_date} at {meeting_time}"},
                        {"type": "text", "text": project_name or "N/A"},
                        {"type": "text", "text": location or "To be shared"},
                        {"type": "text", "text": manager_name or "To be assigned"},
                    ],
                }
            ],
        )

        if template_result.get("success"):
            logger.info(f"Meeting confirmation sent via template to {to_phone}")
            return {
                "success": True,
                "method": "template",
                "message_id": template_result.get("message_id"),
                "message": "Meeting details sent via WhatsApp template",
            }

        # Template failed (might not be approved yet), try free-form
        logger.warning(
            f"Template message failed ({template_result.get('error')}), "
            f"trying free-form text message..."
        )
    except Exception as e:
        logger.warning(f"Template message attempt failed: {e}")

    # ---- Attempt 2: Try free-form text (only works within 24hr window) ----
    text_result = send_text_message(to_phone, message_text)

    if text_result.get("success"):
        logger.info(f"Meeting confirmation sent via text to {to_phone}")
        return {
            "success": True,
            "method": "text",
            "message_id": text_result.get("message_id"),
            "message": "Meeting details sent via WhatsApp text",
        }

    # ---- Both failed ----
    logger.error(f"Failed to send meeting confirmation to {to_phone}")
    return {
        "success": False,
        "method": "none",
        "error": text_result.get("error", "Both template and text message failed"),
        "message": "Could not send WhatsApp message. Details saved in database.",
    }


def send_project_details(
    to_phone: str,
    contact_name: str,
    project_name: str,
    project_location: str = "",
    project_type: str = "",
    key_highlights: list[str] = None,
    price_info: str = "",
    commission_info: str = "",
    contact_type: str = "buyer",
) -> dict:
    """
    Send project/property details via WhatsApp.

    Args:
        to_phone: Contact's phone number
        contact_name: Contact's name
        project_name: Project/property name
        project_location: Location of the project
        project_type: Type of property (residential, commercial, etc.)
        key_highlights: List of key selling points
        price_info: Price or payment plan info
        commission_info: Commission details (for brokers only)
        contact_type: 'buyer' or 'broker'

    Returns:
        API response dict
    """
    lines = [
        f"🏗️ *{project_name}*",
        "",
        f"Hello {contact_name}! Here are the project details you requested:",
        "",
    ]

    if project_location:
        lines.append(f"📍 *Location:* {project_location}")

    if project_type:
        lines.append(f"🏠 *Type:* {project_type}")

    if price_info:
        lines.append(f"💰 *Price/Payment:* {price_info}")

    if key_highlights:
        lines.append("")
        lines.append("✨ *Key Highlights:*")
        for highlight in key_highlights:
            lines.append(f"  • {highlight}")

    # Add commission info for brokers
    if contact_type == "broker" and commission_info:
        lines.append("")
        lines.append(f"💼 *Commission:* {commission_info}")

    lines.extend([
        "",
        "─────────────────",
        f"For more info, call us or reply to this message.",
        f"Powered by {BUSINESS_NAME}",
    ])

    message_text = "\n".join(lines)

    # Try template first, then free-form
    try:
        template_result = send_template_message(
            to_phone=to_phone,
            template_name="project_details",
            language_code="en",
            components=[
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": contact_name},
                        {"type": "text", "text": project_name},
                        {"type": "text", "text": project_location or "Prime Location"},
                        {"type": "text", "text": price_info or "Contact for pricing"},
                    ],
                }
            ],
        )
        if template_result.get("success"):
            return {
                "success": True,
                "method": "template",
                "message_id": template_result.get("message_id"),
                "message": "Project details sent via WhatsApp template",
            }
    except Exception as e:
        logger.warning(f"Template send failed for project details: {e}")

    # Fallback to free-form text
    text_result = send_text_message(to_phone, message_text)

    if text_result.get("success"):
        return {
            "success": True,
            "method": "text",
            "message_id": text_result.get("message_id"),
            "message": "Project details sent via WhatsApp text",
        }

    return {
        "success": False,
        "error": text_result.get("error", "Failed to send project details"),
        "message": "Could not send WhatsApp message.",
    }


def send_schedule_reminder(
    to_phone: str,
    contact_name: str,
    meeting_type: str,
    meeting_date: str,
    meeting_time: str,
    project_name: str = "",
    location: str = "",
) -> dict:
    """
    Send a meeting reminder via WhatsApp (e.g., day before or morning of).

    Args:
        to_phone: Contact's phone number
        contact_name: Contact's name
        meeting_type: Type of meeting
        meeting_date: Meeting date
        meeting_time: Meeting time
        project_name: Project name
        location: Meeting location

    Returns:
        API response dict
    """
    type_label = {
        "site_visit": "site visit",
        "office_meeting": "office meeting",
        "call_back": "call back",
    }.get(meeting_type, meeting_type.replace("_", " "))

    lines = [
        f"📢 *Reminder: Upcoming {type_label.title()}!*",
        "",
        f"Hi {contact_name}!",
        f"Just a friendly reminder about your upcoming {type_label}.",
        "",
        f"📅 *Date:* {meeting_date}",
        f"⏰ *Time:* {meeting_time}",
    ]

    if project_name:
        lines.append(f"🏗️ *Project:* {project_name}")
    if location:
        lines.append(f"📍 *Location:* {location}")

    lines.extend([
        "",
        "Looking forward to seeing you there!",
        "",
        f"For any changes, please reply or call us.",
        f"Powered by {BUSINESS_NAME}",
    ])

    message_text = "\n".join(lines)

    # Try template first
    try:
        template_result = send_template_message(
            to_phone=to_phone,
            template_name="meeting_reminder",
            language_code="en",
            components=[
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": contact_name},
                        {"type": "text", "text": type_label},
                        {"type": "text", "text": f"{meeting_date} at {meeting_time}"},
                        {"type": "text", "text": project_name or "N/A"},
                    ],
                }
            ],
        )
        if template_result.get("success"):
            return {
                "success": True,
                "method": "template",
                "message_id": template_result.get("message_id"),
                "message": "Reminder sent via WhatsApp",
            }
    except Exception as e:
        logger.warning(f"Template send failed for reminder: {e}")

    # Fallback
    text_result = send_text_message(to_phone, message_text)
    return {
        "success": text_result.get("success", False),
        "method": "text" if text_result.get("success") else "none",
        "message_id": text_result.get("message_id"),
        "message": "Reminder sent" if text_result.get("success") else "Failed to send reminder",
    }


# ============================================================
# WhatsApp Message Logging (Database)
# ============================================================

def log_whatsapp_message(
    phone_number: str,
    message_type: str,
    template_name: str = None,
    message_content: str = None,
    whatsapp_message_id: str = None,
    status: str = "sent",
    meeting_id: str = None,
    call_log_id: str = None,
) -> str:
    """
    Log a sent WhatsApp message to the database for tracking.

    Args:
        phone_number: Recipient's phone number
        message_type: Type of message (meeting_confirmation, project_details, reminder)
        template_name: Template name if used
        message_content: The message text content
        whatsapp_message_id: WhatsApp's message ID
        status: Message status (sent, delivered, read, failed)
        meeting_id: Related meeting ID if applicable
        call_log_id: Related call log ID if applicable

    Returns:
        Log entry ID or None on failure
    """
    import uuid
    import pymysql

    log_id = str(uuid.uuid4())
    try:
        conn = pymysql.connect(
            host=os.getenv("DB_HOST"),
            port=int(os.getenv("DB_PORT")),
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASSWORD"),
            database=os.getenv("DB_NAME"),
            ssl={"ssl": {}},
            cursorclass=pymysql.cursors.DictCursor,
        )
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO agent_whatsapp_logs
                    (id, phone_number, message_type, template_name,
                     message_content, whatsapp_message_id, status,
                     meeting_id, call_log_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (log_id, phone_number, message_type, template_name,
                 message_content, whatsapp_message_id, status,
                 meeting_id, call_log_id),
            )
            conn.commit()
        conn.close()
        logger.info(f"Logged WhatsApp message {log_id} to {phone_number}")
        return log_id
    except Exception as e:
        logger.error(f"Error logging WhatsApp message: {e}")
        return None


# ============================================================
# Convenience: Send & Log in One Call
# ============================================================

def send_and_log_meeting_details(
    to_phone: str,
    contact_name: str,
    meeting_type: str,
    meeting_date: str,
    meeting_time: str,
    project_name: str = "",
    location: str = "",
    manager_name: str = "",
    manager_phone: str = "",
    notes: str = "",
    meeting_id: str = None,
    call_log_id: str = None,
) -> dict:
    """
    Send meeting confirmation via WhatsApp and log the result.
    This is the main function to call from the voice agent.

    Returns:
        {
            "success": bool,
            "whatsapp_sent": bool,
            "message": str,
            "message_id": str or None,
            "log_id": str or None,
        }
    """
    # Send the WhatsApp message
    result = send_meeting_confirmation(
        to_phone=to_phone,
        contact_name=contact_name,
        meeting_type=meeting_type,
        meeting_date=meeting_date,
        meeting_time=meeting_time,
        project_name=project_name,
        location=location,
        manager_name=manager_name,
        manager_phone=manager_phone,
        notes=notes,
    )

    # Log the message
    log_id = None
    if result.get("success"):
        log_id = log_whatsapp_message(
            phone_number=to_phone,
            message_type="meeting_confirmation",
            template_name="meeting_confirmation" if result.get("method") == "template" else None,
            message_content=f"Meeting: {meeting_type} | {meeting_date} {meeting_time} | {project_name}",
            whatsapp_message_id=result.get("message_id"),
            status="sent",
            meeting_id=meeting_id,
            call_log_id=call_log_id,
        )
    else:
        log_id = log_whatsapp_message(
            phone_number=to_phone,
            message_type="meeting_confirmation",
            message_content=f"FAILED: {result.get('error', 'unknown')}",
            status="failed",
            meeting_id=meeting_id,
            call_log_id=call_log_id,
        )

    return {
        "success": result.get("success", False),
        "whatsapp_sent": result.get("success", False),
        "message": result.get("message", ""),
        "message_id": result.get("message_id"),
        "log_id": log_id,
    }


def send_and_log_project_details(
    to_phone: str,
    contact_name: str,
    project_name: str,
    project_location: str = "",
    project_type: str = "",
    key_highlights: list[str] = None,
    price_info: str = "",
    commission_info: str = "",
    contact_type: str = "buyer",
    call_log_id: str = None,
) -> dict:
    """
    Send project details via WhatsApp and log the result.

    Returns:
        {
            "success": bool,
            "whatsapp_sent": bool,
            "message": str,
        }
    """
    result = send_project_details(
        to_phone=to_phone,
        contact_name=contact_name,
        project_name=project_name,
        project_location=project_location,
        project_type=project_type,
        key_highlights=key_highlights,
        price_info=price_info,
        commission_info=commission_info,
        contact_type=contact_type,
    )

    log_whatsapp_message(
        phone_number=to_phone,
        message_type="project_details",
        template_name="project_details" if result.get("method") == "template" else None,
        message_content=f"Project: {project_name}",
        whatsapp_message_id=result.get("message_id"),
        status="sent" if result.get("success") else "failed",
        call_log_id=call_log_id,
    )

    return {
        "success": result.get("success", False),
        "whatsapp_sent": result.get("success", False),
        "message": result.get("message", ""),
    }
