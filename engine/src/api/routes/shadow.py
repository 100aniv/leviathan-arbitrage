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
    """Return current shadow mode statistics, with rolling risk metrics (US-281)."""
    ctx = request.app.state.engine_context
    shadow_mode = getattr(ctx, "paper_mode", None) or getattr(ctx, "shadow_mode", None)
    if shadow_mode is None:
        return JSONResponse({"active": False, "message": "Shadow mode not running"})
    try:
        snapshot = shadow_mode.get_snapshot()
        # US-281: attach rolling metrics if available on context
        rolling_metrics = getattr(ctx, "rolling_metrics", None)
        if rolling_metrics is not None:
            try:
                snapshot["metrics"] = rolling_metrics.to_dict()
            except Exception:
                pass
        return JSONResponse(snapshot)
    except Exception as exc:
        logger.warning("Failed to get shadow stats: %s", exc)
        return JSONResponse({"active": False, "message": "Shadow mode not running"})


# US-435: /api/v1/paper/stats alias — same handler as /shadow/stats
router.get("/paper/stats", dependencies=[Depends(require_auth)])(get_shadow_stats)
