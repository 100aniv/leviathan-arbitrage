"""Runtime settings routes."""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from src.api.auth import require_auth

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1")


class SettingsUpdate(BaseModel):
    min_edge_bps: int | None = None
    active_exchanges: list[str] | None = None
    capital_per_exchange_usd: float | None = None
    max_position_usd: float | None = None
    max_daily_loss_usd: float | None = None


class ModeUpdate(BaseModel):
    mode: str  # "shadow", "paper", "live"


@router.get("/settings", dependencies=[Depends(require_auth)])
async def get_settings(request: Request) -> JSONResponse:
    """Return current runtime settings and strategy info."""
    ctx = request.app.state.engine_context
    active_strategies = [
        {"id": sid, "type": s.get("type", sid), "enabled": s.get("enabled", True)}
        for sid, s in ctx.strategies.items()
    ]
    # US-F04: Load strategy-exchange requirements from trading.json
    from src.core.config import load_trading_config
    try:
        tcfg = load_trading_config()
        strategy_reqs = tcfg.get("strategy_exchange_requirements", {})
    except Exception:
        strategy_reqs = {}

    return JSONResponse({
        "min_edge_bps": ctx.runtime_settings.get("min_edge_bps", 5),
        "active_strategies": active_strategies,
        "active_exchanges": ctx.runtime_settings.get("active_exchanges", []),
        "execution_mode": getattr(ctx, "execution_mode", "shadow"),
        "capital_per_exchange_usd": ctx.runtime_settings.get("capital_per_exchange_usd", 70),
        "max_position_usd": ctx.runtime_settings.get("max_position_usd", 5000),
        "max_daily_loss_usd": ctx.runtime_settings.get("max_daily_loss_usd", 500),
        "strategy_exchange_requirements": strategy_reqs,
    })


@router.put("/settings", dependencies=[Depends(require_auth)])
async def update_settings(request: Request, body: SettingsUpdate) -> JSONResponse:
    """Update runtime settings (min_edge_bps, active_exchanges)."""
    ctx = request.app.state.engine_context
    if body.min_edge_bps is not None:
        ctx.runtime_settings["min_edge_bps"] = body.min_edge_bps
    if body.active_exchanges is not None:
        ctx.runtime_settings["active_exchanges"] = body.active_exchanges
    if body.capital_per_exchange_usd is not None:
        ctx.runtime_settings["capital_per_exchange_usd"] = body.capital_per_exchange_usd
    if body.max_position_usd is not None:
        ctx.runtime_settings["max_position_usd"] = body.max_position_usd
    if body.max_daily_loss_usd is not None:
        ctx.runtime_settings["max_daily_loss_usd"] = body.max_daily_loss_usd
    return JSONResponse({
        "min_edge_bps": ctx.runtime_settings.get("min_edge_bps", 5),
        "active_exchanges": ctx.runtime_settings.get("active_exchanges", []),
        "execution_mode": getattr(ctx, "execution_mode", "shadow"),
        "capital_per_exchange_usd": ctx.runtime_settings.get("capital_per_exchange_usd", 70),
        "max_position_usd": ctx.runtime_settings.get("max_position_usd", 5000),
        "max_daily_loss_usd": ctx.runtime_settings.get("max_daily_loss_usd", 500),
    })


def _update_env_file(key: str, value: str) -> bool:
    """Update a key=value in engine/.env file. Returns True on success."""
    env_path = Path(__file__).parents[3] / ".env"
    if not env_path.exists():
        logger.warning("Engine .env not found at %s", env_path)
        return False
    try:
        content = env_path.read_text()
        pattern = re.compile(rf"^{re.escape(key)}=.*$", re.MULTILINE)
        if pattern.search(content):
            content = pattern.sub(f"{key}={value}", content)
        else:
            content = content.rstrip("\n") + f"\n{key}={value}\n"
        env_path.write_text(content)
        return True
    except Exception as exc:
        logger.warning("Failed to update .env %s: %s", key, exc)
        return False


@router.patch("/settings/mode", dependencies=[Depends(require_auth)])
async def update_mode(request: Request, body: ModeUpdate) -> JSONResponse:
    """Switch execution mode. Live mode requires LiveGate check."""
    ctx = request.app.state.engine_context
    valid_modes = {"backtest", "paper", "shadow", "live"}
    if body.mode not in valid_modes:
        raise HTTPException(status_code=400, detail=f"Invalid mode. Must be one of: {valid_modes}")

    # US-F02: Check open positions before allowing mode switch
    if ctx.position_manager is not None:
        try:
            open_positions = list(ctx.position_manager.get_all_positions())
            if open_positions:
                return JSONResponse(status_code=403, content={
                    "error": "포지션 청산 후 전환하세요",
                    "open_positions": len(open_positions),
                    "current_mode": getattr(ctx, "execution_mode", "shadow"),
                })
        except Exception as exc:
            # Codex CRITICAL: fail-closed — 포지션 조회 실패 시 전환 차단
            logger.warning("Position check failed (blocking mode switch): %s", exc)
            return JSONResponse(status_code=503, content={
                "error": "포지션 조회 실패 — 안전을 위해 모드 전환 차단",
                "current_mode": getattr(ctx, "execution_mode", "shadow"),
            })

    livegate_result = None
    if body.mode == "live":
        # Check LiveGate before allowing live mode
        engine = getattr(ctx, "engine", None)
        if engine and hasattr(engine, "_live_gate"):
            try:
                result = await engine._live_gate.evaluate()
                livegate_result = {
                    "passed": result.passed if hasattr(result, 'passed') else False,
                    "checks": {}
                }
                if not livegate_result["passed"]:
                    return JSONResponse(status_code=403, content={
                        "error": "LiveGate check failed",
                        "livegate": livegate_result,
                        "current_mode": ctx.execution_mode,
                    })
            except Exception as exc:
                logger.warning("LiveGate evaluation failed: %s", exc)
                livegate_result = {"passed": False, "error": str(exc)}
                return JSONResponse(status_code=403, content={
                    "error": "LiveGate evaluation error",
                    "livegate": livegate_result,
                    "current_mode": ctx.execution_mode,
                })
        else:
            return JSONResponse(status_code=403, content={
                "error": "LiveGate not available",
                "current_mode": ctx.execution_mode,
            })

    prev_mode = getattr(ctx, "execution_mode", "shadow")
    ctx.execution_mode = body.mode

    # US-F02: Persist EXECUTION_MODE to .env
    env_updated = _update_env_file("EXECUTION_MODE", body.mode)

    # US-F02: Send Telegram notification
    restart_required = body.mode == "live" or prev_mode == "live"
    engine = getattr(ctx, "engine", None)
    telegram = getattr(engine, "_telegram", None) if engine else None
    if telegram is not None:
        try:
            msg = (
                f"⚙️ 모드 전환: {prev_mode.upper()} → {body.mode.upper()}\n"
                f"{'🔴 엔진 재시작 필요' if restart_required else '✅ 런타임 전환 완료'}"
            )
            await telegram.send_alert(msg, level="INFO")
        except Exception as exc:
            logger.warning("Mode change telegram notification failed: %s", exc)

    return JSONResponse({
        "mode": ctx.execution_mode,
        "livegate": livegate_result,
        "env_updated": env_updated,
        "restart_required": restart_required,
        "message": "엔진 재시작 필요" if restart_required else "런타임 전환 완료",
    })


@router.post("/settings/test-alert", dependencies=[Depends(require_auth)])
async def send_test_alert(request: Request) -> JSONResponse:
    """US-211: Send a test alert to Telegram to verify connectivity."""
    ctx = request.app.state.engine_context

    # Append a test alert to alert_history
    test_alert = {
        "id": f"test-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
        "type": "test",
        "severity": "info",
        "message": "테스트 알림입니다 / Test alert",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    ctx.alert_history.append(test_alert)

    # Try to send via Telegram if available
    telegram_sent = False
    engine = getattr(ctx, "engine", None)
    telegram = getattr(engine, "_telegram", None) if engine else None
    if telegram is not None:
        try:
            telegram_sent = await telegram.send_alert("🔔 테스트 알림 / Test alert", level="INFO")
        except Exception as exc:
            logger.warning("Test alert Telegram send failed: %s", exc)

    return JSONResponse({
        "status": "sent",
        "telegram_delivered": telegram_sent,
        "alert": test_alert,
    })
