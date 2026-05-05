"""
Batch Call Trigger
Dispatches the Hookfish voice agent to call multiple phone numbers sequentially.
Each call is spaced out by a configurable delay.

Usage:
  python batch_call.py                                  # Call default batch list as buyer
  python batch_call.py broker                           # Call batch list as broker
  python batch_call.py broker --name TestUser           # Call batch list as broker with name override
  python batch_call.py --delay 30                       # Set delay between calls (default: 10s)
"""

import asyncio
import os
import sys
import random
import json
import time
from dotenv import load_dotenv
from livekit import api

from db_helper import (
    lookup_lead_by_phone,
    lookup_customer_by_phone,
    check_call_allowed,
)

load_dotenv()

# ============================================================
# Batch Configuration
# ============================================================

# Add your batch numbers here
BATCH_NUMBERS = [
    "+916362185137",
    "+919930221107",
]

# Default delay between calls (in seconds)
DEFAULT_DELAY = 10

# Agent name (must match the agent_name in voice_agent.py)
AGENT_NAME = "hookfish-voice-agent"

# Default contact type
CONTACT_TYPE = "buyer"

# ============================================================


async def trigger_single_call(phone_number: str, contact_type: str, caller_name: str = None):
    """Trigger an outbound call to a single phone number. Returns True if dispatched."""

    print(f"\n{'─' * 50}")
    print(f"  Calling: {phone_number}")
    print(f"{'─' * 50}")

    # ---- Pre-Call Validation ----
    print(f"  [VALIDATE] Checking if call is allowed...")

    call_check = check_call_allowed(phone_number)
    if not call_check["allowed"]:
        print(f"  [BLOCKED] {call_check['reason']}")
        return False

    print(f"  [OK] Call allowed. Attempts today: {call_check['attempts_today']}")

    # Look up name from DB if not provided
    resolved_name = caller_name
    if not resolved_name:
        customer = lookup_customer_by_phone(phone_number)
        if customer and customer.get("name"):
            resolved_name = customer["name"].strip()
        else:
            leads = lookup_lead_by_phone(phone_number)
            if leads and leads[0].get("partner_name"):
                resolved_name = leads[0]["partner_name"].strip()

    if not resolved_name:
        print(f"  [BLOCKED] No contact name found for {phone_number}.")
        print(f"  Fix: Use --name flag or add contact to DB.")
        return False

    print(f"  [OK] Contact name: {resolved_name}")

    # ---- Dispatch the Call ----
    lkapi = api.LiveKitAPI(
        url=os.getenv("LIVEKIT_URL"),
        api_key=os.getenv("LIVEKIT_API_KEY"),
        api_secret=os.getenv("LIVEKIT_API_SECRET"),
    )

    room_name = f"call-{''.join(str(random.randint(0, 9)) for _ in range(10))}"

    metadata = json.dumps({
        "phone_number": phone_number,
        "contact_type": contact_type,
        "caller_name": resolved_name,
    })

    try:
        dispatch = await lkapi.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(
                agent_name=AGENT_NAME,
                room=room_name,
                metadata=metadata,
            )
        )
        print(f"  [OK] Call dispatched! Room: {room_name}")
        return True
    except Exception as e:
        print(f"  [ERROR] Failed to dispatch: {e}")
        return False
    finally:
        await lkapi.aclose()


async def run_batch(numbers: list, contact_type: str, caller_name: str = None, delay: int = DEFAULT_DELAY):
    """Run batch calls sequentially with a delay between each."""

    total = len(numbers)
    dispatched = 0
    failed = 0
    blocked = 0

    print(f"\n{'═' * 50}")
    print(f"  Hookfish Voice Agent - BATCH CALL")
    print(f"{'═' * 50}")
    print(f"  Numbers:  {total}")
    print(f"  Type:     {contact_type}")
    print(f"  Name:     {caller_name or 'from DB'}")
    print(f"  Delay:    {delay}s between calls")
    print(f"{'═' * 50}")

    for i, phone in enumerate(numbers):
        print(f"\n  [{i + 1}/{total}] Processing {phone}...")

        success = await trigger_single_call(phone, contact_type, caller_name)

        if success:
            dispatched += 1
        else:
            failed += 1

        # Wait between calls (skip delay after the last call)
        if i < total - 1:
            print(f"\n  ⏳ Waiting {delay}s before next call...")
            await asyncio.sleep(delay)

    # ---- Summary ----
    print(f"\n{'═' * 50}")
    print(f"  BATCH COMPLETE")
    print(f"{'═' * 50}")
    print(f"  Total:      {total}")
    print(f"  Dispatched: {dispatched}")
    print(f"  Failed:     {failed}")
    print(f"{'═' * 50}\n")


if __name__ == "__main__":
    ctype = CONTACT_TYPE
    cname = None
    delay = DEFAULT_DELAY

    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--name" and i + 1 < len(args):
            cname = args[i + 1]
            i += 2
        elif args[i] == "--delay" and i + 1 < len(args):
            delay = int(args[i + 1])
            i += 2
        elif args[i] in ("buyer", "broker"):
            ctype = args[i]
            i += 1
        else:
            i += 1

    asyncio.run(run_batch(BATCH_NUMBERS, ctype, cname, delay))
