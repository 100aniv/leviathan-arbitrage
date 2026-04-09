"""Trading state routes — positions and PnL."""
from __future__ import annotations

import asyncio
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
    """Get PnL from real PositionManager, shadow stats, or fallback to context values."""
    # Paper/Shadow mode: use paper stats for PnL
    shadow_mode = getattr(ctx, "paper_mode", None) or getattr(ctx, "shadow_mode", None)
    if shadow_mode is not None and hasattr(shadow_mode, "_stats"):
        stats = shadow_mode._stats
        return {
            "realized_pnl": stats.total_pnl,
            "unrealized_pnl": 0.0,
            "total_pnl": stats.total_pnl,
            # US-F03: session/daily breakdown
            "session_pnl": stats.total_pnl,
            "cumulative_pnl": stats.total_pnl,
            "daily_pnl": getattr(stats, "daily_pnl", 0.0),
            "session_id": getattr(stats, "session_id", ""),
        }
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


@router.get("/positions/live", dependencies=[Depends(require_auth)])
async def get_live_positions(request: Request) -> JSONResponse:
    """
    Query live positions directly from Binance Futures + Bitget Futures.
    Uses engine's existing adapters if running; otherwise creates fresh ones from env.
    Returns per-exchange balances, open positions, and matched cross-exchange hedge pairs.
    """
    ctx = request.app.state.engine_context

    # Try to reuse engine's already-connected adapters
    _TARGETS = ("binance_futures", "bitget_futures")
    adapters: dict[str, Any] = {}
    _owns_adapters = False

    if ctx.engine is not None and hasattr(ctx.engine, "_exchanges"):
        for eid in _TARGETS:
            if eid in ctx.engine._exchanges:
                adapters[eid] = ctx.engine._exchanges[eid]

    # Fallback: create fresh adapters from environment variables
    if not adapters:
        _owns_adapters = True
        from src.infra.exchange import create_native_adapter
        _CREDS: dict[str, dict[str, str]] = {
            "binance_futures": {
                "api_key": os.getenv("BINANCE_API_KEY", ""),
                "api_secret": os.getenv("BINANCE_API_SECRET", ""),
                "passphrase": "",
            },
            "bitget_futures": {
                "api_key": os.getenv("BITGET_API_KEY", ""),
                "api_secret": os.getenv("BITGET_API_SECRET", ""),
                "passphrase": os.getenv("BITGET_PASSPHRASE", ""),
            },
        }
        for eid, cred in _CREDS.items():
            if not cred["api_key"]:
                continue
            try:
                adapter = create_native_adapter(
                    exchange_id=eid,
                    api_key=cred["api_key"],
                    api_secret=cred["api_secret"],
                    passphrase=cred["passphrase"],
                )
                await adapter.connect()
                adapters[eid] = adapter
            except Exception as exc:
                logger.warning("Live positions: failed to connect %s: %s", eid, exc)

    async def _fetch_one(eid: str, adapter: Any) -> dict[str, Any]:
        result: dict[str, Any] = {"exchange_id": eid, "balance_usdt": 0.0, "positions": [], "error": None}
        try:
            positions_raw, balances_raw = await asyncio.gather(
                adapter.get_positions(),
                adapter.get_balances(),
                return_exceptions=True,
            )
            if isinstance(balances_raw, Exception):
                logger.warning("get_balances failed %s: %s", eid, balances_raw)
            else:
                usdt = balances_raw.get("USDT") or balances_raw.get("usdt")
                if usdt is not None:
                    result["balance_usdt"] = float(usdt.total)

            if isinstance(positions_raw, Exception):
                logger.warning("get_positions failed %s: %s", eid, positions_raw)
            else:
                result["positions"] = [
                    {
                        "symbol": p.symbol,
                        "size": float(p.size),
                        "side": "long" if p.size > 0 else "short",
                        "entry_price": float(p.entry_price),
                        "mark_price": float(p.mark_price) if p.mark_price else float(p.entry_price),
                        "unrealized_pnl": float(p.unrealized_pnl),
                    }
                    for p in positions_raw
                    if p.size != 0
                ]
        except Exception as exc:
            result["error"] = str(exc)
            logger.error("Live positions fetch error %s: %s", eid, exc)
        return result

    fetch_tasks = [_fetch_one(eid, adapter) for eid, adapter in adapters.items()]
    results: list[dict[str, Any]] = await asyncio.gather(*fetch_tasks) if fetch_tasks else []

    # Disconnect fresh adapters (not engine's persistent ones)
    if _owns_adapters:
        for adapter in adapters.values():
            try:
                await adapter.disconnect()
            except Exception:
                pass

    # Build exchange map
    by_exchange: dict[str, dict] = {r["exchange_id"]: r for r in results}

    # Match cross-exchange hedge pairs
    all_symbols: set[str] = set()
    for r in results:
        for p in r["positions"]:
            all_symbols.add(p["symbol"])

    hedge_pairs = []
    for sym in sorted(all_symbols):
        binance_pos = next((p for p in by_exchange.get("binance_futures", {}).get("positions", []) if p["symbol"] == sym), None)
        bitget_pos = next((p for p in by_exchange.get("bitget_futures", {}).get("positions", []) if p["symbol"] == sym), None)

        net_pnl = (binance_pos["unrealized_pnl"] if binance_pos else 0.0) + (bitget_pos["unrealized_pnl"] if bitget_pos else 0.0)
        is_hedged = bool(
            binance_pos and bitget_pos and (
                (binance_pos["size"] > 0 and bitget_pos["size"] < 0) or
                (binance_pos["size"] < 0 and bitget_pos["size"] > 0)
            )
        )
        hedge_pairs.append({
            "symbol": sym,
            "is_hedged": is_hedged,
            "net_pnl": round(net_pnl, 4),
            "binance_futures": binance_pos,
            "bitget_futures": bitget_pos,
        })

    total_balance = sum(r.get("balance_usdt", 0.0) for r in results)
    total_unrealized = sum(p["unrealized_pnl"] for r in results for p in r["positions"])

    return JSONResponse({
        "total_balance_usdt": round(total_balance, 2),
        "total_unrealized_pnl": round(total_unrealized, 4),
        "exchanges": [
            {
                "exchange_id": r["exchange_id"],
                "balance_usdt": round(r["balance_usdt"], 2),
                "positions": r["positions"],
                "error": r.get("error"),
            }
            for r in results
        ],
        "hedge_pairs": hedge_pairs,
    })


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
    mode: str | None = None,
    from_date: str | None = Query(default=None, alias="from"),
    to_date: str | None = Query(default=None, alias="to"),
    limit: int = Query(default=50, ge=1, le=1000),
) -> JSONResponse:
    """Return trade history with optional filters."""
    ctx = request.app.state.engine_context
    trades = list(ctx.trade_history)

    # Paper/Shadow mode: use paper_mode._trade_history if ctx.trade_history is empty
    if not trades:
        shadow_mode = getattr(ctx, "paper_mode", None) or getattr(ctx, "shadow_mode", None)
        if shadow_mode is not None and hasattr(shadow_mode, "_trade_history"):
            trades = list(shadow_mode._trade_history)

    # Fallback: query execution_log from DB when both are empty
    if not trades:
        shadow_mode = getattr(ctx, "paper_mode", None) or getattr(ctx, "shadow_mode", None)
        if shadow_mode is not None and hasattr(shadow_mode, "_db_pool") and shadow_mode._db_pool is not None:
            try:
                pool = shadow_mode._db_pool.pool if hasattr(shadow_mode._db_pool, "pool") else shadow_mode._db_pool
                clauses = ["1=1"]
                params: list[Any] = []
                idx = 1
                if strategy:
                    clauses.append(f"strategy_id = ${idx}")
                    params.append(strategy)
                    idx += 1
                if exchange:
                    clauses.append(f"(buy_exchange = ${idx} OR sell_exchange = ${idx})")
                    params.append(exchange)
                    idx += 1
                if symbol:
                    clauses.append(f"symbol = ${idx}")
                    params.append(symbol)
                    idx += 1
                if mode:
                    clauses.append(f"mode = ${idx}")
                    params.append(mode)
                    idx += 1
                if from_date:
                    clauses.append(f"ts >= ${idx}::timestamptz")
                    params.append(from_date)
                    idx += 1
                if to_date:
                    to_cmp = to_date if "T" in to_date else f"{to_date}T23:59:59.999999"
                    clauses.append(f"ts <= ${idx}::timestamptz")
                    params.append(to_cmp)
                    idx += 1
                where = " AND ".join(clauses)
                query = f"SELECT * FROM execution_log WHERE {where} ORDER BY ts DESC LIMIT {limit}"
                async with pool.acquire() as conn:
                    rows = await conn.fetch(query, *params)
                trades = [
                    {
                        "id": str(i),
                        "strategy_id": r["strategy_id"],
                        "symbol": r["symbol"],
                        "buy_exchange": r["buy_exchange"],
                        "sell_exchange": r["sell_exchange"],
                        "side": "arbitrage",
                        "size": float(r["size"]),
                        "entry_price": float(r["buy_price"]),
                        "exit_price": float(r["sell_price"]),
                        "pnl": float(r["net_pnl"]) if r["net_pnl"] is not None else 0.0,
                        "fee": float(r["fee_total"]) if r["fee_total"] is not None else 0.0,
                        "timestamp": r["ts"].isoformat(),
                        "status": r["status"],
                    }
                    for i, r in enumerate(rows)
                ]
                return JSONResponse(trades)
            except Exception as exc:
                logger.warning("Failed to query execution_log: %s", exc)

    if strategy:
        trades = [t for t in trades if t.get("strategy_id") == strategy]
    if exchange:
        trades = [t for t in trades if t.get("buy_exchange") == exchange or t.get("sell_exchange") == exchange]
    if symbol:
        trades = [t for t in trades if t.get("symbol") == symbol]
    if from_date:
        trades = [t for t in trades if t.get("timestamp", "") >= from_date]
    if to_date:
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
    # Fallback: search paper_mode._trade_history
    shadow_mode = getattr(ctx, "paper_mode", None) or getattr(ctx, "shadow_mode", None)
    if shadow_mode is not None and hasattr(shadow_mode, "_trade_history"):
        for trade in shadow_mode._trade_history:
            if trade.get("id") == trade_id:
                detail = dict(trade)
                detail.setdefault("reason", "Paper mode execution")
                detail.setdefault("spread_bps", 0.0)
                detail.setdefault("fee_usd", detail.get("fee", 0.0))
                detail.setdefault("net_pnl", detail.get("pnl", 0.0))
                detail.setdefault("expected_pnl", 0.0)
                return JSONResponse(detail)
    raise HTTPException(status_code=404, detail="Trade not found")


def _get_shadow_by_strategy(ctx: Any) -> dict[str, dict]:
    """Return paper/shadow by_strategy data keyed by strategy_id, or empty dict."""
    shadow_mode = getattr(ctx, "paper_mode", None) or getattr(ctx, "shadow_mode", None)
    if shadow_mode is None:
        return {}
    snapshot = {}
    try:
        snapshot = shadow_mode.get_snapshot() if hasattr(shadow_mode, "get_snapshot") else {}
    except Exception:
        pass
    result: dict[str, dict] = {}
    for entry in snapshot.get("by_strategy", []):
        sid = entry.get("strategy_id", "")
        if sid:
            result[sid] = entry
    return result


def _safe_jsonify(obj: Any) -> Any:
    """Recursively convert Decimal → float for JSON serialization."""
    from decimal import Decimal as _Dec
    if isinstance(obj, _Dec):
        return float(obj)
    if isinstance(obj, dict):
        return {k: _safe_jsonify(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_safe_jsonify(v) for v in obj]
    return obj


@router.get("/strategy-metrics", dependencies=[Depends(require_auth)])
async def get_strategy_metrics(request: Request) -> JSONResponse:
    """Return per-strategy metrics summary."""
    ctx = request.app.state.engine_context
    shadow_by_strategy = _get_shadow_by_strategy(ctx)

    if ctx.strategy_manager is not None:
        try:
            raw = _safe_jsonify(ctx.strategy_manager.get_all_metrics_summary())
            # raw may be {"total_*": ..., "strategies": {...}} or flat {sid: {...}}
            strat_dict = raw.get("strategies", raw) if isinstance(raw, dict) else raw
            # Merge shadow trades/pnl into strategy metrics
            if shadow_by_strategy and isinstance(strat_dict, dict):
                for sid, sd in shadow_by_strategy.items():
                    if sid in strat_dict and isinstance(strat_dict[sid], dict):
                        strat_dict[sid]["trades"] = sd.get("trades", 0)
                        strat_dict[sid]["pnl"] = sd.get("pnl", 0.0)
                        strat_dict[sid]["wins"] = sd.get("wins", 0)
                        strat_dict[sid]["losses"] = sd.get("losses", 0)
                        strat_dict[sid]["win_rate"] = sd.get("win_rate", 0.0)
            return JSONResponse({"strategies": strat_dict})
        except Exception as exc:
            logger.warning("Failed to get metrics from strategy_manager: %s", exc)

    # Fallback: build basic metrics from context.strategies, merged with shadow data
    metrics: dict[str, dict] = {}
    for sid, s in ctx.strategies.items():
        sd = shadow_by_strategy.get(sid, {})
        metrics[sid] = {
            "id": sid,
            "type": s.get("type", "unknown"),
            "enabled": s.get("enabled", True),
            "signals_received": s.get("signals_received", 0),
            "trade_requests": s.get("trade_requests_generated", 0),
            "fills": s.get("fills_received", 0),
            "pnl": sd.get("pnl", s.get("pnl", 0.0)),
            "trades": sd.get("trades", 0),
            "wins": sd.get("wins", 0),
            "losses": sd.get("losses", 0),
            "win_rate": sd.get("win_rate", 0.0),
        }
    # Include shadow-only strategies not in ctx.strategies
    for sid, sd in shadow_by_strategy.items():
        if sid not in metrics:
            metrics[sid] = {
                "id": sid,
                "type": "unknown",
                "enabled": True,
                "signals_received": 0,
                "trade_requests": 0,
                "fills": 0,
                "pnl": sd.get("pnl", 0.0),
                "trades": sd.get("trades", 0),
                "wins": sd.get("wins", 0),
                "losses": sd.get("losses", 0),
                "win_rate": sd.get("win_rate", 0.0),
            }
    return JSONResponse({"strategies": metrics})


@router.get("/status", dependencies=[Depends(require_auth)])
async def get_status(request: Request) -> JSONResponse:
    """Return overall engine status."""
    ctx = request.app.state.engine_context
    mode = ctx.execution_mode
    shadow_mode = getattr(ctx, "paper_mode", None) or getattr(ctx, "shadow_mode", None)
    if shadow_mode is not None and hasattr(shadow_mode, "_stats"):
        mode = "paper"
    strategy_count = len(ctx.strategies)
    if strategy_count == 0 and ctx.strategy_manager is not None:
        try:
            strategy_count = len(ctx.strategy_manager.list_strategies())
        except Exception:
            pass
    return JSONResponse({
        "running": ctx.running,
        "kill_switch_active": ctx.kill_switch_active,
        "environment": ctx.environment,
        "execution_mode": mode,
        "strategy_count": strategy_count,
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
