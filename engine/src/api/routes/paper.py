"""Paper mode API routes — US-363/372/332."""
from __future__ import annotations

import json
import pathlib
import re
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from src.api.auth import require_auth

router = APIRouter(prefix="/api/paper", tags=["paper"])

_PROJECT_ROOT = pathlib.Path(__file__).parent.parent.parent.parent.parent
_STATE_DIR = _PROJECT_ROOT / ".omc" / "state"
_CUMULATIVE_FILE = _STATE_DIR / "paper-cumulative-hours.json"


def _load_cumulative() -> dict:
    if _CUMULATIVE_FILE.exists():
        try:
            return json.loads(_CUMULATIVE_FILE.read_text())
        except Exception:
            pass
    return {"total_hours": 0.0, "sessions": [], "target_hours": 24.0, "satisfied": False}


def _save_cumulative(data: dict) -> None:
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    _CUMULATIVE_FILE.write_text(json.dumps(data, indent=2))


class PaperStartRequest(BaseModel):
    strategy_ids: list[str] = []
    exchange_ids: list[str] = []
    # US-372: singular params for single-strategy paper run
    exchange_id: str = ""
    strategy_id: str = ""
    symbols: list[str] = []
    seed_capital: float = 0.0
    duration_hours: int = 0  # 0 = unlimited
    # US-388: force_enable bypasses strategy_activation.json
    force_enable: bool = False


@router.post("/start", dependencies=[Depends(require_auth)])
async def start_paper(request: Request, body: PaperStartRequest) -> JSONResponse:
    """Start paper mode with user-specified parameters (US-363/372/388)."""
    ctx = request.app.state.engine_context
    engine = getattr(ctx, "engine", None)
    if engine is None:
        return JSONResponse({"error": "engine_not_initialized"}, status_code=503)

    session_id = str(uuid.uuid4())[:8]

    # US-388: force_enable overrides strategy_activation.json at runtime
    if body.force_enable:
        effective_ids = body.strategy_ids or (
            [body.strategy_id] if body.strategy_id else []
        )
        if not effective_ids:
            return JSONResponse(
                {"error": "force_enable requires at least one strategy_id"},
                status_code=422,
            )
        shadow = getattr(ctx, "shadow_mode", None)
        if shadow is not None and hasattr(shadow, "force_enable_strategies"):
            shadow.force_enable_strategies(effective_ids)

    ctx.paper_session = {
        "session_id": session_id,
        "strategy_ids": body.strategy_ids,
        "exchange_ids": body.exchange_ids,
        "exchange_id": body.exchange_id,
        "strategy_id": body.strategy_id,
        "symbols": body.symbols,
        "seed_capital": body.seed_capital,
        "duration_hours": body.duration_hours,
        "force_enable": body.force_enable,
        "status": "started",
    }

    return JSONResponse({"status": "started", "session_id": session_id, "params": body.model_dump()})


class ApproveRequest(BaseModel):
    stage: str = "live"


@router.post("/approve", dependencies=[Depends(require_auth)])
async def approve_live(body: ApproveRequest) -> JSONResponse:
    """Approve a pending Live gate request — US-364."""
    try:
        from src.infra.approval_gate import approve  # noqa: PLC0415
        result = approve(body.stage)
        if result:
            return JSONResponse({"status": "approved", "stage": body.stage})
        return JSONResponse({"status": "no_pending_request", "stage": body.stage}, status_code=404)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.post("/reject", dependencies=[Depends(require_auth)])
async def reject_live(body: ApproveRequest) -> JSONResponse:
    """Reject a pending Live gate request — US-364."""
    try:
        from src.infra.approval_gate import reject  # noqa: PLC0415
        reject(body.stage)
        return JSONResponse({"status": "rejected", "stage": body.stage})
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.get("/result", dependencies=[Depends(require_auth)])
async def get_paper_result(request: Request) -> JSONResponse:
    """Return current paper mode session status."""
    ctx = request.app.state.engine_context
    session = getattr(ctx, "paper_session", None)
    if session is None:
        return JSONResponse({"error": "no_paper_session"}, status_code=404)
    return JSONResponse(session)


@router.get("/result/{session_id}", dependencies=[Depends(require_auth)])
async def get_paper_result_by_session(session_id: str) -> JSONResponse:
    """Return saved paper session result by session_id — US-372."""
    # session_id 검증 (alphanumeric + _ - 만 허용, path traversal 방지)
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", session_id):
        return JSONResponse({"error": "invalid_session_id"}, status_code=400)
    result_file = _STATE_DIR / f"paper-results-{session_id}.json"
    # path traversal 이중 방어
    if not result_file.resolve().is_relative_to(_STATE_DIR.resolve()):
        return JSONResponse({"error": "invalid_path"}, status_code=400)
    if not result_file.exists():
        return JSONResponse({"error": "session_not_found"}, status_code=404)
    try:
        data = json.loads(result_file.read_text())
        return JSONResponse(data)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


class PaperCompleteRequest(BaseModel):
    """Result payload posted when a paper session ends — US-372/332."""
    session_id: str
    exchange_id: str = ""
    strategy_id: str = ""
    duration_hours: float = 0.0
    pnl_usd: float = 0.0
    sharpe: float = 0.0
    max_drawdown_pct: float = 0.0
    total_trades: int = 0
    profit_factor: float = 0.0
    crash_count: int = 0
    status: str = "completed"


@router.post("/complete", dependencies=[Depends(require_auth)])
async def complete_paper(body: PaperCompleteRequest) -> JSONResponse:
    """Record paper session result and update 24H cumulative tracker — US-372/332."""
    result = body.model_dump()
    result["completed_at"] = datetime.now(timezone.utc).isoformat()

    # Save per-session result file (US-372)
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    result_file = _STATE_DIR / f"paper-results-{body.session_id}.json"
    result_file.write_text(json.dumps(result, indent=2))

    # Update cumulative 24H tracker (US-332)
    cum = _load_cumulative()
    cum["total_hours"] = round(cum["total_hours"] + body.duration_hours, 4)
    cum["sessions"].append({
        "session_id": body.session_id,
        "duration_hours": body.duration_hours,
        "pnl_usd": body.pnl_usd,
        "completed_at": result["completed_at"],
    })
    cum["satisfied"] = cum["total_hours"] >= cum["target_hours"]
    _save_cumulative(cum)

    return JSONResponse({
        "status": "recorded",
        "session_id": body.session_id,
        "total_hours": cum["total_hours"],
        "satisfied": cum["satisfied"],
    })


@router.get("/cumulative", dependencies=[Depends(require_auth)])
async def get_cumulative_hours() -> JSONResponse:
    """Return 24H cumulative paper run tracker — US-332."""
    return JSONResponse(_load_cumulative())
