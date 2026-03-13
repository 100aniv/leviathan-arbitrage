"""Strategy management routes."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from src.api.auth import require_auth

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/strategies")


def _get_strategy_list(ctx: Any) -> list[dict[str, Any]]:
    """Get strategy list from real StrategyManager or fallback to dict."""
    if ctx.strategy_manager is not None:
        try:
            strategies = []
            for sid in ctx.strategy_manager.list_strategies():
                s = ctx.strategy_manager.get_strategy(sid)
                strategies.append({
                    "id": sid,
                    "type": getattr(s, "STRATEGY_TYPE", "unknown"),
                    "enabled": s.is_active if s else False,
                    "metrics": (
                        {k: float(v) if hasattr(v, "as_tuple") else v
                         for k, v in s.metrics.model_dump().items()}
                        if s and hasattr(s, "metrics") else {}
                    ),
                })
            return strategies
        except Exception as exc:
            logger.warning("Failed to get strategies from manager: %s", exc)
    return list(ctx.strategies.values())


@router.get("", dependencies=[Depends(require_auth)])
async def list_strategies(request: Request) -> JSONResponse:
    """Return status of all registered strategies."""
    ctx = request.app.state.engine_context
    return JSONResponse(_get_strategy_list(ctx))


@router.post("/{strategy_id}/toggle", dependencies=[Depends(require_auth)])
async def toggle_strategy(strategy_id: str, request: Request) -> JSONResponse:
    """Enable or disable a strategy by ID."""
    ctx = request.app.state.engine_context

    # Try real StrategyManager first
    if ctx.strategy_manager is not None:
        try:
            strategy = ctx.strategy_manager.get_strategy(strategy_id)
            if strategy is None:
                raise HTTPException(status_code=404, detail=f"Strategy '{strategy_id}' not found")
            if strategy.is_active:
                await ctx.strategy_manager.stop_strategy(strategy_id)
            else:
                await ctx.strategy_manager.start_strategy(strategy_id)
            return JSONResponse({"id": strategy_id, "enabled": strategy.is_active})
        except HTTPException:
            raise
        except Exception as exc:
            logger.warning("StrategyManager toggle failed: %s", exc)

    # Fallback to dict-based strategies
    strategy = ctx.strategies.get(strategy_id)
    if strategy is None:
        raise HTTPException(status_code=404, detail=f"Strategy '{strategy_id}' not found")
    strategy["enabled"] = not strategy.get("enabled", True)
    return JSONResponse({"id": strategy_id, "enabled": strategy["enabled"]})


@router.post("/{strategy_id}/config", dependencies=[Depends(require_auth)])
async def update_strategy_config(
    strategy_id: str,
    request: Request,
) -> JSONResponse:
    """Update runtime configuration parameters for a strategy."""
    ctx = request.app.state.engine_context
    body: dict[str, Any] = await request.json()

    # Try real StrategyManager first
    if ctx.strategy_manager is not None:
        try:
            strategy = ctx.strategy_manager.get_strategy(strategy_id)
            if strategy is None:
                raise HTTPException(status_code=404, detail=f"Strategy '{strategy_id}' not found")
            if hasattr(ctx.strategy_manager, "reconfigure"):
                ctx.strategy_manager.reconfigure(strategy_id, body)
            return JSONResponse({"id": strategy_id, "config": body})
        except HTTPException:
            raise
        except Exception as exc:
            logger.warning("StrategyManager config update failed: %s", exc)

    # Fallback
    strategy = ctx.strategies.get(strategy_id)
    if strategy is None:
        raise HTTPException(status_code=404, detail=f"Strategy '{strategy_id}' not found")
    if "config" not in strategy:
        strategy["config"] = {}
    strategy["config"].update(body)
    return JSONResponse({"id": strategy_id, "config": strategy["config"]})


@router.get("/{strategy_id}/trades", dependencies=[Depends(require_auth)])
async def get_strategy_trades(
    strategy_id: str,
    request: Request,
    limit: int = Query(50, ge=1, le=1000),
) -> JSONResponse:
    """Return trade history filtered by strategy_id."""
    ctx = request.app.state.engine_context
    trades = [
        t for t in ctx.trade_history
        if t.get("strategy_id") == strategy_id
    ]
    return JSONResponse(trades[-limit:])
