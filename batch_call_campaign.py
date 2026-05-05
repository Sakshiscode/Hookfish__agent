"""
Batch Call Campaign Runner
==========================
This script pulls a batch of leads (or brokers) from the database
and automatically dispatches outbound calls to them.

Usage:
  python batch_call_campaign.py --dry-run     # See who would be called without actually calling
  python batch_call_campaign.py --limit 5     # Call the next 5 eligible leads
  python batch_call_campaign.py --project "Sunrise Charkop" # Only call leads for this project
"""

import asyncio
import os
import sys
import json
import time
from dotenv import load_dotenv
from livekit import api

from db_helper import (
    get_connection,
    check_call_allowed,
)

load_dotenv()

AGENT_NAME = "hookfish-voice-agent"

def fetch_campaign_leads(limit=5, project_filter=None):
    """Fetch eligible leads from the database."""
    leads = []
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            query = """
                SELECT id, partner_phone, customer_phone, partner_name, customer_name, property_name 
                FROM all_leads 
                WHERE deleted = 0 AND status != 'closed'
            """
            params = []
            
            if project_filter:
                query += " AND property_name LIKE %s"
                params.append(f"%{project_filter}%")
                
            query += " ORDER BY created_at DESC LIMIT %s"
            params.append(limit)
            
            cur.execute(query, tuple(params))
            leads = cur.fetchall()
        conn.close()
    except Exception as e:
        print(f"[ERROR] Database error: {e}")
    
    return leads

async def dispatch_call(lkapi, phone_number, contact_type, caller_name, target_project):
    """Dispatch a single call via LiveKit."""
    import random
    room_name = f"call-{int(time.time())}-{''.join(str(random.randint(0, 9)) for _ in range(4))}"
    
    metadata = json.dumps({
        "phone_number": phone_number,
        "contact_type": contact_type,
        "caller_name": caller_name,
        "target_project": target_project,
    })

    try:
        dispatch = await lkapi.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(
                agent_name=AGENT_NAME,
                room=room_name,
                metadata=metadata,
            )
        )
        print(f"   [OK] Dispatched to {phone_number} (Room: {room_name})")
        return True
    except Exception as e:
        print(f"   [ERROR] Failed to dispatch {phone_number}: {e}")
        return False

async def run_batch(limit, project_filter, dry_run):
    print("=" * 60)
    print("Hookfish Batch Call Campaign")
    print("=" * 60)
    print(f"Mode: {'DRY RUN' if dry_run else 'LIVE CALLING'}")
    print(f"Limit: {limit} calls")
    print(f"Project Filter: {project_filter or 'None'}")
    print("-" * 60)

    leads = fetch_campaign_leads(limit, project_filter)
    if not leads:
        print("No eligible leads found.")
        return

    print(f"Found {len(leads)} leads. Validating...")
    
    valid_calls = []
    for lead in leads:
        # Determine phone and name (prioritize partner/broker, fallback to customer)
        phone = lead.get('partner_phone') or lead.get('customer_phone')
        name = lead.get('partner_name') or lead.get('customer_name')
        contact_type = "broker" if lead.get('partner_phone') else "buyer"
        project = lead.get('property_name')
        
        if not phone:
            continue
            
        # Validate DNC rules and daily limits
        call_check = check_call_allowed(phone)
        if not call_check["allowed"]:
            print(f"[SKIP] {phone} ({name}) - Blocked: {call_check['reason']}")
            continue
            
        valid_calls.append({
            "phone": phone,
            "name": name,
            "type": contact_type,
            "project": project
        })
        print(f"[READY] {phone} ({name}) - Project: {project}")

    if dry_run or not valid_calls:
        print("\nDry run complete. No calls placed.")
        return

    print(f"\nProceeding to call {len(valid_calls)} valid contacts by waiting 10 seconds between dispatches...")
    
    lkapi = api.LiveKitAPI(
        url=os.getenv("LIVEKIT_URL"),
        api_key=os.getenv("LIVEKIT_API_KEY"),
        api_secret=os.getenv("LIVEKIT_API_SECRET"),
    )
    
    try:
        success_count = 0
        for i, call in enumerate(valid_calls):
            print(f"\nDispatching {i+1}/{len(valid_calls)}: {call['name']} ({call['phone']})")
            success = await dispatch_call(
                lkapi, 
                call["phone"], 
                call["type"], 
                call["name"], 
                call["project"]
            )
            if success:
                success_count += 1
            
            # Wait 10 seconds between dispatches to avoid throttling and SIP bursts
            if i < len(valid_calls) - 1:
                await asyncio.sleep(10)
                
        print(f"\nCampaign Complete! Successfully dispatched {success_count}/{len(valid_calls)} calls.")
    finally:
        await lkapi.aclose()


if __name__ == "__main__":
    args = sys.argv[1:]
    is_dry_run = "--dry-run" in args
    
    limit = 5
    if "--limit" in args:
        idx = args.index("--limit")
        if idx + 1 < len(args):
            limit = int(args[idx + 1])
            
    project_filter = None
    if "--project" in args:
        idx = args.index("--project")
        if idx + 1 < len(args):
            project_filter = args[idx + 1]

    asyncio.run(run_batch(limit, project_filter, is_dry_run))
