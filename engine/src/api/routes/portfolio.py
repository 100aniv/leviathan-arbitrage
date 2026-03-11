"""Portfolio summary route — total balance, per-exchange breakdown."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from src.api.auth import require_auth

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1")


def _get_exchange_balances(ctx: Any) -> list[dict[str, Any]]:
    """Build per-exchange balance list from available data sources."""
    balances: dict[str, float] = {}

    # Source 1: Shadow mode VirtualBalanceTracker (highest priority)
    shadow_mode = getattr(ctx, "shadow_mode", None)
    if shadow_mode is not None:
        tracker = getattr(shadow_mode, "_balance_tracker", None)
        if tracker is not None:
            raw = tracker.summary()  # dict[str, str] — use public method
            for ex_id, bal_str in raw.items():
                try:
                    balances[ex_id] = float(bal_str)
                except (ValueError, TypeError):
                    pass

    # Source 2: exchange_status balance field (fallback for Paper/Live)
    if not balances and ctx.exchange_status:
        for ex_id, status in ctx.exchange_status.items():
            bal = status.get("balance", {}) if isinstance(status, dict) else {}
            usdt = bal.get("USDT", bal.get("usdt", 0.0))
            balances[ex_id] = float(usdt)

    # Build response with connection info
    result = []
    total = sum(balances.values()) if balances else 0.0

    # Shadow mode tracks balances = exchange is effectively connected
    has_shadow = getattr(ctx, "shadow_mode", None) is not None

    for ex_id, bal in sorted(balances.items()):
        connected = False
        if ctx.exchange_status and ex_id in ctx.exchange_status:
            es = ctx.exchange_status[ex_id]
            connected = es.get("connected", False) if isinstance(es, dict) else False
        elif has_shadow:
            # Shadow is tracking this exchange → treat as connected
            connected = True

        result.append({
            "exchange_id": ex_id,
            "balance_usdt": round(bal, 2),
            "connected": connected,
            "pct_of_total": round(bal / total, 4) if total > 0 else 0.0,
        })

    return result


@router.get("/portfolio-summary", dependencies=[Depends(require_auth)])
async def get_portfolio_summary(request: Request) -> JSONResponse:
    """Return portfolio summary with per-exchange balance breakdown."""
    ctx = request.app.state.engine_context

    exchange_balances = _get_exchange_balances(ctx)
    total_balance = sum(eb["balance_usdt"] for eb in exchange_balances)

    # PnL from context (None-safe)
    realized = float(ctx.realized_pnl or 0)
    unrealized = float(ctx.unrealized_pnl or 0)
    total_pnl = realized + unrealized

    # Position count
    position_count = 0
    pm = getattr(ctx, "position_manager", None)
    if pm is not None:
        try:
            position_count = len(pm.get_all_positions())
        except (AttributeError, TypeError, RuntimeError):
            position_count = len(ctx.positions)
    else:
        position_count = len(ctx.positions)

    return JSONResponse({
        "total_balance_usdt": round(total_balance, 2),
        "total_pnl": round(total_pnl, 6),
        "pnl_scope": "session",
        "active_positions": position_count,
        "exchange_balances": exchange_balances,
        "mode": ctx.execution_mode,
        "last_updated": datetime.now(timezone.utc).isoformat(),
    })


@router.get("/portfolio/equity-curve", dependencies=[Depends(require_auth)])
async def get_equity_curve(request: Request) -> JSONResponse:
    """Return daily equity curve data for charting."""
    ctx = request.app.state.engine_context

    initial_capital = ctx.runtime_settings.get("initial_capital", 100000)

    # Build equity curve from shadow mode trade history or context
    curve_data = []
    shadow = getattr(ctx, "shadow_mode", None)
    if shadow is not None:
        snapshot = shadow.get_snapshot() if hasattr(shadow, 'get_snapshot') else {}
        total_pnl = float(snapshot.get("total_pnl", 0))
        # Single point for current session (historical data requires TimescaleDB query)
        curve_data.append({
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "equity": round(total_pnl + initial_capital, 2),
            "pnl": round(total_pnl, 6),
            "btc_benchmark": None,  # requires BTC price tracking — not implemented
        })

    if not curve_data:
        # Fallback: single point from current balance
        total = float(ctx.realized_pnl or 0) + float(ctx.unrealized_pnl or 0)
        curve_data.append({
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "equity": round(total + initial_capital, 2),
            "pnl": round(total, 6),
            "btc_benchmark": None,
        })

    return JSONResponse({"curve": curve_data})


@router.get("/portfolio/metrics", dependencies=[Depends(require_auth)])
async def get_portfolio_metrics(request: Request) -> JSONResponse:
    """Return risk metrics: Sharpe, MDD, Calmar, win rate."""
    ctx = request.app.state.engine_context

    metrics: dict[str, Any] = {
        "sharpe_ratio": None,
        "max_drawdown_pct": 0.0,
        "calmar_ratio": None,
        "win_rate": 0.0,
        "total_trades": 0,
        "total_pnl": 0.0,
    }

    shadow = getattr(ctx, "shadow_mode", None)
    if shadow is not None:
        snapshot = shadow.get_snapshot() if hasattr(shadow, 'get_snapshot') else {}
        metrics["total_pnl"] = float(snapshot.get("total_pnl", 0))
        metrics["win_rate"] = float(snapshot.get("win_rate", 0))
        metrics["total_trades"] = int(snapshot.get("total_trades", 0))
        metrics["max_drawdown_pct"] = float(snapshot.get("max_drawdown", 0)) * 100

        # Calmar = annualized return / max drawdown (requires ≥1 day of session data)
        mdd = metrics["max_drawdown_pct"]
        if mdd > 0:
            initial_capital = ctx.runtime_settings.get("initial_capital", 100000)
            session_start_ts = ctx.runtime_settings.get("session_start_ts")
            if session_start_ts is not None:
                elapsed_days = (datetime.now(timezone.utc).timestamp() - float(session_start_ts)) / 86400
            else:
                elapsed_days = 0
            if elapsed_days >= 1:
                annualized_return_pct = (metrics["total_pnl"] / initial_capital * 100) / elapsed_days * 365
                metrics["calmar_ratio"] = round(annualized_return_pct / mdd, 2)

    return JSONResponse(metrics)
