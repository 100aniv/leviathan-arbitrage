"""Runtime settings routes."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from src.api.auth import require_auth

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1")


class SettingsUpdate(BaseModel):
    min_edge_bps: int | None = None
    active_exchanges: list[str] | None = None


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
