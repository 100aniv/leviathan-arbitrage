"""ModeRunner ABC + 3 구현체 — Phase 5.4 (2026-04-26).

원본: engine/src/runtime/mode_loops.py 826 LOC (paper/live/backtest if-elif).

산업 표준 (Nautilus environment context / LEAN IBrokerage 다형성 / Hummingbot Strategy):
- BacktestRunner / PaperRunner / LiveRunner 모두 ModeRunner ABC 상속
- Engine.run() → mode = create_mode_runner(mode, deps); await mode.start()
- if engine._engine_mode == X 분기 0개

설계:
- ABC: start() / stop() / on_signal(signal) / on_fill(result)
- 각 Runner는 자체 LifecycleManager 보유
- mode-specific 의존성 (executor, data_feed, risk_gate)을 constructor inject
- backward-compat: 기존 mode_loops 함수 호출 수단도 유지 (Phase 5.4 도입 단계)
"""
from __future__ import annotations

import abc
import logging
from typing import Any

logger = logging.getLogger(__name__)


class ModeRunner(abc.ABC):
    """Mode dispatch 추상화. 3 구현체: BacktestRunner / PaperRunner / LiveRunner.

    Engine은 mode 모름 — `create_mode_runner(mode, deps)` factory로만 instantiate.
    """

    name: str = "abstract"

    @abc.abstractmethod
    async def start(self) -> None:
        """Mode lifecycle 시작 (LifecycleManager.start_all 호출)."""

    @abc.abstractmethod
    async def stop(self) -> None:
        """graceful shutdown (LifecycleManager.stop_all 호출)."""


class PaperRunner(ModeRunner):
    """Paper mode runner — real WS data + simulated execution.

    SSOT (2026-04-26): paper = shadow + paper 통합 = 실 WS data + 거래만 안 함.
    synthetic GBM은 backtest 전용 (BacktestRunner).
    """

    name = "paper"

    def __init__(self, engine: Any) -> None:
        # Phase 5.4 도입 단계: 기존 mode_loops.paper_mode_loop를 위임 호출.
        # Phase 5.5+ specific port DI 마이그레이션 시 deps unpacking.
        self._engine = engine

    async def start(self) -> None:
        from src.runtime.mode_loops import paper_mode_loop
        logger.info("PaperRunner.starting")
        await paper_mode_loop(self._engine)

    async def stop(self) -> None:
        # Engine.stop()에서 _shutdown_event 설정 → paper_mode_loop 자체 graceful exit
        logger.info("PaperRunner.stopping")


class LiveRunner(ModeRunner):
    """Live mode runner — real WS data + AtomicExecutor real trading."""

    name = "live"

    def __init__(self, engine: Any) -> None:
        self._engine = engine

    async def start(self) -> None:
        from src.runtime.mode_loops import live_mode_loop
        logger.info("LiveRunner.starting")
        await live_mode_loop(self._engine)

    async def stop(self) -> None:
        logger.info("LiveRunner.stopping")


class BacktestRunner(ModeRunner):
    """Backtest mode runner — synthetic GBM data + simulated execution."""

    name = "backtest"

    def __init__(self, engine: Any) -> None:
        self._engine = engine

    async def start(self) -> None:
        from src.runtime.mode_loops import backtest_mode_task
        logger.info("BacktestRunner.starting")
        await backtest_mode_task(self._engine)

    async def stop(self) -> None:
        logger.info("BacktestRunner.stopping")


def create_mode_runner(mode: str, engine: Any) -> ModeRunner:
    """Factory: mode 이름 → ModeRunner 인스턴스.

    mode ∈ {paper, live, backtest}. EngineMode enum value 또는 string 모두 허용.
    """
    mode_normalized = str(mode).lower().strip()
    if mode_normalized in ("paper", "shadow"):  # shadow legacy alias
        return PaperRunner(engine)
    if mode_normalized == "live":
        return LiveRunner(engine)
    if mode_normalized == "backtest":
        return BacktestRunner(engine)
    raise ValueError(f"Unknown mode: {mode!r}. Expected: paper/live/backtest")
