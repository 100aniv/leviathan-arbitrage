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

    # US-282: use get_report() with backward-compatible top-level fields
    try:
        report = attribution.get_report()
        # Backward compat: ensure top-level total_trades/total_pnl exist
        summary = attribution.summary()
        report.setdefault("total_trades", summary.get("total_trades", 0))
        report.setdefault("total_pnl", summary.get("total_pnl", 0.0))
        report.setdefault("win_rate", summary.get("win_rate", 0.0))
        return JSONResponse(report)
    except AttributeError:
        return JSONResponse(attribution.summary())
