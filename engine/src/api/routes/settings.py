"""Runtime settings routes."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from src.api.auth import require_auth

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1")


class SettingsUpdate(BaseModel):
    min_edge_bps: int | None = None
    active_exchanges: list[str] | None = None


class ModeUpdate(BaseModel):
    mode: str  # "shadow", "paper", "live"


@router.get("/settings", dependencies=[Depends(require_auth)])
async def get_settings(request: Request) -> JSONResponse:
    """Return current runtime settings and strategy info."""
    ctx = request.app.state.engine_context
    active_strategies = [
        {"id": sid, "type": s.get("type", sid), "enabled": s.get("enabled", True)}
        for sid, s in ctx.strategies.items()
    ]
    return JSONResponse({
        "min_edge_bps": ctx.runtime_settings.get("min_edge_bps", 5),
        "active_strategies": active_strategies,
        "active_exchanges": ctx.runtime_settings.get("active_exchanges", []),
    })


@router.put("/settings", dependencies=[Depends(require_auth)])
async def update_settings(request: Request, body: SettingsUpdate) -> JSONResponse:
    """Update runtime settings (min_edge_bps, active_exchanges)."""
    ctx = request.app.state.engine_context
    if body.min_edge_bps is not None:
        ctx.runtime_settings["min_edge_bps"] = body.min_edge_bps
    if body.active_exchanges is not None:
        ctx.runtime_settings["active_exchanges"] = body.active_exchanges
    return JSONResponse({
        "min_edge_bps": ctx.runtime_settings.get("min_edge_bps", 5),
        "active_exchanges": ctx.runtime_settings.get("active_exchanges", []),
    })


@router.patch("/settings/mode", dependencies=[Depends(require_auth)])
async def update_mode(request: Request, body: ModeUpdate) -> JSONResponse:
    """Switch execution mode. Live mode requires LiveGate check."""
    ctx = request.app.state.engine_context
    valid_modes = {"shadow", "paper", "live"}
    if body.mode not in valid_modes:
        raise HTTPException(status_code=400, detail=f"Invalid mode. Must be one of: {valid_modes}")

    livegate_result = None
    if body.mode == "live":
        # Check LiveGate before allowing live mode
        engine = getattr(ctx, "engine", None)
        if engine and hasattr(engine, "_live_gate"):
            try:
                result = engine._live_gate.evaluate()
                livegate_result = {
                    "passed": result.passed if hasattr(result, 'passed') else False,
                    "checks": {}
                }
                if not livegate_result["passed"]:
                    return JSONResponse(status_code=403, content={
                        "error": "LiveGate check failed",
                        "livegate": livegate_result,
                        "current_mode": ctx.execution_mode,
                    })
            except Exception as exc:
                logger.warning("LiveGate evaluation failed: %s", exc)
                livegate_result = {"passed": False, "error": str(exc)}
                return JSONResponse(status_code=403, content={
                    "error": "LiveGate evaluation error",
                    "livegate": livegate_result,
                    "current_mode": ctx.execution_mode,
                })
        else:
            return JSONResponse(status_code=403, content={
                "error": "LiveGate not available",
                "current_mode": ctx.execution_mode,
            })

    ctx.execution_mode = body.mode
    return JSONResponse({
        "mode": ctx.execution_mode,
        "livegate": livegate_result,
    })
