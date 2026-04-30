"""
Settings API Routes — Team management, integrations, and audit log.
"""
import uuid
from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel
from typing import Optional
from api.db import execute_query, execute_insert
from api.auth import hash_password, require_admin

router = APIRouter(prefix="/api/settings", tags=["Settings"])

class TeamMemberCreate(BaseModel):
    email: str
    name: str
    password: str
    role: Optional[str] = "user"

class TeamMemberUpdate(BaseModel):
    name: Optional[str] = None
    role: Optional[str] = None
    is_active: Optional[bool] = None

@router.get("/team")
async def list_team():
    members = execute_query("SELECT id, email, name, role, is_active, last_login, created_at FROM agent_users ORDER BY created_at")
    return {"members": members or []}

@router.post("/team")
async def add_team_member(data: TeamMemberCreate):
    existing = execute_query("SELECT id FROM agent_users WHERE email = %s", (data.email,), fetch_one=True)
    if existing:
        raise HTTPException(status_code=400, detail="Email already exists")
    user_id = str(uuid.uuid4())
    execute_insert("INSERT INTO agent_users (id, email, name, password_hash, role) VALUES (%s,%s,%s,%s,%s)",
                   (user_id, data.email, data.name, hash_password(data.password), data.role))
    return {"id": user_id, "message": "Team member added"}

@router.put("/team/{user_id}")
async def update_team_member(user_id: str, data: TeamMemberUpdate):
    existing = execute_query("SELECT id FROM agent_users WHERE id = %s", (user_id,), fetch_one=True)
    if not existing:
        raise HTTPException(status_code=404, detail="User not found")
    updates, values = [], []
    for field, value in data.dict(exclude_unset=True).items():
        if value is not None:
            updates.append(f"{field} = %s")
            values.append(value)
    if updates:
        values.append(user_id)
        execute_insert(f"UPDATE agent_users SET {', '.join(updates)} WHERE id = %s", tuple(values))
    return {"message": "Team member updated"}

@router.delete("/team/{user_id}")
async def remove_team_member(user_id: str):
    if user_id == "admin-001":
        raise HTTPException(status_code=400, detail="Cannot delete the default admin")
    execute_insert("DELETE FROM agent_users WHERE id = %s", (user_id,))
    return {"message": "Team member removed"}

@router.get("/integrations")
async def get_integrations():
    import os
    return {"integrations": [
        {"name": "LiveKit", "type": "telephony", "status": "connected" if os.getenv("LIVEKIT_URL") else "disconnected"},
        {"name": "Deepgram", "type": "stt", "status": "connected" if os.getenv("deepgram_API_KEY") else "disconnected"},
        {"name": "Cerebras", "type": "llm", "status": "connected" if os.getenv("CEREBRAS_API_KEY") else "disconnected"},
        {"name": "Smallest AI", "type": "tts", "status": "connected" if os.getenv("SMALLEST_API_KEY") else "disconnected"},
        {"name": "MySQL (Aiven)", "type": "database", "status": "connected" if os.getenv("DB_HOST") else "disconnected"},
    ]}

@router.get("/audit-log")
async def get_audit_log(limit: int = Query(50, le=200)):
    logs = execute_query("SELECT * FROM agent_audit_log ORDER BY created_at DESC LIMIT %s", (limit,))
    return {"logs": logs or []}
