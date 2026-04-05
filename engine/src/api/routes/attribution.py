"""Attribution API route — PnL breakdown by strategy, exchange, pair, hour."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from src.api.auth import require_auth
from src.analysis.attribution import PerformanceAttribution, TradeRecord

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1")


def _build_trade_record(trade: dict[str, Any]) -> TradeRecord | None:
    """Convert a trade dict from engine_context.trade_history to TradeRecord."""
    try:
        ts_raw = trade.get("timestamp", "")
        if isinstance(ts_raw, datetime):
            ts = ts_raw
        elif isinstance(ts_raw, (int, float)):
            ts = datetime.fromtimestamp(ts_raw, tz=timezone.utc)
        elif isinstance(ts_raw, str) and ts_raw:
            ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
        else:
            ts = datetime.now(tz=timezone.utc)

        entry_price = float(trade.get("entry_price") or 0.0)
        size = float(trade.get("size") or 0.0)

        return TradeRecord(
            trade_id=str(trade.get("id", "")),
            timestamp=ts,
            strategy_id=str(trade.get("strategy_id", "unknown")),
            exchange_buy=str(trade.get("buy_exchange", "unknown")),
            exchange_sell=str(trade.get("sell_exchange", "unknown")),
            pair=str(trade.get("symbol", "unknown")),
            pnl=float(trade.get("pnl") or 0.0),
            size_usd=size * entry_price,
        )
    except Exception as exc:
        logger.warning("Skipping trade record conversion: %s", exc)
        return None


def _build_attribution_from_shadow(shadow_mode: Any) -> "PerformanceAttribution":
    """Build ephemeral PerformanceAttribution from shadow _trade_history."""
    attribution = PerformanceAttribution()
    trade_history = getattr(shadow_mode, "_trade_history", [])
    for trade in trade_history:
        record = _build_trade_record(trade)
        if record is not None:
            attribution.add_trade(record)
    return attribution


@router.get("/attribution", dependencies=[Depends(require_auth)])
async def get_attribution(request: Request) -> JSONResponse:
    """Return PnL attribution breakdown across all dimensions."""
    ctx = request.app.state.engine_context

    # US-284-b: prefer live attribution instance from EngineContext
    attribution = getattr(ctx, "attribution", None)
    if attribution is None:
        # No live instance — build ephemeral from trade_history (no duplicate risk)
        attribution = PerformanceAttribution()
        for trade in ctx.trade_history:
            record = _build_trade_record(trade)
            if record is not None:
                attribution.add_trade(record)
    # Live instance already accumulates on_fill — do NOT re-add from trade_history

    # Shadow mode fallback: if attribution is empty and shadow has trade history, use it
    shadow_mode = getattr(ctx, "paper_mode", None) or getattr(ctx, "shadow_mode", None)
    if shadow_mode is not None and hasattr(shadow_mode, "_trade_history"):
        attr_summary = attribution.summary()
        if attr_summary.get("total_trades", 0) == 0 and len(shadow_mode._trade_history) > 0:
            attribution = _build_attribution_from_shadow(shadow_mode)

    # US-282: use get_report() with backward-compatible top-level fields
    try:
        report = attribution.get_report()
        # Backward compat: ensure top-level total_trades/total_pnl exist
        summary = attribution.summary()
        report.setdefault("total_trades", summary.get("total_trades", 0))
        report.setdefault("total_pnl", summary.get("total_pnl", 0.0))
        report.setdefault("win_rate", summary.get("win_rate", 0.0))
        # Enrich with shadow stats when available for accurate totals
        if shadow_mode is not None and hasattr(shadow_mode, "_stats"):
            stats = shadow_mode._stats
            total_trades = stats.trades_executed
            report["total_trades"] = total_trades
            report["total_pnl"] = round(stats.total_pnl, 6)
            report["win_rate"] = round(stats.trades_won / total_trades, 4) if total_trades > 0 else 0.0
        return JSONResponse(report)
    except AttributeError:
        return JSONResponse(attribution.summary())
