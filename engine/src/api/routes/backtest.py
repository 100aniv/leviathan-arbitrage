"""Backtest API routes — US-351/353."""
from __future__ import annotations

import json
import pathlib
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

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
