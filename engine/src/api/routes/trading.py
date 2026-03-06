"""Trading state routes — positions and PnL."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1")


def _get_positions(ctx: Any) -> list[dict[str, Any]]:
    """Get positions from real PositionManager or fallback to list."""
    if ctx.position_manager is not None:
        try:
            positions = []
            for pos in ctx.position_manager.get_all_positions():
                positions.append({
                    "strategy_id": pos.strategy_id,
                    "exchange_id": pos.exchange_id,
                    "symbol": pos.symbol,
                    "side": pos.side,
                    "quantity": float(pos.quantity),
                    "entry_price": float(pos.entry_price),
                    "mark_price": float(pos.mark_price),
                    "unrealized_pnl": float(pos.unrealized_pnl),
                    "realized_pnl": float(pos.realized_pnl),
                })
            return positions
        except Exception as exc:
            logger.warning("Failed to get positions from manager: %s", exc)
    return ctx.positions


def _get_pnl(ctx: Any) -> dict[str, float]:
    """Get PnL from real PositionManager or fallback to context values."""
    if ctx.position_manager is not None:
        try:
            total_realized = float(sum(
                p.realized_pnl for p in ctx.position_manager.get_all_positions()
            ))
            total_unrealized = float(sum(
                p.unrealized_pnl for p in ctx.position_manager.get_all_positions()
            ))
            return {
                "realized_pnl": total_realized,
                "unrealized_pnl": total_unrealized,
                "total_pnl": total_realized + total_unrealized,
            }
        except Exception as exc:
            logger.warning("Failed to get PnL from manager: %s", exc)
    realized = float(ctx.realized_pnl)
    unrealized = float(ctx.unrealized_pnl)
    return {
        "realized_pnl": realized,
        "unrealized_pnl": unrealized,
        "total_pnl": realized + unrealized,
    }


@router.get("/positions")
async def list_positions(request: Request) -> JSONResponse:
    """Return all open positions."""
    ctx = request.app.state.engine_context
    return JSONResponse(_get_positions(ctx))


@router.get("/pnl")
async def get_pnl(request: Request) -> JSONResponse:
    """Return PnL summary (realized, unrealized, total)."""
    ctx = request.app.state.engine_context
    return JSONResponse(_get_pnl(ctx))


@router.get("/status")
async def get_status(request: Request) -> JSONResponse:
    """Return overall engine status."""
    ctx = request.app.state.engine_context
    return JSONResponse({
        "running": ctx.running,
        "kill_switch_active": ctx.kill_switch_active,
        "environment": ctx.environment,
        "execution_mode": ctx.execution_mode,
        "strategy_count": len(ctx.strategies),
        "position_count": len(ctx.positions),
        "connection_count": ctx.ws_manager.connection_count if ctx.ws_manager else 0,
    })
