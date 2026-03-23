"""Quick test to verify DB connection and helper functions."""
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from db_helper import get_connection, lookup_customer_by_phone, lookup_lead_by_phone

print("1. Testing DB connection...")
try:
    conn = get_connection()
    print("   [OK] Connection established!")
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) as count FROM customers")
    row = cur.fetchone()
    print(f"   Customers: {row['count']}")

    cur.execute("SELECT COUNT(*) as count FROM all_leads")
    row = cur.fetchone()
    print(f"   Leads: {row['count']}")

    cur.execute("SELECT COUNT(*) as count FROM properties")
    row = cur.fetchone()
    print(f"   Properties: {row['count']}")

    conn.close()
except Exception as e:
    print(f"   [FAIL] Connection failed: {e}")

print()
print("2. Testing lookup functions...")
result = lookup_customer_by_phone("+916362185137")
print(f"   Customer lookup: {result}")

leads = lookup_lead_by_phone("+916362185137")
print(f"   Lead lookup: {len(leads)} leads found")
for lead in leads:
    pname = lead.get('customer_name', 'N/A')
    prop = lead.get('property_name', 'N/A')
    status = lead.get('status', 'N/A')
    print(f"     - {pname} -> {prop} ({status})")

print()
print("[DONE] All database functions tested!")
