"""Strategy management routes."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/api/v1/strategies")


@router.get("")
async def list_strategies(request: Request) -> JSONResponse:
    """Return status of all registered strategies."""
    ctx = request.app.state.engine_context
    return JSONResponse(list(ctx.strategies.values()))


@router.post("/{strategy_id}/toggle")
async def toggle_strategy(strategy_id: str, request: Request) -> JSONResponse:
    """Enable or disable a strategy by ID."""
    ctx = request.app.state.engine_context
    strategy = ctx.strategies.get(strategy_id)
    if strategy is None:
        raise HTTPException(status_code=404, detail=f"Strategy '{strategy_id}' not found")
    strategy["enabled"] = not strategy.get("enabled", True)
    return JSONResponse({"id": strategy_id, "enabled": strategy["enabled"]})


@router.post("/{strategy_id}/config")
async def update_strategy_config(
    strategy_id: str,
    request: Request,
) -> JSONResponse:
    """Update runtime configuration parameters for a strategy."""
    ctx = request.app.state.engine_context
    strategy = ctx.strategies.get(strategy_id)
    if strategy is None:
        raise HTTPException(status_code=404, detail=f"Strategy '{strategy_id}' not found")
    body: dict[str, Any] = await request.json()
    if "config" not in strategy:
        strategy["config"] = {}
    strategy["config"].update(body)
    return JSONResponse({"id": strategy_id, "config": strategy["config"]})
