"""
Outbound Call Trigger
Dispatches the Hookfish voice agent to call a phone number.
It also validates DNC, daily limits, and contact name before calling.

Usage:
  python call_trigger.py                              # Call default buyer number
  python call_trigger.py broker                       # Call as broker type (name defaults to Swapnil)
  python call_trigger.py buyer +91xxxxxxxxxx          # Call specific number as buyer
  python call_trigger.py broker +91xxx --name Rahul   # Call as broker with custom name
"""

import asyncio
import os
import sys
import random
import json
from dotenv import load_dotenv
from livekit import api

from db_helper import (
    lookup_lead_by_phone,
    lookup_customer_by_phone,
    check_call_allowed,
)

load_dotenv()

# ============================================================
# Configuration
# ============================================================

# Default phone number (override via command line)
PHONE_NUMBER = "+916362185137"

# Contact type: "buyer" or "broker" (override via command line)
CONTACT_TYPE = "buyer"

# Agent name (must match the agent_name in voice_agent.py)
AGENT_NAME = "hookfish-voice-agent"

# ============================================================


async def trigger_call(phone_number: str, contact_type: str, caller_name: str = None):
    """Trigger an outbound call to the given phone number."""

    print()

    # ---- Pre-Call Validation (Section 8.3 + 6.3) ----
    print(f"[VALIDATE] Checking if call to {phone_number} is allowed...")

    # 1. Check DNC + daily limit
    call_check = check_call_allowed(phone_number)
    if not call_check["allowed"]:
        print(f"   [BLOCKED] {call_check['reason']}")
        print(f"   Call NOT placed. Exiting.")
        return

    print(f"   [OK] Call is allowed. Attempts today: {call_check['attempts_today']}")

    # 2. Look up name from DB if not provided
    resolved_name = caller_name
    if not resolved_name:
        customer = lookup_customer_by_phone(phone_number)
        if customer and customer.get("name"):
            resolved_name = customer["name"].strip()
        else:
            leads = lookup_lead_by_phone(phone_number)
            if leads and leads[0].get("partner_name"):
                resolved_name = leads[0]["partner_name"].strip()

    # 3. Validate name exists (Section 8.3: no call without name)
    if not resolved_name:
        print(f"   [BLOCKED] No contact name found for {phone_number}.")
        print(f"   Rule: Cannot call without a contact name.")
        print(f"   Fix: Use --name flag or add contact to DB.")
        return

    print(f"   [OK] Contact name: {resolved_name}")

    # ---- Database Lookup ----
    print()
    print(f"[LOOKUP] Looking up {phone_number} in database...")

    customer = lookup_customer_by_phone(phone_number)
    if customer:
        print(f"   [OK] Found customer: {customer['name']} (ID: {customer['id']})")
    else:
        print(f"   [INFO] No customer record found for this number.")

    leads = lookup_lead_by_phone(phone_number)
    if leads:
        print(f"   [OK] Found {len(leads)} leads:")
        for lead in leads:
            print(f"      - {lead['customer_name']} -> {lead['property_name']} (Status: {lead['status']})")
    else:
        print(f"   [INFO] No leads found for this number.")

    print()

    # ---- Dispatch the Call ----
    lkapi = api.LiveKitAPI(
        url=os.getenv("LIVEKIT_URL"),
        api_key=os.getenv("LIVEKIT_API_KEY"),
        api_secret=os.getenv("LIVEKIT_API_SECRET"),
    )

    room_name = f"call-{''.join(str(random.randint(0, 9)) for _ in range(10))}"

    # Build metadata with contact_type and caller_name
    metadata = json.dumps({
        "phone_number": phone_number,
        "contact_type": contact_type,
        "caller_name": resolved_name,
    })

    print(f"[CALL] Dispatching agent to call {phone_number}...")
    print(f"   Room: {room_name}")
    print(f"   Contact Type: {contact_type}")
    print(f"   Contact Name: {resolved_name}")

    try:
        dispatch = await lkapi.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(
                agent_name=AGENT_NAME,
                room=room_name,
                metadata=metadata,
            )
        )
        print(f"[OK] Call dispatched successfully!")
        print(f"   Dispatch: {dispatch}")
        print(f"   The agent will now call {phone_number} as a {contact_type}...")
    except Exception as e:
        print(f"[ERROR] Error dispatching call: {e}")
    finally:
        await lkapi.aclose()


if __name__ == "__main__":

    # Parse command line arguments
    phone = PHONE_NUMBER
    ctype = CONTACT_TYPE
    cname = None  # caller name override

    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--name" and i + 1 < len(args):
            cname = args[i + 1]
            i += 2
        elif args[i] in ("buyer", "broker"):
            ctype = args[i]
            i += 1
        elif args[i].startswith("+"):
            phone = args[i]
            i += 1
        else:
            i += 1

    print(f"=" * 50)
    print(f"Hookfish Voice Agent - Call Trigger")
    print(f"=" * 50)
    print(f"  Phone:  {phone}")
    print(f"  Type:   {ctype}")
    print(f"  Name:   {cname or 'from DB'}")
    print(f"=" * 50)

    asyncio.run(trigger_call(phone, ctype, cname))
