"""Alert history routes."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from src.api.auth import require_auth

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1")


class AlertActionBody(BaseModel):
    alert_id: str


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


@router.post("/alerts/acknowledge", dependencies=[Depends(require_auth)])
async def acknowledge_alert(request: Request, body: AlertActionBody) -> JSONResponse:
    """US-211: Mark an alert as acknowledged."""
    ctx = request.app.state.engine_context
    for alert in ctx.alert_history:
        if alert.get("id") == body.alert_id:
            alert["acknowledged"] = True
            alert["acknowledged_at"] = datetime.now(timezone.utc).isoformat()
            return JSONResponse({"status": "acknowledged", "alert_id": body.alert_id})
    raise HTTPException(status_code=404, detail=f"Alert '{body.alert_id}' not found")


@router.post("/alerts/resolve", dependencies=[Depends(require_auth)])
async def resolve_alert(request: Request, body: AlertActionBody) -> JSONResponse:
    """US-211: Mark an alert as resolved."""
    ctx = request.app.state.engine_context
    for alert in ctx.alert_history:
        if alert.get("id") == body.alert_id:
            alert["resolved"] = True
            alert["resolved_at"] = datetime.now(timezone.utc).isoformat()
            return JSONResponse({"status": "resolved", "alert_id": body.alert_id})
    raise HTTPException(status_code=404, detail=f"Alert '{body.alert_id}' not found")
