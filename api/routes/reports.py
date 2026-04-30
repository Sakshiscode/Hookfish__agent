"""
Reports API Routes — Analytics, charts, and CSV export.
"""
from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from api.db import execute_query
import csv, io

router = APIRouter(prefix="/api/reports", tags=["Reports"])

@router.get("/summary")
async def get_summary(campaign_id: str = None, days: int = Query(30, le=90)):
    where = "WHERE started_at >= DATE_SUB(NOW(), INTERVAL %s DAY)"
    params = [days]
    if campaign_id:
        where += " AND call_id IN (SELECT call_id FROM agent_call_logs WHERE call_id LIKE %s)"
        params.append(f"%{campaign_id}%")
    stats = execute_query(f"""
        SELECT COUNT(*) as total_calls,
            SUM(CASE WHEN disposition IS NOT NULL AND disposition NOT IN ('failed','no_answer') THEN 1 ELSE 0 END) as connected,
            SUM(CASE WHEN disposition IN ('qualified','follow_up') THEN 1 ELSE 0 END) as converted,
            ROUND(AVG(CASE WHEN duration_seconds > 0 THEN duration_seconds ELSE NULL END),0) as avg_duration
        FROM agent_call_logs {where}""", tuple(params), fetch_one=True)
    total = stats["total_calls"] or 0
    connected = stats["connected"] or 0
    converted = stats["converted"] or 0
    return {
        "total_calls": total,
        "connect_rate": round(connected/total*100, 1) if total else 0,
        "conversion": round(converted/total*100, 1) if total else 0,
        "avg_duration": int(stats["avg_duration"] or 0),
    }

@router.get("/daily")
async def get_daily(days: int = Query(14, le=90)):
    rows = execute_query("""
        SELECT DATE(started_at) as date, COUNT(*) as calls,
            SUM(CASE WHEN disposition NOT IN ('failed','no_answer') THEN 1 ELSE 0 END) as connected
        FROM agent_call_logs WHERE started_at >= DATE_SUB(NOW(), INTERVAL %s DAY)
        GROUP BY DATE(started_at) ORDER BY date""", (days,))
    return {"data": rows or []}

@router.get("/outcomes")
async def get_outcomes(days: int = Query(30, le=90)):
    rows = execute_query("""
        SELECT COALESCE(disposition, 'unknown') as outcome, COUNT(*) as count
        FROM agent_call_logs WHERE started_at >= DATE_SUB(NOW(), INTERVAL %s DAY)
        GROUP BY disposition ORDER BY count DESC""", (days,))
    return {"data": rows or []}

@router.get("/heatmap")
async def get_heatmap(days: int = Query(14, le=30)):
    rows = execute_query("""
        SELECT DAYOFWEEK(started_at) as day_of_week, HOUR(started_at) as hour, COUNT(*) as count
        FROM agent_call_logs WHERE started_at >= DATE_SUB(NOW(), INTERVAL %s DAY)
        GROUP BY DAYOFWEEK(started_at), HOUR(started_at)""", (days,))
    return {"data": rows or []}

@router.get("/export")
async def export_csv(days: int = Query(30, le=90)):
    rows = execute_query("""
        SELECT phone_number, caller_name, contact_type, direction, disposition,
            interest_level, duration_seconds, outcome_reason, started_at, ended_at
        FROM agent_call_logs WHERE started_at >= DATE_SUB(NOW(), INTERVAL %s DAY)
        ORDER BY started_at DESC""", (days,))
    output = io.StringIO()
    if rows:
        writer = csv.DictWriter(output, fieldnames=rows[0].keys())
        writer.writeheader()
        for row in rows:
            writer.writerow({k: str(v) if v else "" for k, v in row.items()})
    output.seek(0)
    return StreamingResponse(io.BytesIO(output.getvalue().encode()),
        media_type="text/csv", headers={"Content-Disposition": "attachment; filename=hookfish_calls_export.csv"})
