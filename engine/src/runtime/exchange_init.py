"""Exchange adapter initialization — Phase 4-3 main.py 모듈화 (2026-04-26).

Extracted from main.py:442-571 (134 LOC):
- init_exchanges       (mode dispatch)
- init_paper_exchanges (PaperExchangeAdapter × N)
- init_sandbox_exchanges (native sandbox)
- init_live_exchanges  (native live, mode-aware)
- init_native_exchanges (real CEX adapter)

각 함수는 ``engine: "Engine"`` 첫 인자.
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.main import Engine

logger = logging.getLogger(__name__)


def _get_fallback_exchanges() -> list[str]:
    """Mirror main.py:_get_fallback_exchanges helper (Phase 4-3 local copy)."""
    return ["binance", "okx", "bybit", "bitget", "upbit", "bithumb", "coinone"]


async def init_exchanges(engine: "Engine") -> None:
    from src.core.config import EngineMode

    capital = engine._settings.capital.initial_capital if engine._settings else Decimal("70")

    _engine_mode = getattr(engine, "_engine_mode", None)
    if _engine_mode is None:
        from src.core.config import ExecutionMode
        _exec = getattr(engine._settings, "execution_mode", None) if engine._settings else None
        _engine_mode = (
            EngineMode.PAPER
            if _exec in (ExecutionMode.PAPER, None)
            else EngineMode.LIVE
        )
    if _engine_mode in (EngineMode.BACKTEST, EngineMode.PAPER):
        await engine._init_paper_exchanges(capital)
    elif _engine_mode == EngineMode.LIVE:
        await engine._init_live_exchanges()

    logger.info(
        "Initialized %d exchange adapters: %s",
        len(engine._exchanges), list(engine._exchanges.keys()),
    )


async def init_paper_exchanges(engine: "Engine", capital: Decimal) -> None:
    """Create one PaperExchangeAdapter per configured exchange.

    SSOT 정의 (2026-04-26 사장님 확인):
    - paper 모드: 실 WebSocket data + 거래 시뮬 (synthetic GBM 미사용)
    - backtest 모드: synthetic GBM (별도 path, PaperExchangeAdapter 미사용)

    spread_injection / synthetic loop 인자는 backtest 호환용이며 paper에서는
    호출되지 않음. paper canary 검증: subscribe_orderbook log 0건 (실 WS만 사용).
    """
    from src.execution.paper import PaperExecutor, SlippageModel
    from src.execution.paper_adapter import PaperExchangeAdapter

    exchanges = engine._active_exchanges or _get_fallback_exchanges()

    for idx, eid in enumerate(exchanges):
        executor = PaperExecutor(
            fee_rate=Decimal("0.001"),
            slippage_model=SlippageModel(base_slippage_pct=Decimal("0.0005")),
        )
        adapter = PaperExchangeAdapter(
            exchange_id=eid,
            initial_capital=capital,
            paper_executor=executor,
            # Phase 5 정의 정합 (2026-04-26): paper = 실 WS data only.
            # synthetic spread injection은 backtest 모드 전용이므로 0으로 비활성.
            spread_injection_rate=0.0,
            spread_injection_bps=0,
            tick_interval=0.5,
        )
        await adapter.connect()
        engine._exchanges[eid] = adapter
        logger.info(
            "paper adapter initialized: %s (idx=%d, real-WS-only)", eid, idx,
        )


async def init_sandbox_exchanges(engine: "Engine") -> None:
    exchanges = engine._active_exchanges or _get_fallback_exchanges()
    await engine._init_native_exchanges(exchanges, sandbox=True)


async def init_live_exchanges(engine: "Engine") -> None:
    from src.core.config import load_engine_config

    _engine_cfg = load_engine_config()
    _em = getattr(engine, "_engine_mode", None)
    _mode_cfg = _engine_cfg.get(_em.value, {}) if _em is not None else {}
    _cfg_exchanges = _mode_cfg.get("exchanges")

    exchanges = _cfg_exchanges or engine._active_exchanges or _get_fallback_exchanges()
    await engine._init_native_exchanges(exchanges, sandbox=False)


async def init_native_exchanges(engine: "Engine", exchanges: list[str], sandbox: bool) -> None:
    """Create and connect native adapters for each exchange."""
    from src.infra.exchange import create_native_adapter

    _CRED_FIELD_MAP: dict[str, tuple[str, str]] = {
        "upbit": ("upbit_access_key", "upbit_secret_key"),
        "coinone": ("coinone_access_token", "coinone_api_secret"),
    }
    ex_cfg = engine._settings.exchange if engine._settings else None
    for eid in exchanges:
        try:
            _base_eid = eid.removesuffix("_futures") if eid.endswith("_futures") else eid
            _key_field, _secret_field = _CRED_FIELD_MAP.get(
                _base_eid, (f"{_base_eid}_api_key", f"{_base_eid}_api_secret")
            )
            api_key = getattr(ex_cfg, _key_field, "") if ex_cfg else ""
            api_secret = getattr(ex_cfg, _secret_field, "") if ex_cfg else ""
            passphrase = getattr(ex_cfg, f"{_base_eid}_passphrase", "") if ex_cfg else ""
            if not api_key and eid.endswith("_futures") and ex_cfg:
                _base = eid.removesuffix("_futures")
                _k, _s = _CRED_FIELD_MAP.get(_base, (f"{_base}_api_key", f"{_base}_api_secret"))
                api_key = getattr(ex_cfg, _k, "")
                api_secret = getattr(ex_cfg, _s, "")
                passphrase = getattr(ex_cfg, f"{_base}_passphrase", "")
            adapter = create_native_adapter(
                exchange_id=eid,
                api_key=api_key,
                api_secret=api_secret,
                passphrase=passphrase,
                sandbox=sandbox,
            )
            await adapter.connect()
            engine._exchanges[eid] = adapter
            logger.info("Native adapter connected: %s (sandbox=%s)", eid, sandbox)
        except ValueError as exc:
            logger.warning("Native adapter not available for %s: %s", eid, exc)
        except Exception as exc:
            logger.warning("Native adapter connect failed for %s: %s", eid, exc)
