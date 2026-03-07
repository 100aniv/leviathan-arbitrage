"""JWT authentication helpers for the LEVIATHAN API."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

_JWT_SECRET = os.environ.get("JWT_SECRET", "leviathan-dev-secret-change-in-production")
_JWT_ALGORITHM = "HS256"
_JWT_EXPIRY_HOURS = 24

DASHBOARD_USER = os.environ.get("DASHBOARD_USER", "admin")
DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "leviathan")

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
