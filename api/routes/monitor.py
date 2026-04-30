"""
Live Monitor API Routes — Active calls and real-time stats.
"""
from fastapi import APIRouter, Query
from api.db import execute_query

router = APIRouter(prefix="/api/monitor", tags=["Live Monitor"])

@router.get("/stats")
async def get_monitor_stats():
    active = execute_query("""
        SELECT COUNT(*) as active_calls FROM agent_call_logs
        WHERE started_at >= DATE_SUB(NOW(), INTERVAL 10 MINUTE) AND ended_at IS NULL""", fetch_one=True)
    recent = execute_query("""
        SELECT COUNT(*) as recent_calls FROM agent_call_logs
        WHERE started_at >= DATE_SUB(NOW(), INTERVAL 5 MINUTE)""", fetch_one=True)
    running = execute_query(
        "SELECT COUNT(*) as cnt FROM agent_campaigns WHERE status = 'running'", fetch_one=True)
    today = execute_query("""
        SELECT COUNT(*) as total FROM agent_call_logs WHERE DATE(started_at) = CURDATE()""", fetch_one=True)
    return {
        "active_calls": active["active_calls"] if active else 0,
        "calls_5min": recent["recent_calls"] if recent else 0,
        "running_campaigns": running["cnt"] if running else 0,
        "total_today": today["total"] if today else 0,
    }

@router.get("/active")
async def get_active_calls():
    calls = execute_query("""
        SELECT cl.id, cl.phone_number, cl.caller_name, cl.contact_type, cl.direction,
            cl.started_at, cl.duration_seconds, cl.transcript,
            c.name as campaign_name
        FROM agent_call_logs cl
        LEFT JOIN agent_campaigns c ON cl.call_id LIKE CONCAT('%', c.id, '%')
        WHERE cl.started_at >= DATE_SUB(NOW(), INTERVAL 10 MINUTE) AND cl.ended_at IS NULL
        ORDER BY cl.started_at DESC LIMIT 20""")
    return {"calls": calls or []}

@router.get("/recent")
async def get_recent_calls(limit: int = Query(20, le=50)):
    calls = execute_query("""
        SELECT id, phone_number, caller_name, contact_type, disposition,
            interest_level, duration_seconds, started_at, ended_at, transcript
        FROM agent_call_logs ORDER BY started_at DESC LIMIT %s""", (limit,))
    return {"calls": calls or []}

@router.get("/running-campaigns")
async def get_running_campaigns():
    campaigns = execute_query("""
        SELECT id, name, calls_made, total_contacts, status
        FROM agent_campaigns WHERE status = 'running' ORDER BY started_at DESC""")
    return {"campaigns": campaigns or []}
