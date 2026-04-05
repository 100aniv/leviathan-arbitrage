"""Backtest API routes — US-351/353/361."""
from __future__ import annotations

import json
import pathlib
from typing import Any, Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from src.api.auth import require_auth

router = APIRouter(prefix="/api/backtest", tags=["backtest"])

# Project root: engine/src/api/routes/ → 5 parents up → arbitrage_OMC/
_PROJECT_ROOT = pathlib.Path(__file__).parent.parent.parent.parent.parent
_RESULTS_FILE = _PROJECT_ROOT / ".omc" / "state" / "backtest_results.json"


@router.get("/result", dependencies=[Depends(require_auth)])
async def get_backtest_result(request: Request) -> JSONResponse:
    """Return the latest BacktestMode run result."""
    ctx = request.app.state.engine_context
    result = getattr(ctx, "backtest_result", None)
    if result is not None:
        return JSONResponse({
            "snapshots_replayed": getattr(result, "snapshots_replayed", 0),
            "signals_generated": getattr(result, "signals_generated", 0),
            "trades_executed": getattr(result, "trades_executed", 0),
            "total_pnl": getattr(result, "total_pnl", 0.0),
            "sharpe_ratio": getattr(result, "sharpe_ratio", 0.0),
            "max_drawdown_pct": getattr(result, "max_drawdown_pct", 0.0),
            "win_rate": getattr(result, "win_rate", 0.0),
            "profit_factor": getattr(result, "profit_factor", 0.0),
            "duration_s": getattr(result, "duration_s", 0.0),
            "by_strategy": getattr(result, "by_strategy", {}),
            "error": getattr(result, "error", ""),
            "strategy_ids": getattr(result, "strategy_ids", []),
            "exchange_ids": getattr(result, "exchange_ids", []),
            "seed_capital": getattr(result, "seed_capital", 0.0),
            "period_label": getattr(result, "period_label", ""),
            "by_exchange": getattr(result, "by_exchange", {}),
        })
    # Fallback: load from saved JSON
    try:
        if _RESULTS_FILE.exists():
            data = json.loads(_RESULTS_FILE.read_text())
            return JSONResponse(data.get("backtest", {}))
    except Exception:
        pass
    return JSONResponse({"error": "no_backtest_result"}, status_code=404)


@router.get("/wfa", dependencies=[Depends(require_auth)])
async def get_wfa_result(request: Request) -> JSONResponse:
    """Return Walk-Forward Analysis results for all 6 strategies."""
    ctx = request.app.state.engine_context
    wfa_results = getattr(ctx, "wfa_results", None)
    if wfa_results:
        return JSONResponse(wfa_results)
    # Fallback: load from saved JSON
    try:
        if _RESULTS_FILE.exists():
            data = json.loads(_RESULTS_FILE.read_text())
            wfa = data.get("wfa", {})
            if wfa:
                return JSONResponse(wfa)
    except Exception:
        pass
    return JSONResponse({"error": "no_wfa_result"}, status_code=404)


class BacktestStartRequest(BaseModel):
    strategy_ids: list[str] = []
    exchange_ids: list[str] = []
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    seed_capital: float = 1000.0
    symbols: list[str] = []
    # US-368~371 batch meta
    run_id: str = ""
    batch_id: str = ""
    metadata: dict = {}


@router.post("/start", dependencies=[Depends(require_auth)])
async def start_backtest(request: Request, body: BacktestStartRequest) -> JSONResponse:
    """Trigger a backtest run with user-specified parameters (US-361)."""
    ctx = request.app.state.engine_context
    backtest_mode = getattr(ctx, "backtest_mode", None)
    if backtest_mode is None:
        return JSONResponse({"error": "backtest_mode_not_initialized"}, status_code=503)

    # 동시 실행 방지 — 이미 실행 중이면 409
    if getattr(backtest_mode, "_running", False):
        return JSONResponse({"error": "backtest_already_running"}, status_code=409)

    if body.start_date:
        backtest_mode._start_time = body.start_date
    if body.end_date:
        backtest_mode._end_time = body.end_date
    if body.symbols:
        backtest_mode._symbols = body.symbols
    if body.exchange_ids:
        backtest_mode._exchanges = body.exchange_ids
    if body.strategy_ids:
        backtest_mode._strategy_ids = body.strategy_ids
    if body.seed_capital:
        backtest_mode._seed_capital = body.seed_capital
    if body.run_id:
        backtest_mode._run_id = body.run_id
    if body.batch_id:
        backtest_mode._batch_id = body.batch_id
    if body.metadata:
        backtest_mode._metadata = body.metadata

    import asyncio
    asyncio.create_task(backtest_mode.run())

    return JSONResponse({"status": "started", "params": body.model_dump()})


class DownloadHistoryRequest(BaseModel):
    exchange: str = "binance"
    symbols: list[str] = ["BTC/USDT"]
    start: str = "2025-01-01"
    end: str = "2026-01-01"


@router.post("/download_history", dependencies=[Depends(require_auth)])
async def download_history(request: Request, body: DownloadHistoryRequest) -> JSONResponse:
    """Download OHLCV history and store as synthetic orderbook (US-362)."""
    from src.infra.db.ohlcv_downloader import OHLCVDownloader

    ctx = request.app.state.engine_context
    db_pool = getattr(ctx, "db_pool", None)
    downloader = OHLCVDownloader(db_pool=db_pool)

    total = 0
    for symbol in body.symbols:
        count = await downloader.download_and_store(
            exchange=body.exchange,
            symbol=symbol,
            start_date=body.start,
            end_date=body.end,
        )
        total += count

    return JSONResponse({"status": "done", "snapshots_stored": total, "symbols": body.symbols})


@router.get("/batch_results", dependencies=[Depends(require_auth)])
async def get_batch_results(request: Request) -> JSONResponse:
    """Return all K-BT batch backtest results from .omc/state/."""
    results = []
    state_dir = _PROJECT_ROOT / ".omc" / "state"
    for f in sorted(state_dir.glob("backtest-summary-K-BT-*.json")):
        try:
            data = json.loads(f.read_text())
            results.append(data)
        except Exception:
            pass
    return JSONResponse(results)


@router.get("/data_availability", dependencies=[Depends(require_auth)])
async def data_availability(request: Request) -> JSONResponse:
    """Check if synthetic OHLCV data is available (US-362)."""
    ctx = request.app.state.engine_context
    db_pool = getattr(ctx, "db_pool", None)
    if db_pool is None:
        return JSONResponse({"available": False, "reason": "no_db_pool"})
    try:
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT COUNT(*) as cnt, MIN(timestamp) as min_ts, MAX(timestamp) as max_ts "
                "FROM orderbook_snapshots WHERE source = 'ohlcv_synthetic'"
            )
            count = row["cnt"] if row else 0
            return JSONResponse({
                "available": count > 0,
                "synthetic_snapshots": count,
                "min_ts": str(row["min_ts"]) if row and row["min_ts"] else None,
                "max_ts": str(row["max_ts"]) if row and row["max_ts"] else None,
            })
    except Exception as exc:
        return JSONResponse({"available": False, "reason": str(exc)})
