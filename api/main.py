"""
Hookfish Dashboard API Server
==============================
FastAPI backend powering the dashboard UI at dial-insight-engine.lovable.app

Run: python -m api.main
  or: uvicorn api.main:app --reload --port 8000
"""

import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from api.auth import login_user, register_user, get_current_user
from api.routes import dashboard, campaigns, scripts, contacts, reports, monitor, settings


# ── App Setup ───────────────────────────────────────────────────

app = FastAPI(
    title="Hookfish Dashboard API",
    description="Backend API for the Hookfish AI cold-calling platform dashboard.",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS — allow the Lovable dashboard frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://dial-insight-engine.lovable.app",
        "http://localhost:3000",
        "http://localhost:5173",
        "http://localhost:8080",
        "*",  # TODO: Restrict in production
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Auth Routes ─────────────────────────────────────────────────

class LoginRequest(BaseModel):
    email: str
    password: str

class RegisterRequest(BaseModel):
    email: str
    name: str
    password: str
    role: str = "user"


@app.post("/api/auth/login", tags=["Auth"])
async def login(data: LoginRequest):
    """Login and get a JWT token."""
    return login_user(data.email, data.password)


@app.post("/api/auth/register", tags=["Auth"])
async def register(data: RegisterRequest):
    """Register a new user (admin only in production)."""
    return register_user(data.email, data.name, data.password, data.role)


@app.get("/api/auth/me", tags=["Auth"])
async def get_me(user=None):
    """Get current user info. Works in demo mode without auth."""
    if user:
        return {"user": user}
    # Demo mode fallback
    return {
        "user": {
            "id": "admin-001",
            "email": "admin@hookfish.in",
            "name": "Admin",
            "role": "admin",
        }
    }


# ── Health Check ────────────────────────────────────────────────

@app.get("/", tags=["Health"])
async def health():
    return {"status": "ok", "service": "hookfish-dashboard-api", "version": "1.0.0"}

@app.get("/health", tags=["Health"])
async def health_check():
    """Health check for Fly.io and monitoring."""
    try:
        from api.db import execute_query
        execute_query("SELECT 1", fetch_one=True)
        db_status = "connected"
    except Exception as e:
        db_status = f"error: {str(e)}"

    return {"status": "ok", "database": db_status}


# ── Register All Route Modules ──────────────────────────────────

app.include_router(dashboard.router)
app.include_router(campaigns.router)
app.include_router(scripts.router)
app.include_router(contacts.router)
app.include_router(reports.router)
app.include_router(monitor.router)
app.include_router(settings.router)


# ── Run ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("API_PORT", 8000))
    print(f"\n{'='*50}")
    print(f"  Hookfish Dashboard API")
    print(f"  http://localhost:{port}")
    print(f"  Docs: http://localhost:{port}/docs")
    print(f"{'='*50}\n")
    uvicorn.run("api.main:app", host="0.0.0.0", port=port, reload=True)
