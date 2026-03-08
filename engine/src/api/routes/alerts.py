"""Alert history routes."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from src.api.auth import require_auth

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1")


@router.get("/alerts", dependencies=[Depends(require_auth)])
async def list_alerts(request: Request, severity: str | None = None, limit: int = 100) -> JSONResponse:
    """Return alert history, optionally filtered by severity.

    Alert structure: {id, type, severity, message, timestamp, metadata}
    severity values: critical, warning, info
    type values: kill_switch, ws_disconnect, drawdown, exchange_error
    """
    ctx = request.app.state.engine_context
    alerts: list[dict[str, Any]] = list(ctx.alert_history)
    if severity:
        alerts = [a for a in alerts if a.get("severity") == severity]
    alerts = sorted(alerts, key=lambda a: a.get("timestamp", ""), reverse=True)
    return JSONResponse(alerts[:limit])
