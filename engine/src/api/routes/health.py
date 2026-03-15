"""Health check route."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get("/health")
async def health(request: Request) -> JSONResponse:
    """Liveness probe — returns 200 when the process is alive.

    Security: internal state (engine_running, kill_switch_active) removed
    to prevent reconnaissance via unauthenticated endpoint (TF-QF MEDIUM-1).
    """
    return JSONResponse({"status": "ok"})
