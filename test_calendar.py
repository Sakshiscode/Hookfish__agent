# test_calendar.py — Test Google Calendar Integration
# =====================================================
# Run: python test_calendar.py
#
# Before running, make sure you have:
#   1. google_credentials.json in the project directory
#   2. GOOGLE_CALENDAR_ID set in .env (or it will use 'primary')
#   3. Shared the calendar with the service account email

import os
from dotenv import load_dotenv

load_dotenv()

from google_calendar import (
    get_calendar_service,
    parse_meeting_datetime,
    schedule_meeting_on_calendar,
    check_availability,
)

print("=" * 60)
print("Google Calendar Integration Test")
print("=" * 60)

# ---- 1. Test Service Authentication ----
print("\n[1] Testing Google Calendar authentication...")
service = get_calendar_service()
if service:
    print("   [OK] Successfully connected to Google Calendar API!")
else:
    print("   [FAIL] Could not connect. Check your credentials file.")
    print(f"   Looking for: {os.getenv('GOOGLE_CALENDAR_CREDENTIALS_FILE', 'google_credentials.json')}")
    exit(1)

# ---- 2. Test Date/Time Parsing ----
print("\n[2] Testing Hindi/English date-time parsing...")
test_cases = [
    ("kal", "subah 10 baje"),
    ("parso", "shaam 5 baje"),
    ("monday", "3 PM"),
    ("15 march", "dopahar"),
    ("tomorrow", "evening"),
    ("aaj", "2:30 PM"),
]
for date_str, time_str in test_cases:
    start, end = parse_meeting_datetime(date_str, time_str)
    print(f"   '{date_str}' + '{time_str}'  ->  {start.strftime('%d %b %Y %I:%M %p')} - {end.strftime('%I:%M %p')}")

# ---- 3. Test Availability Check ----
print("\n[3] Testing availability check for tomorrow 10 AM...")
start, end = parse_meeting_datetime("kal", "subah 10 baje")
avail = check_availability(start, end)
if avail["available"]:
    print(f"   [OK] Time slot is AVAILABLE!")
else:
    print(f"   [BUSY] Conflicts: {[c['summary'] for c in avail['conflicts']]}")
    if avail.get("suggestion"):
        print(f"   [SUGGEST] Next available: {avail['suggestion']}")

# ---- 4. Test Event Creation ----
print("\n[4] Creating a test meeting event...")
result = schedule_meeting_on_calendar(
    contact_name="Test Contact",
    contact_phone="+916362185137",
    contact_type="buyer",
    meeting_type="site_visit",
    date_str="kal",
    time_str="3 PM",
    project_name="Manikya - Mahim West",
    location="Mahim West, Mumbai",
    manager_name="Test Manager",
    manager_email="",  # Add a real email here to test invites
    notes="This is a test event from the voice agent.",
)

if result["success"]:
    print(f"   [OK] Event created successfully!")
    print(f"   Event ID: {result['event_id']}")
    print(f"   Time: {result['start']} to {result['end']}")
    print(f"   Link: {result['html_link']}")
else:
    print(f"   [FAIL] {result['message']}")

print()
print("=" * 60)
print("Test complete!")
print("=" * 60)
