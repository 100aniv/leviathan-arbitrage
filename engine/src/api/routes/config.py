"""API route: GET /api/v1/config/exchanges — returns exchanges_meta.json (US-383).

⚡ WIRING:
  생성: This module defines config_router with /api/v1/config/exchanges GET endpoint.
  주입: server.py must import and include config_router (see server.py).
  호출: curl http://localhost:8080/api/v1/config/exchanges → 200 JSON response.
"""
from __future__ import annotations

import json
import pathlib

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/v1/config", tags=["config"])

_META_PATH = pathlib.Path(__file__).resolve().parents[3] / "config" / "exchanges_meta.json"


@router.get("/exchanges", summary="Exchanges metadata (backtest capability, tiers, etc.)")
async def get_exchanges_meta() -> dict:
    """Return exchanges_meta.json content.

    Contains per-exchange: tier, paper_capable, backtest data_type/precision,
    orderbook/OHLCV data sources, and download URLs.
    """
    if not _META_PATH.exists():
        raise HTTPException(status_code=404, detail=f"exchanges_meta.json not found at {_META_PATH}")
    try:
        return json.loads(_META_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"exchanges_meta.json parse error: {exc}")
