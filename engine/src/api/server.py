"""FastAPI server factory and shared engine context."""
from __future__ import annotations

import logging
import os
from collections import deque
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Optional

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from src.api.auth import DASHBOARD_USER, require_auth, verify_password, create_token, verify_ws_token
from src.api.middleware import IPWhitelistMiddleware, LoginRateLimitMiddleware, RateLimitMiddleware
from src.api.websocket import ConnectionManager

logger = logging.getLogger(__name__)


@dataclass
class EngineContext:
    """
    Shared state injected into FastAPI app.state for all route handlers.

    Holds live references to engine subsystems and their current status.
    Updated by the main engine loop as state changes.
    """
    running: bool = False
    kill_switch_active: bool = False
    environment: str = "unknown"
    execution_mode: str = "paper"
    strategies: dict[str, Any] = field(default_factory=dict)
    positions: list[dict[str, Any]] = field(default_factory=list)
    realized_pnl: Decimal = field(default_factory=lambda: Decimal("0"))
    unrealized_pnl: Decimal = field(default_factory=lambda: Decimal("0"))
    ws_manager: Optional[ConnectionManager] = None
    trade_history: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=10_000))
    alert_history: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=5_000))
    runtime_settings: dict[str, Any] = field(default_factory=lambda: {
        "min_edge_bps": 5,
        "active_exchanges": ["binance", "binance_futures", "bybit", "okx", "bitget", "upbit", "bithumb", "coinone"],
    })
    funding_rates: dict[str, Any] = field(default_factory=dict)
    exchange_status: dict[str, Any] = field(default_factory=dict)
    shadow_mode: Any = None
    rolling_metrics: Any = None  # US-281: RollingMetricsCalculator
    # Real subsystem references (set during engine init)
    engine: Any = None
    strategy_manager: Any = None
    risk_guardian: Any = None
    position_manager: Any = None
    trade_consumer: Any = None
    # Wave 3 (US-114/115/118)
    correlation_monitor: Any = None
    slippage_feedback: Any = None
    attribution: Any = None  # US-284-b
    capital_allocator: Any = None  # US-284-a
    portfolio_risk: Any = None  # US-277/278
    dynamic_sizer: Any = None
    tca_analyzer: Any = None  # US-116
    rebalancer: Any = None  # US-120


class KillBody(BaseModel):
    reason: str = "manual"


class LoginBody(BaseModel):
    username: str
    password: str


def create_app(context: EngineContext | None = None) -> FastAPI:
    """
    Create and configure the FastAPI application.

    Args:
        context: Optional pre-built EngineContext for testing.
                 In production, pass the live context from main.py.
    """
    if context is None:
        context = EngineContext()

    ws_manager = ConnectionManager()
    context.ws_manager = ws_manager

    _is_prod = os.environ.get("ENGINE_ENV", "dev") in ("prod", "staging")
    app = FastAPI(
        title="LEVIATHAN Arbitrage Engine",
        version="1.0.0",
        description="Cross-exchange arbitrage engine REST API",
        docs_url=None if _is_prod else "/docs",
        redoc_url=None if _is_prod else "/redoc",
    )

    _cors_origins = os.environ.get(
        "CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"
    ).split(",")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(LoginRateLimitMiddleware)
    app.add_middleware(IPWhitelistMiddleware)

    # Attach shared context to app state
    app.state.engine_context = context

    # ---------------------------------------------------------------------------
    # Auth endpoints (public)
    # ---------------------------------------------------------------------------

    @app.post("/api/auth/login")
    async def login(body: LoginBody):  # type: ignore[return]
        if body.username != DASHBOARD_USER or not verify_password(body.password):
            raise HTTPException(status_code=401, detail="Invalid credentials")
        token = create_token(body.username)
        return JSONResponse({"access_token": token, "token_type": "bearer"})

    # Mount route modules
    from src.api.routes.health import router as health_router
    from src.api.routes.strategies import router as strategies_router
    from src.api.routes.trading import router as trading_router
    from src.api.routes.risk import router as risk_router
    from src.api.routes.alerts import router as alerts_router
    from src.api.routes.settings import router as settings_router
    from src.api.routes.funding import router as funding_router
    from src.api.routes.exchanges import router as exchanges_router
    from src.api.routes.attribution import router as attribution_router
    from src.api.routes.shadow import router as shadow_router
    from src.api.routes.portfolio import router as portfolio_router

    app.include_router(health_router)
    app.include_router(strategies_router)
    app.include_router(trading_router)
    app.include_router(risk_router)
    app.include_router(alerts_router)
    app.include_router(settings_router)
    app.include_router(funding_router)
    app.include_router(exchanges_router)
    app.include_router(attribution_router)
    app.include_router(shadow_router)
    app.include_router(portfolio_router)
    from src.api.routes.tca import router as tca_router
    app.include_router(tca_router)
    from src.api.routes.system import router as system_router
    app.include_router(system_router)

    # ---------------------------------------------------------------------------
    # Prometheus short-path alias
    # ---------------------------------------------------------------------------

    @app.get("/metrics")
    async def short_metrics(request: Request):  # type: ignore[return]
        """Alias so Prometheus scraper at /metrics works alongside /api/v1/metrics.

        In prod/staging, restrict to internal Docker network IPs only.
        """
        if _is_prod:
            client_ip = request.client.host if request.client else ""
            if not (client_ip.startswith("172.") or client_ip.startswith("127.") or client_ip == "::1"):
                from fastapi.responses import PlainTextResponse
                return PlainTextResponse("Forbidden", status_code=403)
        try:
            from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
            from fastapi.responses import PlainTextResponse
            data = generate_latest()
            return PlainTextResponse(
                content=data.decode() if isinstance(data, bytes) else data,
                media_type=CONTENT_TYPE_LATEST,
            )
        except Exception:
            from fastapi.responses import PlainTextResponse
            return PlainTextResponse("# metrics unavailable\n", media_type="text/plain")

    # ---------------------------------------------------------------------------
    # Short-path aliases (integration-layer convenience routes)
    # ---------------------------------------------------------------------------

    @app.get("/status", dependencies=[Depends(require_auth)])
    async def short_status():  # type: ignore[return]
        return JSONResponse({
            "running": context.running,
            "kill_switch_active": context.kill_switch_active,
            "environment": context.environment,
            "strategy_count": len(context.strategies),
            "uptime_seconds": 0,
        })

    @app.post("/kill", dependencies=[Depends(require_auth)])
    async def short_kill(body: KillBody):  # type: ignore[return]
        context.kill_switch_active = True
        context.running = False
        try:
            from src.risk.kill_switch import halt_local
            halt_local()
        except ImportError:
            pass
        return JSONResponse({"status": "halted", "reason": body.reason})

    @app.get("/strategies", dependencies=[Depends(require_auth)])
    async def short_strategies():  # type: ignore[return]
        return JSONResponse(list(context.strategies.values()))

    @app.post("/strategies/{strategy_id}/toggle", dependencies=[Depends(require_auth)])
    async def short_toggle(strategy_id: str):  # type: ignore[return]
        strategy = context.strategies.get(strategy_id)
        if strategy is None:
            raise HTTPException(status_code=404, detail=f"Strategy '{strategy_id}' not found")
        strategy["enabled"] = not strategy.get("enabled", True)
        return JSONResponse({"id": strategy_id, "enabled": strategy["enabled"]})

    # ---------------------------------------------------------------------------
    # WebSocket endpoints
    # ---------------------------------------------------------------------------

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        user = verify_ws_token(websocket)
        if user is None:
            await websocket.accept()
            await websocket.close(code=4003, reason="Authentication required")
            return
        await ws_manager.connect(websocket)
        logger.info("WebSocket /ws authenticated — user: %s", user)
        try:
            while True:
                data = await websocket.receive_text()
                await ws_manager.send_personal(websocket, {"type": "ack", "data": data})
        except WebSocketDisconnect:
            ws_manager.disconnect(websocket)
        except Exception as exc:
            logger.warning("WebSocket error: %s", exc)
            ws_manager.disconnect(websocket)

    @app.websocket("/ws/feed")
    async def websocket_feed_endpoint(websocket: WebSocket) -> None:
        user = verify_ws_token(websocket)
        if user is None:
            await websocket.accept()
            await websocket.close(code=4003, reason="Authentication required")
            return
        await ws_manager.connect(websocket)
        logger.info("WebSocket /ws/feed authenticated — user: %s", user)
        try:
            while True:
                data = await websocket.receive_text()
                await ws_manager.send_personal(websocket, {"type": "ack", "data": data})
        except WebSocketDisconnect:
            ws_manager.disconnect(websocket)
        except Exception as exc:
            logger.warning("WebSocket feed error: %s", exc)
            ws_manager.disconnect(websocket)

    @app.websocket("/ws/strategies")
    async def websocket_strategies_endpoint(websocket: WebSocket) -> None:
        user = verify_ws_token(websocket)
        if user is None:
            await websocket.accept()
            await websocket.close(code=4003, reason="Authentication required")
            return
        await ws_manager.connect(websocket)
        logger.info("WebSocket /ws/strategies authenticated — user: %s", user)
        try:
            from src.api.routes.strategies import _get_strategy_list
            await ws_manager.send_personal(websocket, {
                "type": "state_update",
                "strategies": _get_strategy_list(context),
            })
            while True:
                data = await websocket.receive_text()
                await ws_manager.send_personal(websocket, {
                    "type": "state_update",
                    "strategies": _get_strategy_list(context),
                })
        except WebSocketDisconnect:
            ws_manager.disconnect(websocket)
        except Exception as exc:
            logger.warning("WebSocket strategies error: %s", exc)
            ws_manager.disconnect(websocket)

    return app
