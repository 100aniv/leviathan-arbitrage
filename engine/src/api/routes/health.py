"""Health check route."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get("/health")
async def health(request: Request) -> JSONResponse:
    """Liveness probe — returns 200 when the process is alive."""
    ctx = request.app.state.engine_context
    return JSONResponse({
        "status": "ok",
        "engine_running": ctx.running,
        "kill_switch_active": ctx.kill_switch_active,
    })
