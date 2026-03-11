"""JWT authentication helpers for the LEVIATHAN API."""
from __future__ import annotations

import hashlib
import logging
import os
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException
from typing import Any
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# JWT Configuration — NO default secret. Fail-fast if unset in production.
# ---------------------------------------------------------------------------
_ENGINE_ENV = os.environ.get("ENGINE_ENV", "dev")

_JWT_SECRET: str | None = os.environ.get("JWT_SECRET")
if _JWT_SECRET is None:
    if _ENGINE_ENV in ("prod", "staging"):
        raise RuntimeError(
            "JWT_SECRET environment variable MUST be set in production/staging. "
            "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(64))\""
        )
    # dev/test: auto-generate a random secret per process (safe for local dev)
    import secrets
    _JWT_SECRET = secrets.token_urlsafe(32)
    logger.warning("JWT_SECRET not set — using ephemeral random secret (dev/test only)")

_JWT_ALGORITHM = "HS256"
_JWT_EXPIRY_HOURS = 24

# ---------------------------------------------------------------------------
# Dashboard credentials — bcrypt hashed password
# ---------------------------------------------------------------------------
DASHBOARD_USER = os.environ.get("DASHBOARD_USER", "admin")
_DASHBOARD_PASSWORD_RAW = os.environ.get("DASHBOARD_PASSWORD", "leviathan")

# Bcrypt hashing for password storage and verification
try:
    import bcrypt
    _HAS_BCRYPT = True
except ImportError:
    _HAS_BCRYPT = False
    logger.warning("bcrypt not installed — using SHA-256 fallback (install bcrypt for production)")

if _HAS_BCRYPT:
    # Hash the password from env at startup (so plaintext is never compared at runtime)
    _DASHBOARD_PASSWORD_HASH: bytes = bcrypt.hashpw(
        _DASHBOARD_PASSWORD_RAW.encode("utf-8"), bcrypt.gensalt(rounds=12)
    )
else:
    # SHA-256 fallback for test environments without bcrypt
    _DASHBOARD_PASSWORD_HASH_SHA = hashlib.sha256(
        _DASHBOARD_PASSWORD_RAW.encode("utf-8")
    ).hexdigest()


def verify_password(plain_password: str) -> bool:
    """Verify a plaintext password against the stored hash."""
    if _HAS_BCRYPT:
        return bcrypt.checkpw(
            plain_password.encode("utf-8"), _DASHBOARD_PASSWORD_HASH
        )
    # SHA-256 fallback
    return (
        hashlib.sha256(plain_password.encode("utf-8")).hexdigest()
        == _DASHBOARD_PASSWORD_HASH_SHA
    )

_bearer_scheme = HTTPBearer(auto_error=False)


def create_token(username: str) -> str:
    """Create a signed JWT for the given username."""
    payload = {
        "sub": username,
        "exp": datetime.now(timezone.utc) + timedelta(hours=_JWT_EXPIRY_HOURS),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, _JWT_SECRET, algorithm=_JWT_ALGORITHM)


def require_auth(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> str:
    """FastAPI dependency that validates a Bearer JWT and returns the username."""
    if credentials is None:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    try:
        payload = jwt.decode(
            credentials.credentials, _JWT_SECRET, algorithms=[_JWT_ALGORITHM]
        )
        return str(payload["sub"])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


def verify_ws_token(websocket: Any) -> str | None:
    """
    Verify JWT for WebSocket connections.

    Checks in order:
    1. ?token= query parameter
    2. leviathan_token cookie (dashboard compatibility)

    Returns username if valid, None if invalid/missing.
    """
    token = websocket.query_params.get("token")
    if not token:
        token = websocket.cookies.get("leviathan_token")
    if not token:
        return None
    try:
        payload = jwt.decode(token, _JWT_SECRET, algorithms=[_JWT_ALGORITHM])
        return str(payload["sub"])
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None
