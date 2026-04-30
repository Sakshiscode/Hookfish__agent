"""
Scripts API Routes
===================
CRUD for AI call scripts used in campaigns.
"""

import uuid
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional

from api.db import execute_query, execute_insert

router = APIRouter(prefix="/api/scripts", tags=["Scripts"])


class ScriptCreate(BaseModel):
    name: str
    description: Optional[str] = None
    content: Optional[str] = None
    voice_id: Optional[str] = None
    voice_name: Optional[str] = "Riya"
    node_count: Optional[int] = 0


class ScriptUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    content: Optional[str] = None
    voice_id: Optional[str] = None
    voice_name: Optional[str] = None
    node_count: Optional[int] = None
    is_active: Optional[bool] = None


@router.get("")
async def list_scripts(active_only: bool = False):
    """List all scripts."""
    if active_only:
        scripts = execute_query(
            "SELECT * FROM agent_scripts WHERE is_active = 1 ORDER BY updated_at DESC"
        )
    else:
        scripts = execute_query(
            "SELECT * FROM agent_scripts ORDER BY updated_at DESC"
        )
    return {"scripts": scripts or []}


@router.get("/{script_id}")
async def get_script(script_id: str):
    """Get a single script by ID."""
    script = execute_query(
        "SELECT * FROM agent_scripts WHERE id = %s",
        (script_id,),
        fetch_one=True,
    )
    if not script:
        raise HTTPException(status_code=404, detail="Script not found")
    return script


@router.post("")
async def create_script(data: ScriptCreate):
    """Create a new script."""
    script_id = str(uuid.uuid4())
    execute_insert(
        """
        INSERT INTO agent_scripts (id, name, description, content, voice_id, voice_name, node_count)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (script_id, data.name, data.description, data.content,
         data.voice_id, data.voice_name, data.node_count),
    )
    return {"id": script_id, "message": "Script created"}


@router.put("/{script_id}")
async def update_script(script_id: str, data: ScriptUpdate):
    """Update an existing script."""
    existing = execute_query(
        "SELECT id FROM agent_scripts WHERE id = %s",
        (script_id,),
        fetch_one=True,
    )
    if not existing:
        raise HTTPException(status_code=404, detail="Script not found")

    updates = []
    values = []
    for field, value in data.dict(exclude_unset=True).items():
        if value is not None:
            updates.append(f"{field} = %s")
            values.append(value)

    if updates:
        values.append(script_id)
        execute_insert(
            f"UPDATE agent_scripts SET {', '.join(updates)} WHERE id = %s",
            tuple(values),
        )

    return {"message": "Script updated"}


@router.delete("/{script_id}")
async def delete_script(script_id: str):
    """Delete a script."""
    existing = execute_query(
        "SELECT id FROM agent_scripts WHERE id = %s",
        (script_id,),
        fetch_one=True,
    )
    if not existing:
        raise HTTPException(status_code=404, detail="Script not found")

    # Check if any campaign uses this script
    in_use = execute_query(
        "SELECT id FROM agent_campaigns WHERE script_id = %s AND status IN ('running', 'paused') LIMIT 1",
        (script_id,),
        fetch_one=True,
    )
    if in_use:
        raise HTTPException(status_code=400, detail="Script is in use by an active campaign")

    execute_insert("DELETE FROM agent_scripts WHERE id = %s", (script_id,))
    return {"message": "Script deleted"}
