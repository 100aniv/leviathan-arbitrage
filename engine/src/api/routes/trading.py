"""Trading state routes — positions and PnL."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/api/v1")


@router.get("/positions")
async def list_positions(request: Request) -> JSONResponse:
    """Return all open positions."""
    ctx = request.app.state.engine_context
    return JSONResponse(ctx.positions)


@router.get("/pnl")
async def get_pnl(request: Request) -> JSONResponse:
    """Return PnL summary (realized, unrealized, total)."""
    ctx = request.app.state.engine_context
    realized = float(ctx.realized_pnl)
    unrealized = float(ctx.unrealized_pnl)
    return JSONResponse({
        "realized_pnl": realized,
        "unrealized_pnl": unrealized,
        "total_pnl": realized + unrealized,
    })


@router.get("/status")
async def get_status(request: Request) -> JSONResponse:
    """Return overall engine status."""
    ctx = request.app.state.engine_context
    return JSONResponse({
        "running": ctx.running,
        "kill_switch_active": ctx.kill_switch_active,
        "environment": ctx.environment,
        "strategy_count": len(ctx.strategies),
        "position_count": len(ctx.positions),
        "connection_count": ctx.ws_manager.connection_count if ctx.ws_manager else 0,
    })
