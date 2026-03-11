"""TCA (Transaction Cost Analysis) API routes — US-116."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from src.api.auth import require_auth

router = APIRouter(prefix="/api/v1/tca", tags=["tca"])


@router.get("/summary", dependencies=[Depends(require_auth)])
async def get_tca_summary(request: Request) -> JSONResponse:
    ctx = request.app.state.engine_context
    tca = getattr(ctx, "tca_analyzer", None)
    if tca is None:
        return JSONResponse({
            "is_p50_bps": 0, "is_p95_bps": 0,
            "latency_p50_ms": 0, "latency_p95_ms": 0, "latency_p99_ms": 0,
            "fill_rate_pct": 0, "sample_count": 0,
        })
    return JSONResponse(tca.get_summary())
