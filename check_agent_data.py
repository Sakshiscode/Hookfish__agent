"""
Check Agent Data — View all data in the new agent tables after a test call.
Usage: python check_agent_data.py [phone_number]
"""

import sys
from db_helper import get_connection

phone = sys.argv[1] if len(sys.argv) > 1 else None


def query(sql, params=None):
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute(sql, params or ())
        rows = cur.fetchall()
    conn.close()
    return rows


def print_table(title, rows, max_col_width=60):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")
    if not rows:
        print("  (empty)")
        return
    for i, row in enumerate(rows):
        print(f"\n  --- Record {i+1} ---")
        for key, val in row.items():
            val_str = str(val) if val is not None else "(null)"
            if len(val_str) > max_col_width:
                val_str = val_str[:max_col_width] + "..."
            print(f"    {key}: {val_str}")


# 1. Call Logs
if phone:
    rows = query("SELECT * FROM agent_call_logs WHERE phone_number = %s ORDER BY created_at DESC LIMIT 5", (phone,))
    print_table(f"CALL LOGS for {phone}", rows)
else:
    rows = query("SELECT * FROM agent_call_logs ORDER BY created_at DESC LIMIT 5")
    print_table("RECENT CALL LOGS (last 5)", rows)

# 2. Call Attempts
if phone:
    rows = query("SELECT * FROM agent_call_attempts WHERE phone_number = %s ORDER BY attempt_date DESC LIMIT 5", (phone,))
    print_table(f"CALL ATTEMPTS for {phone}", rows)
else:
    rows = query("SELECT * FROM agent_call_attempts ORDER BY attempt_date DESC LIMIT 5")
    print_table("RECENT CALL ATTEMPTS (last 5)", rows)

# 3. DNC List
if phone:
    rows = query("SELECT * FROM agent_dnc_list WHERE phone_number = %s", (phone,))
    print_table(f"DNC STATUS for {phone}", rows)
else:
    rows = query("SELECT * FROM agent_dnc_list ORDER BY created_at DESC LIMIT 10")
    print_table("DNC LIST", rows)

# 4. Meetings
if phone:
    rows = query("SELECT * FROM agent_meetings WHERE phone_number = %s ORDER BY created_at DESC LIMIT 5", (phone,))
    print_table(f"MEETINGS for {phone}", rows)
else:
    rows = query("SELECT * FROM agent_meetings ORDER BY created_at DESC LIMIT 5")
    print_table("RECENT MEETINGS", rows)

# 5. Managers
rows = query("SELECT * FROM agent_managers ORDER BY total_allocated DESC LIMIT 10")
print_table("MANAGER POOL", rows)

print(f"\n{'='*60}")
print("  Done!")
print(f"{'='*60}\n")
