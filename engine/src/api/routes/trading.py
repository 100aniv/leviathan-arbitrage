"""Trading state routes — positions and PnL."""
from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from src.api.auth import require_auth

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
            positions = list(ctx.position_manager.get_all_positions())
            total_realized = float(sum(
                p.realized_pnl for p in positions
            ))
            total_unrealized = float(sum(
                p.unrealized_pnl for p in positions
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


@router.get("/positions", dependencies=[Depends(require_auth)])
async def list_positions(request: Request) -> JSONResponse:
    """Return all open positions."""
    ctx = request.app.state.engine_context
    return JSONResponse(_get_positions(ctx))


@router.get("/pnl", dependencies=[Depends(require_auth)])
async def get_pnl(request: Request) -> JSONResponse:
    """Return PnL summary (realized, unrealized, total)."""
    ctx = request.app.state.engine_context
    return JSONResponse(_get_pnl(ctx))


@router.get("/trades", dependencies=[Depends(require_auth)])
async def list_trades(
    request: Request,
    strategy: str | None = None,
    exchange: str | None = None,
    symbol: str | None = None,
    from_date: str | None = Query(default=None, alias="from"),
    to_date: str | None = Query(default=None, alias="to"),
    limit: int = Query(default=50, ge=1, le=1000),
) -> JSONResponse:
    """Return trade history with optional filters."""
    ctx = request.app.state.engine_context
    trades = list(ctx.trade_history)
    if strategy:
        trades = [t for t in trades if t.get("strategy_id") == strategy]
    if exchange:
        trades = [t for t in trades if t.get("buy_exchange") == exchange or t.get("sell_exchange") == exchange]
    if symbol:
        trades = [t for t in trades if t.get("symbol") == symbol]
    if from_date:
        trades = [t for t in trades if t.get("timestamp", "") >= from_date]
    if to_date:
        # Append end-of-day time if only date provided (e.g. "2026-03-12" → "2026-03-12T23:59:59.999999")
        to_cmp = to_date if "T" in to_date else f"{to_date}T23:59:59.999999"
        trades = [t for t in trades if t.get("timestamp", "") <= to_cmp]
    trades = sorted(trades, key=lambda t: t.get("timestamp", ""), reverse=True)
    return JSONResponse(trades[:limit])


@router.get("/trades/{trade_id}", dependencies=[Depends(require_auth)])
async def get_trade_detail(request: Request, trade_id: str) -> JSONResponse:
    """Return detailed trade info including reason and fee breakdown."""
    ctx = request.app.state.engine_context
    for trade in ctx.trade_history:
        if trade.get("id") == trade_id:
            detail = dict(trade)
            detail.setdefault("reason", "Cross-exchange spread detected")
            detail.setdefault("spread_bps", 0.0)
            detail.setdefault("fee_usd", 0.0)
            detail.setdefault("net_pnl", detail.get("pnl", 0.0))
            detail.setdefault("expected_pnl", 0.0)
            return JSONResponse(detail)
    raise HTTPException(status_code=404, detail="Trade not found")


@router.get("/strategy-metrics", dependencies=[Depends(require_auth)])
async def get_strategy_metrics(request: Request) -> JSONResponse:
    """Return per-strategy metrics summary."""
    ctx = request.app.state.engine_context
    if ctx.strategy_manager is not None:
        try:
            return JSONResponse({"strategies": ctx.strategy_manager.get_all_metrics_summary()})
        except Exception as exc:
            logger.warning("Failed to get metrics from strategy_manager: %s", exc)
    # Fallback: build basic metrics from context.strategies
    metrics = {
        sid: {
            "id": sid,
            "type": s.get("type", "unknown"),
            "enabled": s.get("enabled", True),
            "signals_received": s.get("signals_received", 0),
            "trade_requests": s.get("trade_requests_generated", 0),
            "fills": s.get("fills_received", 0),
            "pnl": s.get("pnl", 0.0),
        }
        for sid, s in ctx.strategies.items()
    }
    return JSONResponse({"strategies": metrics})


@router.get("/status", dependencies=[Depends(require_auth)])
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


@router.get("/symbols", dependencies=[Depends(require_auth)])
async def get_symbols(request: Request) -> JSONResponse:
    """Return active trading symbol list from collector_manager or env config."""
    ctx = request.app.state.engine_context

    symbols: list[str] = []

    # Source 1: collector_manager.get_active_symbols() via engine
    engine = getattr(ctx, "engine", None)
    if engine is not None:
        cm = getattr(engine, "collector_manager", None)
        if cm is not None:
            try:
                symbols = list(cm.get_active_symbols())
            except Exception as exc:
                logger.warning("collector_manager.get_active_symbols() failed: %s", exc)

    # Source 2: runtime_settings injected by main.py
    if not symbols:
        symbols = ctx.runtime_settings.get("trading_symbols", [])

    # Source 3: TRADING_SYMBOLS env var
    if not symbols:
        env_symbols = os.environ.get("TRADING_SYMBOLS", "")
        if env_symbols:
            symbols = [s.strip() for s in env_symbols.split(",") if s.strip()]

    return JSONResponse({"symbols": symbols, "count": len(symbols)})


@router.get("/spreads", dependencies=[Depends(require_auth)])
async def get_spreads(request: Request) -> JSONResponse:
    """Return current exchange×symbol spread snapshot from SignalGenerator."""
    ctx = request.app.state.engine_context

    spreads: list[dict[str, Any]] = []

    engine = getattr(ctx, "engine", None)
    if engine is not None:
        # Try SignalGenerator snapshot
        sg = getattr(engine, "signal_generator", None)
        if sg is not None:
            try:
                snapshot = sg.get_spread_snapshot() if hasattr(sg, "get_spread_snapshot") else {}
                for key, data in snapshot.items():
                    spreads.append({
                        "symbol": data.get("symbol", key),
                        "exchange_a": data.get("exchange_a", ""),
                        "exchange_b": data.get("exchange_b", ""),
                        "spread_bps": data.get("spread_bps", 0.0),
                        "timestamp": data.get("timestamp", ""),
                    })
            except Exception as exc:
                logger.warning("SignalGenerator.get_spread_snapshot() failed: %s", exc)

        # Fallback: PriceHub snapshot
        if not spreads:
            ph = getattr(engine, "price_hub", None)
            if ph is not None:
                try:
                    snapshot = ph.get_snapshot() if hasattr(ph, "get_snapshot") else {}
                    for symbol, prices in snapshot.items():
                        if isinstance(prices, dict) and len(prices) >= 2:
                            exs = list(prices.keys())
                            p_a = float(prices[exs[0]])
                            p_b = float(prices[exs[1]])
                            mid = (p_a + p_b) / 2 if (p_a + p_b) > 0 else 1.0
                            spread_bps = abs(p_a - p_b) / mid * 10000
                            spreads.append({
                                "symbol": symbol,
                                "exchange_a": exs[0],
                                "exchange_b": exs[1],
                                "spread_bps": round(spread_bps, 2),
                                "timestamp": "",
                            })
                except Exception as exc:
                    logger.warning("PriceHub.get_snapshot() failed: %s", exc)

    return JSONResponse(spreads)
