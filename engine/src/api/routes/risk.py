"""Risk management routes — kill switch and metrics."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import PlainTextResponse, JSONResponse
from pydantic import BaseModel

from src.api.auth import require_auth

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1")


class KillSwitchRequest(BaseModel):
    reason: str = "manual"


@router.post("/kill-switch", dependencies=[Depends(require_auth)])
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


@router.get("/mode", dependencies=[Depends(require_auth)])
async def engine_mode(request: Request) -> JSONResponse:
    """Return current engine execution mode — public endpoint for dashboard status bar."""
    ctx = request.app.state.engine_context
    mode = ctx.execution_mode
    shadow_active = getattr(ctx, "shadow_active", False)
    # Auto-detect shadow mode from runtime state
    shadow_mode = getattr(ctx, "paper_mode", None) or getattr(ctx, "shadow_mode", None)
    if shadow_mode is not None and hasattr(shadow_mode, "_stats"):
        mode = "shadow"
        shadow_active = True
    return JSONResponse({
        "mode": mode,
        "data_mode": getattr(ctx, "data_mode", "real" if shadow_active else "synthetic"),
        "shadow_active": shadow_active,
        "live_gate_eligible": getattr(ctx, "live_gate_eligible", False),
    })


@router.get("/risk/metrics", dependencies=[Depends(require_auth)])
async def risk_metrics(request: Request) -> JSONResponse:
    """Return current risk metrics — public endpoint for dashboard status bar."""
    ctx = request.app.state.engine_context

    kill_switch_active = ctx.kill_switch_active
    circuit_breaker_state = "CLOSED"
    max_drawdown_pct = 0.0
    daily_loss_pct = 0.0
    position_count = 0
    correlation_alert = False

    rg = ctx.risk_guardian
    if rg is not None:
        try:
            kill_switch_active = getattr(rg, "kill_switch_active", kill_switch_active)
            circuit_breaker_state = getattr(rg, "circuit_breaker_state", circuit_breaker_state)
            max_drawdown_pct = float(getattr(rg, "max_drawdown_pct", max_drawdown_pct))
            daily_loss_pct = float(getattr(rg, "daily_loss_pct", daily_loss_pct))
            correlation_alert = getattr(rg, "correlation_alert", correlation_alert)
        except Exception as exc:
            logger.warning("Failed to read risk_guardian attributes: %s", exc)

    if ctx.position_manager is not None:
        try:
            position_count = len(ctx.position_manager.get_all_positions())
        except Exception as exc:
            logger.warning("Failed to read position count: %s", exc)
    else:
        position_count = len(ctx.positions)

    return JSONResponse({
        "kill_switch_active": kill_switch_active,
        "circuit_breaker_state": circuit_breaker_state,
        "max_drawdown_pct": max_drawdown_pct,
        "daily_loss_pct": daily_loss_pct,
        "position_count": position_count,
        "correlation_alert": correlation_alert,
    })


@router.get("/metrics", dependencies=[Depends(require_auth)])
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
