"""Funding rate routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from src.api.auth import require_auth

router = APIRouter(prefix="/api/v1")


@router.get("/funding-rates", dependencies=[Depends(require_auth)])
async def get_funding_rates(request: Request) -> JSONResponse:
    """Return funding rates keyed by exchange -> symbol -> {rate, next_funding_time, updated_at}."""
    ctx = request.app.state.engine_context
    return JSONResponse(ctx.funding_rates)
