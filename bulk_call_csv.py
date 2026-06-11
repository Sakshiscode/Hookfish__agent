"""
Bulk Call CSV Runner
====================
Loads contacts from a CSV file and places parallel outbound calls.

CSV FORMAT (required columns):
  phone       - phone number with country code e.g. +919876543210
  name        - contact name
  type        - buyer or broker (optional, defaults to buyer)
  project     - project name to pitch (optional)

Usage:
  python bulk_call_csv.py contacts.csv                     # Call all contacts
  python bulk_call_csv.py contacts.csv --parallel 10       # 10 calls at a time (default: 5)
  python bulk_call_csv.py contacts.csv --dry-run           # Preview without calling
  python bulk_call_csv.py contacts.csv --parallel 5 --delay 2  # 5 parallel, 2s between batches

Example CSV:
  phone,name,type,project
  +919876543210,Rahul Sharma,buyer,माणिक्य
  +918765432109,Priya Mehta,buyer,माणिक्य
  +917654321098,Amit Broker,broker,माणिक्य
"""

import asyncio
import csv
import os
import sys
import json
import time
import random
from dotenv import load_dotenv
from livekit import api

load_dotenv()

AGENT_NAME = "hookfish-voice-agent"
DEFAULT_PARALLEL = 5      # calls dispatched simultaneously
DEFAULT_DELAY = 2         # seconds between batches
DEFAULT_PROJECT = "माणिक्य"
DEFAULT_TYPE = "buyer"


# ============================================================
# Load CSV
# ============================================================

def load_csv(filepath: str) -> list:
    """Load and validate contacts from CSV file."""
    contacts = []
    errors = []

    if not os.path.exists(filepath):
        print(f"[ERROR] File not found: {filepath}")
        sys.exit(1)

    with open(filepath, newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)

        # Normalize column names to lowercase
        if reader.fieldnames:
            reader.fieldnames = [h.strip().lower() for h in reader.fieldnames]

        for i, row in enumerate(reader, start=2):
            phone = (row.get('phone') or row.get('mobile') or row.get('number') or '').strip()
            name  = (row.get('name')  or row.get('contact') or '').strip()
            ctype = (row.get('type')  or row.get('contact_type') or DEFAULT_TYPE).strip().lower()
            proj  = (row.get('project') or row.get('property') or DEFAULT_PROJECT).strip()

            if not phone:
                errors.append(f"Row {i}: missing phone number")
                continue
            if not name:
                errors.append(f"Row {i}: missing name for {phone}")
                continue

            # Normalize phone — add +91 if missing country code
            if not phone.startswith('+'):
                if len(phone) == 10:
                    phone = '+91' + phone
                else:
                    phone = '+' + phone

            if ctype not in ('buyer', 'broker'):
                ctype = DEFAULT_TYPE

            contacts.append({
                'phone':   phone,
                'name':    name,
                'type':    ctype,
                'project': proj,
            })

    if errors:
        print(f"\n[WARNINGS] {len(errors)} rows skipped:")
        for e in errors:
            print(f"  - {e}")

    return contacts


# ============================================================
# DNC + Daily limit check
# ============================================================

def is_call_allowed(phone: str) -> tuple[bool, str]:
    """Check DNC and daily call limit. Returns (allowed, reason)."""
    try:
        from db_helper import check_call_allowed
        result = check_call_allowed(phone)
        return result.get('allowed', True), result.get('reason', 'OK')
    except Exception as e:
        # If DB check fails, allow the call (don't block on DB errors)
        return True, f"DB check skipped: {e}"


# ============================================================
# Dispatch single call
# ============================================================

async def dispatch_call(lkapi, contact: dict) -> tuple[bool, str]:
    """Dispatch one outbound call. Returns (success, message)."""
    phone   = contact['phone']
    name    = contact['name']
    ctype   = contact['type']
    project = contact['project']

    room_name = f"call-{int(time.time() * 1000)}-{random.randint(1000, 9999)}"

    metadata = json.dumps({
        "phone_number":   phone,
        "contact_type":   ctype,
        "caller_name":    name,
        "target_project": project,
    })

    try:
        await lkapi.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(
                agent_name=AGENT_NAME,
                room=room_name,
                metadata=metadata,
            )
        )
        return True, room_name
    except Exception as e:
        return False, str(e)


# ============================================================
# Main batch runner
# ============================================================

async def run_bulk(contacts: list, parallel: int, delay: float, dry_run: bool):
    print(f"\n{'═' * 60}")
    print(f"  Hookfish Bulk Call Runner")
    print(f"{'═' * 60}")
    print(f"  Total contacts : {len(contacts)}")
    print(f"  Parallel calls : {parallel}")
    print(f"  Batch delay    : {delay}s")
    print(f"  Mode           : {'DRY RUN' if dry_run else 'LIVE'}")
    print(f"{'═' * 60}\n")

    # ---- Validate contacts ----
    print("Validating contacts...")
    valid = []
    skipped = 0

    for c in contacts:
        allowed, reason = is_call_allowed(c['phone'])
        if allowed:
            valid.append(c)
            print(f"  [READY]  {c['phone']:>15}  {c['name']:<20}  {c['type']}")
        else:
            skipped += 1
            print(f"  [SKIP]   {c['phone']:>15}  {c['name']:<20}  {reason}")

    print(f"\n  Valid: {len(valid)}  |  Skipped: {skipped}\n")

    if not valid:
        print("No valid contacts to call.")
        return

    if dry_run:
        print("Dry run complete. No calls placed.")
        return

    # ---- Confirm before calling ----
    confirm = input(f"Place {len(valid)} calls now? [y/N] ").strip().lower()
    if confirm != 'y':
        print("Aborted.")
        return

    # ---- Dispatch in parallel batches ----
    lkapi = api.LiveKitAPI(
        url=os.getenv("LIVEKIT_URL"),
        api_key=os.getenv("LIVEKIT_API_KEY"),
        api_secret=os.getenv("LIVEKIT_API_SECRET"),
    )

    success_count = 0
    fail_count    = 0
    total_batches = (len(valid) + parallel - 1) // parallel

    try:
        for batch_num, i in enumerate(range(0, len(valid), parallel), start=1):
            batch = valid[i:i + parallel]

            print(f"\nBatch {batch_num}/{total_batches} — dispatching {len(batch)} calls...")

            tasks = [dispatch_call(lkapi, c) for c in batch]
            results = await asyncio.gather(*tasks)

            for contact, (ok, msg) in zip(batch, results):
                if ok:
                    success_count += 1
                    print(f"  [OK]    {contact['phone']}  {contact['name']}  →  room: {msg}")
                else:
                    fail_count += 1
                    print(f"  [FAIL]  {contact['phone']}  {contact['name']}  →  {msg}")

            # Wait between batches (skip after last batch)
            if i + parallel < len(valid):
                print(f"  Waiting {delay}s before next batch...")
                await asyncio.sleep(delay)

    finally:
        await lkapi.aclose()

    # ---- Summary ----
    print(f"\n{'═' * 60}")
    print(f"  BULK CALL COMPLETE")
    print(f"{'═' * 60}")
    print(f"  Dispatched : {success_count}")
    print(f"  Failed     : {fail_count}")
    print(f"  Skipped    : {skipped}")
    print(f"  Total      : {len(contacts)}")
    print(f"{'═' * 60}\n")


# ============================================================
# CLI
# ============================================================

if __name__ == "__main__":
    args = sys.argv[1:]

    if not args or args[0].startswith('--'):
        print("Usage: python bulk_call_csv.py <contacts.csv> [--parallel N] [--delay N] [--dry-run]")
        print("\nExample CSV format:")
        print("  phone,name,type,project")
        print("  +919876543210,Rahul Sharma,buyer,माणिक्य")
        sys.exit(1)

    csv_file  = args[0]
    parallel  = DEFAULT_PARALLEL
    delay     = DEFAULT_DELAY
    dry_run   = '--dry-run' in args

    if '--parallel' in args:
        idx = args.index('--parallel')
        if idx + 1 < len(args):
            parallel = int(args[idx + 1])

    if '--delay' in args:
        idx = args.index('--delay')
        if idx + 1 < len(args):
            delay = float(args[idx + 1])

    contacts = load_csv(csv_file)
    if not contacts:
        print("No valid contacts found in CSV.")
        sys.exit(1)

    asyncio.run(run_bulk(contacts, parallel, delay, dry_run))