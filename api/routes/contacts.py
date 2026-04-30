"""
Contacts & Lists API Routes
=============================
Contact list management, CSV import, DNC management, and search.
"""

import uuid
import csv
import io
from fastapi import APIRouter, HTTPException, Query, UploadFile, File
from pydantic import BaseModel
from typing import Optional
from api.db import execute_query, execute_insert, get_db

router = APIRouter(prefix="/api", tags=["Contacts"])


class ContactListCreate(BaseModel):
    name: str
    description: Optional[str] = None

class ContactCreate(BaseModel):
    name: Optional[str] = None
    phone: str
    email: Optional[str] = None
    company: Optional[str] = None
    contact_type: Optional[str] = "buyer"

class DNCCreate(BaseModel):
    phone_number: str
    reason: Optional[str] = "manual"


@router.get("/contact-lists")
async def list_contact_lists():
    lists = execute_query(
        """SELECT cl.*, (SELECT COUNT(*) FROM agent_contacts WHERE list_id = cl.id) as contact_count
        FROM agent_contact_lists cl ORDER BY cl.created_at DESC"""
    )
    return {"lists": lists or []}

@router.get("/contact-lists/{list_id}")
async def get_contact_list(list_id: str):
    cl = execute_query("SELECT * FROM agent_contact_lists WHERE id = %s", (list_id,), fetch_one=True)
    if not cl:
        raise HTTPException(status_code=404, detail="Contact list not found")
    return cl

@router.post("/contact-lists")
async def create_contact_list(data: ContactListCreate):
    list_id = str(uuid.uuid4())
    execute_insert("INSERT INTO agent_contact_lists (id, name, description) VALUES (%s, %s, %s)",
                   (list_id, data.name, data.description))
    return {"id": list_id, "message": "Contact list created"}

@router.delete("/contact-lists/{list_id}")
async def delete_contact_list(list_id: str):
    existing = execute_query("SELECT id FROM agent_contact_lists WHERE id = %s", (list_id,), fetch_one=True)
    if not existing:
        raise HTTPException(status_code=404, detail="Contact list not found")
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM agent_contacts WHERE list_id = %s", (list_id,))
            cur.execute("DELETE FROM agent_contact_lists WHERE id = %s", (list_id,))
            conn.commit()
    return {"message": "Contact list deleted"}

@router.get("/contact-lists/{list_id}/contacts")
async def list_contacts(list_id: str, search: Optional[str] = None, limit: int = Query(50, le=200), offset: int = 0):
    if search:
        contacts = execute_query(
            "SELECT * FROM agent_contacts WHERE list_id = %s AND (name LIKE %s OR phone LIKE %s) ORDER BY created_at DESC LIMIT %s OFFSET %s",
            (list_id, f"%{search}%", f"%{search}%", limit, offset))
    else:
        contacts = execute_query(
            "SELECT * FROM agent_contacts WHERE list_id = %s ORDER BY created_at DESC LIMIT %s OFFSET %s",
            (list_id, limit, offset))
    count = execute_query("SELECT COUNT(*) as total FROM agent_contacts WHERE list_id = %s", (list_id,), fetch_one=True)
    return {"contacts": contacts or [], "total": count["total"] if count else 0}

@router.post("/contact-lists/{list_id}/contacts")
async def add_contact(list_id: str, data: ContactCreate):
    contact_id = str(uuid.uuid4())
    execute_insert(
        "INSERT INTO agent_contacts (id, list_id, name, phone, email, company, contact_type) VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (contact_id, list_id, data.name, data.phone, data.email, data.company, data.contact_type))
    execute_insert("UPDATE agent_contact_lists SET total_count = (SELECT COUNT(*) FROM agent_contacts WHERE list_id = %s) WHERE id = %s", (list_id, list_id))
    return {"id": contact_id, "message": "Contact added"}

@router.post("/contact-lists/{list_id}/import")
async def import_csv(list_id: str, file: UploadFile = File(...)):
    existing = execute_query("SELECT id FROM agent_contact_lists WHERE id = %s", (list_id,), fetch_one=True)
    if not existing:
        raise HTTPException(status_code=404, detail="Contact list not found")
    if not file.filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="Only CSV files supported")
    content = await file.read()
    reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")))
    headers = [h.lower().strip() for h in (reader.fieldnames or [])]
    if "phone" not in headers:
        raise HTTPException(status_code=400, detail="CSV must have a 'phone' column")
    imported, skipped = 0, 0
    with get_db() as conn:
        with conn.cursor() as cur:
            for row in reader:
                row = {k.lower().strip(): v.strip() for k, v in row.items() if v}
                phone = row.get("phone", "").strip()
                if not phone:
                    skipped += 1
                    continue
                if not phone.startswith("+") and len(phone) == 10:
                    phone = "+91" + phone
                try:
                    cur.execute("INSERT INTO agent_contacts (id, list_id, name, phone, email, company) VALUES (%s,%s,%s,%s,%s,%s)",
                                (str(uuid.uuid4()), list_id, row.get("name"), phone, row.get("email"), row.get("company")))
                    imported += 1
                except Exception:
                    skipped += 1
            cur.execute("UPDATE agent_contact_lists SET total_count = (SELECT COUNT(*) FROM agent_contacts WHERE list_id = %s) WHERE id = %s", (list_id, list_id))
            conn.commit()
    return {"imported": imported, "skipped": skipped, "message": f"Imported {imported} contacts"}

@router.get("/contacts/search")
async def search_contacts(q: str = Query(..., min_length=2)):
    contacts = execute_query(
        "SELECT c.*, cl.name as list_name FROM agent_contacts c LEFT JOIN agent_contact_lists cl ON c.list_id = cl.id WHERE c.name LIKE %s OR c.phone LIKE %s LIMIT 50",
        (f"%{q}%", f"%{q}%"))
    return {"contacts": contacts or []}

@router.get("/dnc")
async def list_dnc(limit: int = Query(100, le=500)):
    dnc = execute_query("SELECT * FROM agent_dnc_list ORDER BY created_at DESC LIMIT %s", (limit,))
    return {"dnc_list": dnc or []}

@router.post("/dnc")
async def add_to_dnc(data: DNCCreate):
    execute_insert("INSERT INTO agent_dnc_list (phone_number, reason, added_by) VALUES (%s, %s, 'dashboard') ON DUPLICATE KEY UPDATE reason = %s",
                   (data.phone_number, data.reason, data.reason))
    return {"message": f"Added {data.phone_number} to DNC list"}

@router.delete("/dnc/{phone_number}")
async def remove_from_dnc(phone_number: str):
    execute_insert("DELETE FROM agent_dnc_list WHERE phone_number = %s", (phone_number,))
    return {"message": f"Removed {phone_number} from DNC list"}
