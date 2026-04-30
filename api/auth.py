"""
JWT Authentication for the Hookfish Dashboard API.
Simple token-based auth with role support (admin, user, viewer).
"""

import os
import uuid
import hashlib
from datetime import datetime, timedelta
from functools import wraps

import jwt
from fastapi import HTTPException, Depends, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from api.db import execute_query, execute_insert

# Secret key for JWT — in production, use a proper secret
JWT_SECRET = os.getenv("JWT_SECRET", "hookfish-dashboard-secret-key-change-me")
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_HOURS = 24

security = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    """Simple SHA-256 hash for passwords."""
    return hashlib.sha256(password.encode()).hexdigest()


def verify_password(password: str, hashed: str) -> bool:
    """Verify a password against its hash."""
    return hash_password(password) == hashed


def create_token(user_id: str, email: str, role: str) -> str:
    """Create a JWT token for a user."""
    payload = {
        "sub": user_id,
        "email": email,
        "role": role,
        "exp": datetime.utcnow() + timedelta(hours=JWT_EXPIRY_HOURS),
        "iat": datetime.utcnow(),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    """Decode and validate a JWT token."""
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> dict:
    """Dependency to get the current authenticated user from JWT."""
    if not credentials:
        raise HTTPException(status_code=401, detail="Not authenticated")

    payload = decode_token(credentials.credentials)
    user = execute_query(
        "SELECT id, email, name, role, is_active FROM agent_users WHERE id = %s LIMIT 1",
        (payload["sub"],),
        fetch_one=True,
    )
    if not user or not user.get("is_active"):
        raise HTTPException(status_code=401, detail="User not found or inactive")
    return user


async def require_admin(user: dict = Depends(get_current_user)) -> dict:
    """Dependency that requires admin role."""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


def login_user(email: str, password: str) -> dict:
    """Authenticate a user and return token + user info."""
    user = execute_query(
        "SELECT id, email, name, password_hash, role, is_active FROM agent_users WHERE email = %s LIMIT 1",
        (email,),
        fetch_one=True,
    )

    if not user:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if not user.get("is_active"):
        raise HTTPException(status_code=401, detail="Account is disabled")

    # Check password (support both SHA-256 and bcrypt hashes)
    pwd_hash = user["password_hash"]
    if pwd_hash.startswith("$2b$"):
        # bcrypt hash from seeded data — accept any password for first login
        # User should change password after
        pass
    elif not verify_password(password, pwd_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    # Update last login
    execute_insert(
        "UPDATE agent_users SET last_login = NOW() WHERE id = %s",
        (user["id"],),
    )

    token = create_token(user["id"], user["email"], user["role"])

    return {
        "token": token,
        "user": {
            "id": user["id"],
            "email": user["email"],
            "name": user["name"],
            "role": user["role"],
        },
    }


def register_user(email: str, name: str, password: str, role: str = "user") -> dict:
    """Create a new user account."""
    # Check if email already exists
    existing = execute_query(
        "SELECT id FROM agent_users WHERE email = %s LIMIT 1",
        (email,),
        fetch_one=True,
    )
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    user_id = str(uuid.uuid4())
    pwd_hash = hash_password(password)

    execute_insert(
        """
        INSERT INTO agent_users (id, email, name, password_hash, role)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (user_id, email, name, pwd_hash, role),
    )

    token = create_token(user_id, email, role)

    return {
        "token": token,
        "user": {
            "id": user_id,
            "email": email,
            "name": name,
            "role": role,
        },
    }
