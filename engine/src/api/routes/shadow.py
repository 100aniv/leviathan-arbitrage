"""Shadow mode stats routes."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from src.api.auth import require_auth

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1")


@router.get("/shadow/stats", dependencies=[Depends(require_auth)])
async def get_shadow_stats(request: Request) -> JSONResponse:
    """Return current shadow mode statistics."""
    ctx = request.app.state.engine_context
    shadow_mode = getattr(ctx, "shadow_mode", None)
    if shadow_mode is None:
        return JSONResponse({"active": False, "message": "Shadow mode not running"})
    try:
        return JSONResponse(shadow_mode.get_snapshot())
    except Exception as exc:
        logger.warning("Failed to get shadow stats: %s", exc)
        return JSONResponse({"active": False, "message": "Shadow mode not running"})
