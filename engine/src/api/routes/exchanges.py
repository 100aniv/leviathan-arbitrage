"""Exchange status routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from src.api.auth import require_auth

router = APIRouter(prefix="/api/v1")


@router.get("/exchanges", dependencies=[Depends(require_auth)])
async def get_exchange_status(request: Request) -> JSONResponse:
    """Return exchange status keyed by exchange_id -> {connected, latency_ms, orderbook_depth, symbols_count, last_update, balance}."""
    ctx = request.app.state.engine_context
    return JSONResponse(ctx.exchange_status)
