"""Risk management routes — kill switch and metrics."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse, JSONResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1")


class KillSwitchRequest(BaseModel):
    reason: str = "manual"


@router.post("/kill-switch")
async def trigger_kill_switch(body: KillSwitchRequest, request: Request) -> JSONResponse:
    """
    Trigger emergency kill switch.

    Sets Tier 1 halt flag immediately (< 1ms).
    Updates engine context so API reflects halted state.
    """
    ctx = request.app.state.engine_context
    ctx.kill_switch_active = True
    ctx.running = False

    # Activate the in-process halt flag (threading.Event — Redis-independent)
    try:
        from src.risk.kill_switch import halt_local
        halt_local()
    except Exception as exc:
        logger.error("Failed to set halt flag: %s", exc)

    logger.critical("KILL SWITCH TRIGGERED via API — reason: %s", body.reason)
    return JSONResponse({"status": "halted", "reason": body.reason})


@router.get("/metrics")
async def prometheus_metrics() -> PlainTextResponse:
    """Expose Prometheus metrics in text format."""
    try:
        from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
        data = generate_latest()
        return PlainTextResponse(
            content=data.decode() if isinstance(data, bytes) else data,
            media_type=CONTENT_TYPE_LATEST,
        )
    except Exception as exc:
        logger.warning("Metrics generation failed: %s", exc)
        return PlainTextResponse("# metrics unavailable\n", media_type="text/plain")
