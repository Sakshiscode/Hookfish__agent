"""
Campaign Management API Routes
================================
CRUD + state management for calling campaigns.
"""

import uuid
from datetime import datetime
from fastapi import APIRouter, HTTPException, Query, BackgroundTasks
from pydantic import BaseModel
from typing import Optional
import os
import json
import asyncio
from livekit import api

from api.db import execute_query, execute_insert

router = APIRouter(prefix="/api/campaigns", tags=["Campaigns"])


# ── Pydantic Models ─────────────────────────────────────────────

class CampaignCreate(BaseModel):
    name: str
    description: Optional[str] = None
    contact_list_id: Optional[str] = None
    script_id: Optional[str] = None
    project_name: Optional[str] = None
    scheduled_at: Optional[str] = None


class CampaignUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    contact_list_id: Optional[str] = None
    script_id: Optional[str] = None
    project_name: Optional[str] = None
    scheduled_at: Optional[str] = None


class CampaignAction(BaseModel):
    action: str  # start, pause, resume, stop


# ── Background Task ─────────────────────────────────────────────

async def run_campaign_calls(campaign_id: str):
    """Background task to dispatch LiveKit calls for a campaign."""
    campaign = execute_query(
        "SELECT * FROM agent_campaigns WHERE id = %s",
        (campaign_id,),
        fetch_one=True
    )
    if not campaign or not campaign.get("contact_list_id"):
        return

    contacts = execute_query(
        "SELECT * FROM agent_contacts WHERE list_id = %s",
        (campaign["contact_list_id"],)
    )
    if not contacts:
        return

    lkapi = api.LiveKitAPI(
        url=os.getenv("LIVEKIT_URL"),
        api_key=os.getenv("LIVEKIT_API_KEY"),
        api_secret=os.getenv("LIVEKIT_API_SECRET"),
    )
    
    try:
        for contact in contacts:
            room_name = f"camp-{campaign_id[:8]}-{contact['phone'][-4:]}"
            metadata = json.dumps({
                "campaign_id": campaign_id,
                "phone_number": contact["phone"],
                "caller_name": contact.get('name', 'Customer'),
                "target_project": campaign.get("project_name", "Hookfish Properties"),
                "contact_type": "buyer"
            })
            
            try:
                await lkapi.agent_dispatch.create_dispatch(
                    api.CreateAgentDispatchRequest(
                        agent_name="hookfish-voice-agent",
                        room=room_name,
                        metadata=metadata,
                    )
                )
                print(f"[API] Dispatched call to Voice Agent for {contact['phone']}")
                
                # Increment calls_made counter
                execute_insert(
                    """UPDATE agent_campaigns 
                       SET calls_made = calls_made + 1,
                           calls_connected = calls_connected + 1,
                           connect_rate = ROUND((calls_connected + 1) / (calls_made + 1), 3),
                           updated_at = NOW()
                       WHERE id = %s""",
                    (campaign_id,)
                )
                print(f"[API] Updated campaign counters for {campaign_id}")
                
            except Exception as e:
                print(f"[API] Failed to dispatch for {contact['phone']}: {e}")
                
            await asyncio.sleep(5)  # 5-second delay between calls
        
        # Mark campaign as completed after all contacts processed
        execute_insert(
            "UPDATE agent_campaigns SET status = 'completed', completed_at = NOW() WHERE id = %s",
            (campaign_id,)
        )
        print(f"[API] Campaign {campaign_id} completed")
    finally:
        await lkapi.aclose()


# ── Routes ──────────────────────────────────────────────────────

@router.get("")
async def list_campaigns(
    status: Optional[str] = None,
    limit: int = Query(50, le=100),
    offset: int = 0,
):
    """List all campaigns with optional status filter."""
    if status:
        campaigns = execute_query(
            """
            SELECT c.*,
                   cl.name as contact_list_name,
                   s.name as script_name
            FROM agent_campaigns c
            LEFT JOIN agent_contact_lists cl ON c.contact_list_id = cl.id
            LEFT JOIN agent_scripts s ON c.script_id = s.id
            WHERE c.status = %s
            ORDER BY c.updated_at DESC
            LIMIT %s OFFSET %s
            """,
            (status, limit, offset),
        )
    else:
        campaigns = execute_query(
            """
            SELECT c.*,
                   cl.name as contact_list_name,
                   s.name as script_name
            FROM agent_campaigns c
            LEFT JOIN agent_contact_lists cl ON c.contact_list_id = cl.id
            LEFT JOIN agent_scripts s ON c.script_id = s.id
            ORDER BY c.updated_at DESC
            LIMIT %s OFFSET %s
            """,
            (limit, offset),
        )

    # Get total count
    count = execute_query(
        "SELECT COUNT(*) as total FROM agent_campaigns" +
        (" WHERE status = %s" if status else ""),
        (status,) if status else None,
        fetch_one=True,
    )

    return {
        "campaigns": campaigns or [],
        "total": count["total"] if count else 0,
    }


@router.get("/{campaign_id}")
async def get_campaign(campaign_id: str):
    """Get a single campaign by ID."""
    campaign = execute_query(
        """
        SELECT c.*,
               cl.name as contact_list_name,
               s.name as script_name
        FROM agent_campaigns c
        LEFT JOIN agent_contact_lists cl ON c.contact_list_id = cl.id
        LEFT JOIN agent_scripts s ON c.script_id = s.id
        WHERE c.id = %s
        """,
        (campaign_id,),
        fetch_one=True,
    )
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return campaign


@router.post("")
async def create_campaign(data: CampaignCreate):
    """Create a new campaign."""
    campaign_id = str(uuid.uuid4())

    # Get contact count if list is specified
    total_contacts = 0
    if data.contact_list_id:
        count = execute_query(
            "SELECT COUNT(*) as cnt FROM agent_contacts WHERE list_id = %s",
            (data.contact_list_id,),
            fetch_one=True,
        )
        total_contacts = count["cnt"] if count else 0

    execute_insert(
        """
        INSERT INTO agent_campaigns
            (id, name, description, contact_list_id, script_id,
             project_name, total_contacts, scheduled_at, status)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'draft')
        """,
        (
            campaign_id, data.name, data.description,
            data.contact_list_id, data.script_id,
            data.project_name, total_contacts,
            data.scheduled_at,
        ),
    )

    return {"id": campaign_id, "status": "draft", "message": "Campaign created"}


@router.put("/{campaign_id}")
async def update_campaign(campaign_id: str, data: CampaignUpdate):
    """Update an existing campaign."""
    # Verify campaign exists
    existing = execute_query(
        "SELECT id, status FROM agent_campaigns WHERE id = %s",
        (campaign_id,),
        fetch_one=True,
    )
    if not existing:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if existing["status"] == "running":
        raise HTTPException(status_code=400, detail="Cannot edit a running campaign")

    updates = []
    values = []
    for field, value in data.dict(exclude_unset=True).items():
        if value is not None:
            updates.append(f"{field} = %s")
            values.append(value)

    if updates:
        values.append(campaign_id)
        execute_insert(
            f"UPDATE agent_campaigns SET {', '.join(updates)} WHERE id = %s",
            tuple(values),
        )

    return {"message": "Campaign updated"}


@router.post("/{campaign_id}/action")
async def campaign_action(campaign_id: str, data: CampaignAction, background_tasks: BackgroundTasks):
    """Start, pause, resume, or stop a campaign."""
    campaign = execute_query(
        "SELECT id, status FROM agent_campaigns WHERE id = %s",
        (campaign_id,),
        fetch_one=True,
    )
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    current = campaign["status"]
    action = data.action

    # Validate state transitions
    valid_transitions = {
        "start": ["draft", "scheduled"],
        "pause": ["running"],
        "resume": ["paused"],
        "stop": ["running", "paused"],
    }

    if action not in valid_transitions:
        raise HTTPException(status_code=400, detail=f"Invalid action: {action}")
    if current not in valid_transitions[action]:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot {action} a campaign with status '{current}'",
        )

    new_status_map = {
        "start": "running",
        "pause": "paused",
        "resume": "running",
        "stop": "completed",
    }
    new_status = new_status_map[action]

    # Update status with timestamp
    if action == "start":
        execute_insert(
            "UPDATE agent_campaigns SET status = %s, started_at = NOW() WHERE id = %s",
            (new_status, campaign_id),
        )
    elif action == "stop":
        execute_insert(
            "UPDATE agent_campaigns SET status = %s, completed_at = NOW() WHERE id = %s",
            (new_status, campaign_id),
        )
    else:
        execute_insert(
            "UPDATE agent_campaigns SET status = %s WHERE id = %s",
            (new_status, campaign_id),
        )

    # Trigger actual calls in background if starting/resuming
    if action in ["start", "resume"]:
        background_tasks.add_task(run_campaign_calls, campaign_id)

    return {"id": campaign_id, "status": new_status, "message": f"Campaign {action}ed"}


@router.get("/{campaign_id}/progress")
async def get_campaign_progress(campaign_id: str):
    """Get call progress for a specific campaign."""
    campaign = execute_query(
        "SELECT * FROM agent_campaigns WHERE id = %s",
        (campaign_id,),
        fetch_one=True,
    )
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    return {
        "total_contacts": campaign["total_contacts"],
        "calls_made": campaign["calls_made"],
        "calls_connected": campaign["calls_connected"],
        "calls_converted": campaign["calls_converted"],
        "connect_rate": float(campaign["connect_rate"] or 0),
        "status": campaign["status"],
    }


@router.delete("/{campaign_id}")
async def delete_campaign(campaign_id: str):
    """Delete a campaign (only drafts)."""
    campaign = execute_query(
        "SELECT id, status FROM agent_campaigns WHERE id = %s",
        (campaign_id,),
        fetch_one=True,
    )
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if campaign["status"] not in ("draft", "completed"):
        raise HTTPException(status_code=400, detail="Can only delete draft or completed campaigns")

    execute_insert("DELETE FROM agent_campaigns WHERE id = %s", (campaign_id,))
    return {"message": "Campaign deleted"}
