"""
Dashboard API Routes
====================
Stats, charts, and overview data for the main dashboard page.
Powered by existing agent_call_logs table.
"""

from fastapi import APIRouter, Query
from api.db import execute_query

router = APIRouter(prefix="/api/dashboard", tags=["Dashboard"])


@router.get("/stats")
async def get_dashboard_stats(period: str = Query("24h", enum=["24h", "7d", "30d"])):
    """Get key metrics for the dashboard cards."""
    period_map = {"24h": 1, "7d": 7, "30d": 30}
    days = period_map.get(period, 1)

    stats = execute_query(
        """
        SELECT
            COUNT(*) as total_calls,
            SUM(CASE WHEN disposition IS NOT NULL AND disposition NOT IN ('failed', 'no_answer') THEN 1 ELSE 0 END) as connected_calls,
            SUM(CASE WHEN disposition IN ('qualified', 'follow_up') THEN 1 ELSE 0 END) as converted_calls,
            ROUND(AVG(CASE WHEN duration_seconds > 0 THEN duration_seconds ELSE NULL END), 0) as avg_duration
        FROM agent_call_logs
        WHERE started_at >= DATE_SUB(NOW(), INTERVAL %s DAY)
        """,
        (days,),
        fetch_one=True,
    )

    total = stats["total_calls"] or 0
    connected = stats["connected_calls"] or 0
    converted = stats["converted_calls"] or 0
    avg_dur = int(stats["avg_duration"] or 0)

    # Active campaigns
    active_campaigns = execute_query(
        "SELECT COUNT(*) as cnt FROM agent_campaigns WHERE status = 'running'",
        fetch_one=True,
    )

    return {
        "active_campaigns": active_campaigns["cnt"] if active_campaigns else 0,
        "total_calls": total,
        "connect_rate": round((connected / total * 100), 1) if total > 0 else 0,
        "conversion": round((converted / total * 100), 1) if total > 0 else 0,
        "avg_duration": avg_dur,
        "period": period,
    }


@router.get("/calls-per-hour")
async def get_calls_per_hour(period: str = Query("24h", enum=["24h", "7d"])):
    """Get calls per hour for the chart."""
    if period == "24h":
        rows = execute_query(
            """
            SELECT
                HOUR(started_at) as hour,
                COUNT(*) as count
            FROM agent_call_logs
            WHERE started_at >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
            GROUP BY HOUR(started_at)
            ORDER BY hour
            """
        )
    else:
        rows = execute_query(
            """
            SELECT
                DATE(started_at) as date,
                HOUR(started_at) as hour,
                COUNT(*) as count
            FROM agent_call_logs
            WHERE started_at >= DATE_SUB(NOW(), INTERVAL 7 DAY)
            GROUP BY DATE(started_at), HOUR(started_at)
            ORDER BY date, hour
            """
        )

    return {"data": rows or [], "period": period}


@router.get("/recent-campaigns")
async def get_recent_campaigns(limit: int = 5):
    """Get recent campaigns for the dashboard sidebar."""
    campaigns = execute_query(
        """
        SELECT id, name, status, calls_made, total_contacts, created_at, updated_at
        FROM agent_campaigns
        ORDER BY updated_at DESC
        LIMIT %s
        """,
        (limit,),
    )
    return {"campaigns": campaigns or []}


@router.get("/recent-activity")
async def get_recent_activity(limit: int = 10):
    """Get recent call activity."""
    calls = execute_query(
        """
        SELECT
            id, phone_number, caller_name, contact_type, direction,
            disposition, interest_level, duration_seconds,
            started_at, ended_at
        FROM agent_call_logs
        ORDER BY started_at DESC
        LIMIT %s
        """,
        (limit,),
    )
    return {"calls": calls or []}
