"""WS-C2: /api/v1/positions/hedge-pairs — grouped hedge-pair view.

For each symbol with open positions on BOTH Binance futures and Bitget futures,
emit a single row containing both legs and a net unrealized total. Symbols with
only one leg open are surfaced under ``unpaired_positions`` so the dashboard
can flag orphaned exposure.

Uses the engine's existing adapters (the same pattern as /api/v1/positions/live)
so this endpoint works only while the live engine is running. Graceful degrade:
returns ``{pairs: [], unpaired_positions: [], total_pairs: 0, total_unrealized: 0.0}``
when no adapters are available.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from src.api.auth import require_auth

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1")

_FUTURES_EXCHANGES = ("binance_futures", "bitget_futures")


def _leg_side(size: float) -> str:
    return "LONG" if size > 0 else "SHORT"


def _get_adapter_ids(engine: Any) -> dict[str, Any]:
    """Return {exchange_id: adapter} for Binance+Bitget futures from engine state."""
    adapters: dict[str, Any] = {}
    if engine is None or not hasattr(engine, "_exchanges"):
        return adapters
    for eid in _FUTURES_EXCHANGES:
        a = engine._exchanges.get(eid)
        if a is not None:
            adapters[eid] = a
    return adapters


async def _fetch_positions(eid: str, adapter: Any) -> list[dict[str, Any]]:
    """Fetch positions for one adapter; returns [] on error."""
    try:
        raw = await adapter.get_positions()
    except Exception as exc:  # noqa: BLE001
        logger.warning("hedge_pairs.get_positions_failed eid=%s err=%s", eid, exc)
        return []
    out: list[dict[str, Any]] = []
    for p in raw:
        try:
            size = float(p.size)
            if size == 0:
                continue
            entry = float(p.entry_price)
            mark = float(p.mark_price) if getattr(p, "mark_price", None) else entry
            out.append({
                "exchange_id": eid,
                "symbol": str(p.symbol),
                "side": _leg_side(size),
                "size": abs(size),
                "entry_price": entry,
                "mark_price": mark,
                "unrealized_pnl": round(float(p.unrealized_pnl), 4),
                "opened_at": getattr(p, "opened_at", None),
            })
        except Exception as exc:  # noqa: BLE001
            logger.debug("hedge_pairs.position_parse_failed eid=%s err=%s", eid, exc)
    return out


def _position_manager_age_seconds(ctx: Any, exchange_id: str, symbol: str) -> int:
    """Best-effort age from PositionManager — returns 0 if not tracked."""
    pm = getattr(ctx, "position_manager", None)
    if pm is None:
        return 0
    try:
        now_ts = time.time()
        for pos in pm.get_all_positions():
            if pos.exchange_id == exchange_id and pos.symbol == symbol:
                opened = getattr(pos, "opened_at", None) or getattr(pos, "entry_time", None)
                if opened is None:
                    return 0
                # opened may be datetime or epoch float
                if hasattr(opened, "timestamp"):
                    return max(0, int(now_ts - opened.timestamp()))
                try:
                    return max(0, int(now_ts - float(opened)))
                except Exception:  # noqa: BLE001
                    return 0
    except Exception as exc:  # noqa: BLE001
        logger.debug("hedge_pairs.age_lookup_failed err=%s", exc)
    return 0


@router.get("/positions/hedge-pairs", dependencies=[Depends(require_auth)])
async def get_hedge_pairs(request: Request) -> JSONResponse:
    """Return cross-exchange hedge pair view (Binance futures × Bitget futures)."""
    ctx = request.app.state.engine_context
    engine = getattr(ctx, "engine", None)

    adapters = _get_adapter_ids(engine)
    if not adapters:
        return JSONResponse({
            "pairs": [],
            "unpaired_positions": [],
            "total_pairs": 0,
            "total_unrealized": 0.0,
        })

    results = await asyncio.gather(
        *(_fetch_positions(eid, ad) for eid, ad in adapters.items()),
        return_exceptions=True,
    )

    by_exchange: dict[str, list[dict[str, Any]]] = {}
    for eid, res in zip(adapters.keys(), results):
        by_exchange[eid] = res if isinstance(res, list) else []

    # Index per symbol
    binance_by_sym: dict[str, dict[str, Any]] = {
        p["symbol"]: p for p in by_exchange.get("binance_futures", [])
    }
    bitget_by_sym: dict[str, dict[str, Any]] = {
        p["symbol"]: p for p in by_exchange.get("bitget_futures", [])
    }
    all_symbols = sorted(set(binance_by_sym.keys()) | set(bitget_by_sym.keys()))

    pairs: list[dict[str, Any]] = []
    unpaired: list[dict[str, Any]] = []
    total_unrealized = 0.0

    for sym in all_symbols:
        b_leg = binance_by_sym.get(sym)
        g_leg = bitget_by_sym.get(sym)
        if b_leg and g_leg:
            age_b = _position_manager_age_seconds(ctx, "binance_futures", sym)
            age_g = _position_manager_age_seconds(ctx, "bitget_futures", sym)
            net = round(b_leg["unrealized_pnl"] + g_leg["unrealized_pnl"], 4)
            pairs.append({
                "symbol": sym,
                "binance_leg": {
                    "side": b_leg["side"],
                    "size": b_leg["size"],
                    "entry_price": b_leg["entry_price"],
                    "mark_price": b_leg["mark_price"],
                    "unrealized_pnl": b_leg["unrealized_pnl"],
                },
                "bitget_leg": {
                    "side": g_leg["side"],
                    "size": g_leg["size"],
                    "entry_price": g_leg["entry_price"],
                    "mark_price": g_leg["mark_price"],
                    "unrealized_pnl": g_leg["unrealized_pnl"],
                },
                "net_unrealized": net,
                "age_seconds": max(age_b, age_g),
            })
            total_unrealized += net
        else:
            leg = b_leg or g_leg
            if leg is None:
                continue
            unpaired.append({
                "exchange_id": leg["exchange_id"],
                "symbol": sym,
                "side": leg["side"],
                "size": leg["size"],
                "entry_price": leg["entry_price"],
                "mark_price": leg["mark_price"],
                "unrealized_pnl": leg["unrealized_pnl"],
            })
            total_unrealized += leg["unrealized_pnl"]

    return JSONResponse({
        "pairs": pairs,
        "unpaired_positions": unpaired,
        "total_pairs": len(pairs),
        "total_unrealized": round(total_unrealized, 4),
    })
