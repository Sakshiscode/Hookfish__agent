"""
Batch Outbound Call Trigger
Dispatches the Hookfish voice agent to call multiple phone numbers in parallel.
Each call gets its own room + agent, so all calls run concurrently.

Usage:
  python batch_call_trigger.py                        # Call all numbers in BATCH_NUMBERS list
  python batch_call_trigger.py broker                 # Call all as broker type
  python batch_call_trigger.py --delay 5              # 5 second stagger between dispatches
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
# Configuration
# ============================================================

# Batch phone numbers to call
BATCH_NUMBERS = [
    "+917039853851",
    "+918468857601",
    "+919819876103",
    "+919619755450",
]

# Default contact type for all calls (override via command line: "buyer" or "broker")
CONTACT_TYPE = "buyer"

# Stagger delay between dispatches (seconds) — prevents API rate limits
# The calls themselves run fully in parallel; this is just dispatch spacing.
DISPATCH_DELAY = 10

# Agent name (must match the agent_name in voice_agent.py)
AGENT_NAME = "hookfish-voice-agent"

# ============================================================


async def dispatch_single_call(phone_number: str, contact_type: str, call_index: int, total: int):
    """Validate and dispatch a single outbound call. Returns success status."""

    label = f"[{call_index}/{total}]"
    print(f"\n{'─' * 50}")
    print(f"{label} Processing: {phone_number}")
    print(f"{'─' * 50}")

    # ---- Pre-Call Validation ----
    print(f"{label} [VALIDATE] Checking if call is allowed...")

    # 1. Check DNC + daily limit
    call_check = check_call_allowed(phone_number)
    if not call_check["allowed"]:
        print(f"{label} [BLOCKED] {call_check['reason']}")
        return {"phone": phone_number, "status": "blocked", "reason": call_check["reason"]}

    print(f"{label} [OK] Call allowed. Attempts today: {call_check['attempts_today']}")

    # 2. Look up name from DB (optional — fallback to "Sir" if not found)
    resolved_name = None
    customer = lookup_customer_by_phone(phone_number)
    if customer and customer.get("name"):
        resolved_name = customer["name"].strip()
    else:
        leads = lookup_lead_by_phone(phone_number)
        if leads and leads[0].get("partner_name"):
            resolved_name = leads[0]["partner_name"].strip()

    if not resolved_name:
        resolved_name = "Sir"
        print(f"{label} [INFO] No name in DB — using default greeting")
    else:
        print(f"{label} [OK] Contact name: {resolved_name}")

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

    print(f"{label} [CALL] Dispatching to {phone_number} (Room: {room_name})...")

    try:
        dispatch = await lkapi.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(
                agent_name=AGENT_NAME,
                room=room_name,
                metadata=metadata,
            )
        )
        print(f"{label} [OK] ✅ Dispatched! {resolved_name} ({phone_number}) as {contact_type}")
        return {"phone": phone_number, "status": "dispatched", "name": resolved_name, "room": room_name}
    except Exception as e:
        print(f"{label} [ERROR] ❌ Failed: {e}")
        return {"phone": phone_number, "status": "failed", "reason": str(e)}
    finally:
        await lkapi.aclose()


async def run_batch(numbers: list, contact_type: str, delay: int):
    """Dispatch calls to all numbers with a small stagger between dispatches."""

    total = len(numbers)
    print(f"\n{'═' * 60}")
    print(f"  Hookfish Voice Agent - BATCH Call Trigger")
    print(f"{'═' * 60}")
    print(f"  Numbers:       {total}")
    print(f"  Contact Type:  {contact_type}")
    print(f"  Dispatch Delay: {delay}s between each")
    print(f"  Note: All calls run IN PARALLEL once dispatched!")
    print(f"{'═' * 60}")

    results = []
    start_time = time.time()

    for i, phone in enumerate(numbers, 1):
        result = await dispatch_single_call(phone, contact_type, i, total)
        results.append(result)

        # Stagger between dispatches (skip delay after last one)
        if i < total:
            print(f"\n⏳ Waiting {delay}s before next dispatch...")
            await asyncio.sleep(delay)

    elapsed = time.time() - start_time

    # ---- Summary ----
    print(f"\n{'═' * 60}")
    print(f"  BATCH COMPLETE — {total} numbers processed in {elapsed:.1f}s")
    print(f"{'═' * 60}")

    dispatched = [r for r in results if r["status"] == "dispatched"]
    blocked = [r for r in results if r["status"] == "blocked"]
    failed = [r for r in results if r["status"] == "failed"]

    if dispatched:
        print(f"\n  ✅ DISPATCHED ({len(dispatched)}):")
        for r in dispatched:
            print(f"     {r['name']} — {r['phone']} → Room: {r['room']}")

    if blocked:
        print(f"\n  🚫 BLOCKED ({len(blocked)}):")
        for r in blocked:
            print(f"     {r['phone']} — {r['reason']}")

    if failed:
        print(f"\n  ❌ FAILED ({len(failed)}):")
        for r in failed:
            print(f"     {r['phone']} — {r['reason']}")

    print(f"\n{'═' * 60}\n")


if __name__ == "__main__":

    ctype = CONTACT_TYPE
    delay = DISPATCH_DELAY

    # Parse args
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] in ("buyer", "broker"):
            ctype = args[i]
            i += 1
        elif args[i] == "--delay" and i + 1 < len(args):
            delay = int(args[i + 1])
            i += 2
        else:
            i += 1

    asyncio.run(run_batch(BATCH_NUMBERS, ctype, delay))
