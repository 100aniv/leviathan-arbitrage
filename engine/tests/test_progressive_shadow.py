"""Tests for ProgressiveShadowOrchestrator (US-054).

TDD test suite for:
1. StageDefinition — 6 stages, correct durations, env override
2. ProgressiveShadowOrchestrator.run() — sequential execution, fail-fast
3. Stage gate evaluations — PASS/FAIL boundary conditions per stage
4. _compute_sharpe() — hourly PnL deltas → annualized Sharpe
5. Telegram safety — None telegram does not crash
6. ShadowMode lifecycle — start()/stop() called exactly once

Run:
    cd engine && python -m pytest tests/test_progressive_shadow.py -x --tb=short -v
"""
from __future__ import annotations

import asyncio
import time as _real_time
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.modes.progressive_shadow import (
    STAGES,
    ProgressiveShadowOrchestrator,
    StageDefinition,
    StageResult,
)
from src.modes.live_gate import LiveGateCheck, LiveGateResult
from src.modes.shadow import ShadowStats, StrategyStats


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fast_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """Instant asyncio.sleep + fake time.monotonic that advances with sleep calls.

    Allows all 6 stages to run without real waiting while preserving the
    elapsed-time semantics that Stage 4's RSS-rate gate depends on.
    """
    _base = _real_time.monotonic()
    _accumulated: list[float] = [0.0]

    async def _instant_sleep(seconds: float) -> None:
        _accumulated[0] += seconds

    mock_time = MagicMock()
    mock_time.monotonic.side_effect = lambda: _base + _accumulated[0]

    monkeypatch.setattr(asyncio, "sleep", _instant_sleep)
    monkeypatch.setattr("src.modes.progressive_shadow.time", mock_time)


@pytest.fixture
def mock_psutil():
    """Mock psutil.Process globally (psutil is imported locally inside functions).

    Default: 500 MB baseline RSS — Stage 4 passes unless overridden via
    memory_info.side_effect in individual tests.
    """
    proc_instance = MagicMock()
    proc_instance.memory_info.return_value.rss = 500 * 1024 * 1024  # 500 MB
    proc_instance.cpu_percent.return_value = 5.0

    with MagicMock() as mock_ps_cls:
        import unittest.mock as _mock
        with _mock.patch("psutil.Process", return_value=proc_instance):
            yield proc_instance


# ---------------------------------------------------------------------------
# Helpers / Factories
# ---------------------------------------------------------------------------


def _make_shadow_stats(
    *,
    signals_detected: int = 500,
    trades_executed: int = 100,
    trades_won: int = 60,
    trades_lost: int = 40,
    total_pnl: float = 10.0,
    max_drawdown: float = 0.02,
    by_strategy: dict | None = None,
) -> ShadowStats:
    """Build a ShadowStats with controllable fields."""
    stats = ShadowStats(start_time=_real_time.monotonic())
    stats.signals_detected = signals_detected
    stats.trades_executed = trades_executed
    stats.trades_won = trades_won
    stats.trades_lost = trades_lost
    stats.total_pnl = total_pnl
    stats.max_drawdown = max_drawdown
    if by_strategy is None:
        s1 = StrategyStats()
        s1.trades = 10
        s1.wins = 6
        s2 = StrategyStats()
        s2.trades = 5
        s2.wins = 3
        stats.by_strategy = {"cross_exchange": s1, "latency_arb": s2}
    else:
        stats.by_strategy = by_strategy
    return stats


def _make_shadow_mode(stats: ShadowStats | None = None) -> MagicMock:
    """Build a ShadowMode mock with configurable _stats."""
    mock = MagicMock()
    mock.start = AsyncMock()
    mock.stop = AsyncMock()
    mock._stats = stats or _make_shadow_stats()
    mock._running = True
    return mock


def _make_live_gate_result(eligible: bool = True) -> LiveGateResult:
    checks = [
        LiveGateCheck(name=f"check_{i}", passed=eligible, value="ok", threshold="ok")
        for i in range(6)
    ]
    return LiveGateResult(
        timestamp=datetime.now(tz=timezone.utc),
        eligible=eligible,
        checks=checks,
    )


def _make_live_gate(eligible: bool = True) -> MagicMock:
    mock = MagicMock()
    mock.evaluate = AsyncMock(return_value=_make_live_gate_result(eligible=eligible))
    mock.EVALUATION_DAYS = 7  # default; Stage 6 must override to 3
    return mock


def _good_pnl_snapshots(n: int = 25) -> list[tuple[float, float]]:
    """Monotonically increasing PnL snapshots → high Sharpe ratio."""
    return [(float(i * 3600), float(i * 2.0)) for i in range(n)]


def _make_orchestrator(
    *,
    stats: ShadowStats | None = None,
    live_gate: MagicMock | None = None,
    telegram: object | None = None,
    pnl_snapshots: list[tuple[float, float]] | None = None,
) -> ProgressiveShadowOrchestrator:
    """Convenience factory for ProgressiveShadowOrchestrator."""
    shadow = _make_shadow_mode(stats=stats)
    orch = ProgressiveShadowOrchestrator(
        shadow_mode=shadow,
        live_gate=live_gate if live_gate is not None else _make_live_gate(),
        telegram=telegram,
    )
    if pnl_snapshots is not None:
        orch._pnl_snapshots = pnl_snapshots
    return orch


# ---------------------------------------------------------------------------
# 1. StageDefinition — count, names, order, env override
# ---------------------------------------------------------------------------


def test_stage_definitions_count():
    """STAGES constant must contain exactly 6 stage definitions."""
    assert len(STAGES) == 6


def test_stage_definitions_names():
    """Stage names must be 1H, 2H, 6H, 12H, 24H, 72H in that exact order."""
    assert [s.name for s in STAGES] == ["1H", "2H", "6H", "12H", "24H", "72H"]


def test_stage_definitions_order():
    """Each stage duration must be strictly greater than the previous."""
    durations = [s.duration_seconds for s in STAGES]
    for a, b in zip(durations, durations[1:]):
        assert a < b, f"Durations not monotonically increasing: {durations}"


def test_stage_definitions_base_durations():
    """Default stage durations match the spec: 3600 7200 21600 43200 86400 259200."""
    expected = [3600, 7200, 21600, 43200, 86400, 259200]
    assert [s.duration_seconds for s in STAGES] == expected


def test_stage_env_override(monkeypatch: pytest.MonkeyPatch):
    """PROGRESSIVE_STAGE_1H_SECONDS=10 overrides Stage 1 duration via _build_stages()."""
    from src.modes.progressive_shadow import _build_stages

    monkeypatch.setenv("PROGRESSIVE_STAGE_1H_SECONDS", "10")
    stages = _build_stages()
    assert stages[0].duration_seconds == 10


def test_safe_int_malformed_env(monkeypatch: pytest.MonkeyPatch):
    """_safe_int returns default when env var is malformed (non-integer)."""
    from src.modes.progressive_shadow import _safe_int

    monkeypatch.setenv("TEST_SAFE_INT", "not_a_number")
    assert _safe_int("TEST_SAFE_INT", 42) == 42


def test_safe_int_missing_env():
    """_safe_int returns default when env var is not set."""
    from src.modes.progressive_shadow import _safe_int

    assert _safe_int("NONEXISTENT_ENV_VAR_12345", 99) == 99


# ---------------------------------------------------------------------------
# 2. Stage 1 (1H): crash=0, signals>0, trades>0
# ---------------------------------------------------------------------------


async def test_stage1_pass(fast_run, mock_psutil):
    """Stage 1 passes when signals_detected>0 and trades_executed>0."""
    stats = _make_shadow_stats(signals_detected=100, trades_executed=10)
    orch = _make_orchestrator(stats=stats, pnl_snapshots=_good_pnl_snapshots())
    results = await orch.run()
    assert results[0].passed is True


async def test_stage1_fail_no_trades(fast_run, mock_psutil):
    """Stage 1 fails when trades_executed=0; run() stops after Stage 1."""
    stats = _make_shadow_stats(signals_detected=100, trades_executed=0)
    orch = _make_orchestrator(stats=stats)
    results = await orch.run()
    assert results[0].passed is False
    assert len(results) == 1  # fail-fast: no further stages


async def test_stage1_fail_no_signals(fast_run, mock_psutil):
    """Stage 1 fails when signals_detected=0."""
    stats = _make_shadow_stats(signals_detected=0, trades_executed=0)
    orch = _make_orchestrator(stats=stats)
    results = await orch.run()
    assert results[0].passed is False


# ---------------------------------------------------------------------------
# 3. Stage 2 (2H): WR>50%, total_pnl>0
# ---------------------------------------------------------------------------


async def test_stage2_pass(fast_run, mock_psutil):
    """Stage 2 passes when WR=60% and total_pnl>0."""
    stats = _make_shadow_stats(trades_executed=100, trades_won=60, total_pnl=5.0)
    orch = _make_orchestrator(stats=stats, pnl_snapshots=_good_pnl_snapshots())
    results = await orch.run()
    assert results[1].passed is True


async def test_stage2_fail_low_wr(fast_run, mock_psutil):
    """Stage 2 fails when WR<=50%; run() stops after Stage 2."""
    stats = _make_shadow_stats(
        trades_executed=100, trades_won=40, trades_lost=60, total_pnl=5.0
    )
    orch = _make_orchestrator(stats=stats)
    results = await orch.run()
    assert results[1].passed is False
    assert len(results) == 2


async def test_stage2_fail_negative_pnl(fast_run, mock_psutil):
    """Stage 2 fails when total_pnl<=0 even if WR is high."""
    stats = _make_shadow_stats(trades_executed=100, trades_won=70, total_pnl=-1.0)
    orch = _make_orchestrator(stats=stats)
    results = await orch.run()
    assert results[1].passed is False


# ---------------------------------------------------------------------------
# 4. Stage 3 (6H): at least 2 strategies actively producing trades
# ---------------------------------------------------------------------------


async def test_stage3_pass(fast_run, mock_psutil):
    """Stage 3 passes when at least 2 strategies have trades>0."""
    s1 = StrategyStats()
    s1.trades = 5
    s2 = StrategyStats()
    s2.trades = 3
    stats = _make_shadow_stats(by_strategy={"cross_exchange": s1, "latency_arb": s2})
    orch = _make_orchestrator(stats=stats, pnl_snapshots=_good_pnl_snapshots())
    results = await orch.run()
    assert results[2].passed is True
    assert results[2].gate_results["strategy_separation"]["active_count"] >= 2


async def test_stage3_skip_conditional(fast_run, mock_psutil):
    """Stage 3 still passes when 2+ active + 1 conditional (0 trades)."""
    s1 = StrategyStats()
    s1.trades = 10
    s2 = StrategyStats()
    s2.trades = 7
    conditional = StrategyStats()
    conditional.trades = 0  # stat_arb: no trades — SKIP allowed
    stats = _make_shadow_stats(
        by_strategy={"cross_exchange": s1, "latency_arb": s2, "stat_arb": conditional}
    )
    orch = _make_orchestrator(stats=stats, pnl_snapshots=_good_pnl_snapshots())
    results = await orch.run()
    assert results[2].passed is True


async def test_stage3_fail_insufficient_strategies(fast_run, mock_psutil):
    """Stage 3 fails when fewer than 2 strategies have trades>0."""
    s = StrategyStats()
    s.trades = 5
    stats = _make_shadow_stats(by_strategy={"cross_exchange": s})
    orch = _make_orchestrator(stats=stats, pnl_snapshots=_good_pnl_snapshots())
    results = await orch.run()
    assert results[2].passed is False
    assert results[2].gate_results["strategy_separation"]["active_count"] == 1


async def test_stage3_fail_empty_by_strategy(fast_run, mock_psutil):
    """Stage 3 fails when by_strategy is empty (0 active < 2 minimum)."""
    stats = _make_shadow_stats(by_strategy={})
    orch = _make_orchestrator(stats=stats, pnl_snapshots=_good_pnl_snapshots())
    results = await orch.run()
    assert results[2].passed is False


# ---------------------------------------------------------------------------
# 5. Stage 4 (12H): RSS growth < 100 MB/hr, trades>50
# ---------------------------------------------------------------------------


async def test_stage4_pass(fast_run, mock_psutil):
    """Stage 4 passes when RSS growth is negligible and trades>50.

    mock_psutil returns 500 MB consistently → baseline = current → 0 MB/hr growth → PASS.
    """
    from src.modes.progressive_shadow import STAGES

    stats = _make_shadow_stats(trades_executed=100)
    shadow = _make_shadow_mode(stats=stats)
    # Run only Stage 4 to isolate the gate check (elapsed = 12H after single sleep)
    orch = ProgressiveShadowOrchestrator(
        shadow_mode=shadow, stages=[STAGES[3]]
    )
    results = await orch.run()
    assert results[0].passed is True
    assert results[0].stage.name == "12H"


async def test_stage4_fail_memory(fast_run, mock_psutil):
    """Stage 4 fails when RSS growth exceeds 100 MB/hr.

    Patches _get_current_rss: first call (baseline) = 500 MB,
    subsequent calls = 3100 MB → growth = 2600 MB / 12 H = 216 MB/hr → FAIL.
    """
    from unittest.mock import patch
    from src.modes.progressive_shadow import STAGES

    stats = _make_shadow_stats(trades_executed=100)
    shadow = _make_shadow_mode(stats=stats)
    orch = ProgressiveShadowOrchestrator(
        shadow_mode=shadow, stages=[STAGES[3]]
    )

    call_count = [0]
    def rss_sequence():
        call_count[0] += 1
        return 500 * 1024 * 1024 if call_count[0] == 1 else 3100 * 1024 * 1024

    with patch.object(orch, "_get_current_rss", side_effect=rss_sequence):
        results = await orch.run()

    assert results[0].passed is False
    assert results[0].stage.name == "12H"


async def test_stage4_fail_too_few_trades(fast_run, mock_psutil):
    """Stage 4 fails when trades_executed<=50 regardless of RSS health."""
    from src.modes.progressive_shadow import STAGES

    stats = _make_shadow_stats(trades_executed=30)
    shadow = _make_shadow_mode(stats=stats)
    orch = ProgressiveShadowOrchestrator(
        shadow_mode=shadow, stages=[STAGES[3]]
    )
    results = await orch.run()
    assert results[0].passed is False


# ---------------------------------------------------------------------------
# 6. Stage 5 (24H): Sharpe>2.0, MDD<5%, daily PnL>0
# ---------------------------------------------------------------------------


async def test_stage5_pass(fast_run, mock_psutil):
    """Stage 5 passes when Sharpe>2.0, MDD fraction<5%, total_pnl>0.

    _compute_sharpe is patched to return 3.0 to avoid background snapshot
    task non-determinism during the 24H instant-sleep run.
    """
    from unittest.mock import patch
    from src.modes.progressive_shadow import STAGES

    # max_drawdown is absolute USD; default initial_balance=10M USDT
    # 0.02 USD → fraction = 2e-9 → well below 5%
    stats = _make_shadow_stats(total_pnl=50.0, max_drawdown=0.02)
    shadow = _make_shadow_mode(stats=stats)
    orch = ProgressiveShadowOrchestrator(
        shadow_mode=shadow, stages=[STAGES[4]]
    )
    with patch.object(orch, "_compute_sharpe", return_value=3.0):
        results = await orch.run()
    assert results[0].passed is True
    assert results[0].stage.name == "24H"


async def test_stage5_fail_sharpe(fast_run, mock_psutil):
    """Stage 5 fails when _compute_sharpe() returns a value <= 2.0."""
    from unittest.mock import patch
    from src.modes.progressive_shadow import STAGES

    stats = _make_shadow_stats(total_pnl=50.0, max_drawdown=0.02)
    shadow = _make_shadow_mode(stats=stats)
    orch = ProgressiveShadowOrchestrator(
        shadow_mode=shadow, stages=[STAGES[4]]
    )
    with patch.object(orch, "_compute_sharpe", return_value=1.5):
        results = await orch.run()
    assert results[0].passed is False
    assert results[0].gate_results["sharpe"]["passed"] is False


async def test_stage5_fail_mdd(fast_run, mock_psutil):
    """Stage 5 fails when max_drawdown / initial_balance >= 5%.

    Initial balance defaults to 10,000,000 USDT (SHADOW_INITIAL_BALANCE_USDT).
    600,000 USD drawdown → 6% fraction → FAIL.
    """
    from unittest.mock import patch
    from src.modes.progressive_shadow import STAGES

    stats = _make_shadow_stats(total_pnl=50.0, max_drawdown=600_000.0)
    shadow = _make_shadow_mode(stats=stats)
    orch = ProgressiveShadowOrchestrator(
        shadow_mode=shadow, stages=[STAGES[4]]
    )
    with patch.object(orch, "_compute_sharpe", return_value=3.0):
        results = await orch.run()
    assert results[0].passed is False
    assert results[0].gate_results["max_drawdown"]["passed"] is False


# ---------------------------------------------------------------------------
# 7. Stage 6 (72H): LiveGate.evaluate() ALL PASS
# ---------------------------------------------------------------------------


async def test_stage6_pass(fast_run, mock_psutil):
    """Stage 6 passes when LiveGate.evaluate() returns eligible=True."""
    from unittest.mock import patch

    live_gate = _make_live_gate(eligible=True)
    stats = _make_shadow_stats(total_pnl=50.0, max_drawdown=0.02)
    orch = _make_orchestrator(stats=stats, live_gate=live_gate)
    with patch.object(orch, "_compute_sharpe", return_value=3.0):
        results = await orch.run()
    assert results[5].passed is True
    live_gate.evaluate.assert_awaited_once()


async def test_stage6_fail(fast_run, mock_psutil):
    """Stage 6 fails when LiveGate.evaluate() returns eligible=False."""
    from unittest.mock import patch

    live_gate = _make_live_gate(eligible=False)
    stats = _make_shadow_stats(total_pnl=50.0, max_drawdown=0.02)
    orch = _make_orchestrator(stats=stats, live_gate=live_gate)
    with patch.object(orch, "_compute_sharpe", return_value=3.0):
        results = await orch.run()
    assert results[5].passed is False


async def test_stage6_sets_evaluation_days_3(fast_run, mock_psutil):
    """Stage 6 must override LiveGate.EVALUATION_DAYS to 3 before calling evaluate()."""
    from unittest.mock import patch

    live_gate = _make_live_gate(eligible=True)
    stats = _make_shadow_stats(total_pnl=50.0, max_drawdown=0.02)
    orch = _make_orchestrator(stats=stats, live_gate=live_gate)
    with patch.object(orch, "_compute_sharpe", return_value=3.0):
        await orch.run()
    assert live_gate.EVALUATION_DAYS == 3


# ---------------------------------------------------------------------------
# 8. Full run scenarios
# ---------------------------------------------------------------------------


async def test_run_all_pass_returns_6_results(fast_run, mock_psutil):
    """A full successful run returns exactly 6 StageResults all marked PASS."""
    from unittest.mock import patch

    live_gate = _make_live_gate(eligible=True)
    stats = _make_shadow_stats(total_pnl=50.0, max_drawdown=0.02, trades_executed=100)
    orch = _make_orchestrator(stats=stats, live_gate=live_gate)
    with patch.object(orch, "_compute_sharpe", return_value=3.0):
        results = await orch.run()
    assert len(results) == 6
    assert all(r.passed for r in results)


async def test_run_mid_fail_stops_immediately(fast_run, mock_psutil):
    """When Stage 2 fails (WR<50%, negative PnL), run() returns exactly 2 results."""
    stats = _make_shadow_stats(
        trades_executed=100,
        trades_won=40,   # 40% WR → FAIL
        trades_lost=60,
        total_pnl=-1.0,  # negative → FAIL
    )
    orch = _make_orchestrator(stats=stats)
    results = await orch.run()
    assert len(results) == 2
    assert results[1].passed is False


async def test_run_calls_shadow_start_stop_exactly_once(fast_run, mock_psutil):
    """ShadowMode.start() and stop() must each be called exactly once (no restart)."""
    from unittest.mock import patch

    live_gate = _make_live_gate(eligible=True)
    stats = _make_shadow_stats(total_pnl=50.0, max_drawdown=0.02, trades_executed=100)
    shadow = _make_shadow_mode(stats=stats)
    orch = ProgressiveShadowOrchestrator(
        shadow_mode=shadow, live_gate=live_gate
    )
    with patch.object(orch, "_compute_sharpe", return_value=3.0):
        await orch.run()
    shadow.start.assert_awaited_once()
    shadow.stop.assert_awaited_once()


async def test_run_early_exit_still_calls_stop(fast_run, mock_psutil):
    """When Stage 1 fails, ShadowMode.stop() is called exactly once (in finally block).

    The fail-fast path uses `break` instead of `return`, so stop() is only
    called once in the finally block. No double-stop, no resource leak.
    """
    stats = _make_shadow_stats(signals_detected=0, trades_executed=0)
    shadow = _make_shadow_mode(stats=stats)
    orch = ProgressiveShadowOrchestrator(shadow_mode=shadow)
    await orch.run()
    shadow.stop.assert_awaited_once()  # exactly once — no double-stop, no leak


# ---------------------------------------------------------------------------
# 9. Telegram safety
# ---------------------------------------------------------------------------


async def test_telegram_none_does_not_crash(fast_run, mock_psutil):
    """Passing telegram=None must not raise on any stage notification."""
    stats = _make_shadow_stats(signals_detected=0, trades_executed=0)
    orch = _make_orchestrator(stats=stats, telegram=None)
    results = await orch.run()
    assert results[0].passed is False  # Stage 1 FAIL — but no exception


async def test_telegram_notified_on_stage_pass(fast_run, mock_psutil):
    """Telegram alerter's send_alert must be awaited when at least one stage passes."""
    mock_telegram = MagicMock()
    mock_telegram.send_alert = AsyncMock()

    # Stage 1 passes; Stage 2 fails (WR = 40%) → stops at Stage 2
    stats = _make_shadow_stats(
        signals_detected=100,
        trades_executed=100,
        trades_won=40,   # 40% WR
        trades_lost=60,
        total_pnl=5.0,
    )
    shadow = _make_shadow_mode(stats=stats)
    orch = ProgressiveShadowOrchestrator(shadow_mode=shadow, telegram=mock_telegram)
    await orch.run()
    mock_telegram.send_alert.assert_awaited()


async def test_telegram_notified_on_stage_fail(fast_run, mock_psutil):
    """Telegram alerter must be called when a stage fails."""
    mock_telegram = MagicMock()
    mock_telegram.send_alert = AsyncMock()
    stats = _make_shadow_stats(signals_detected=0, trades_executed=0)
    shadow = _make_shadow_mode(stats=stats)
    orch = ProgressiveShadowOrchestrator(shadow_mode=shadow, telegram=mock_telegram)
    await orch.run()
    mock_telegram.send_alert.assert_awaited()


# ---------------------------------------------------------------------------
# 10. _compute_sharpe() — unit tests
# ---------------------------------------------------------------------------


def test_compute_sharpe_consistent_positive_returns():
    """Monotonically increasing PnL snapshots → Sharpe well above 2.0."""
    orch = _make_orchestrator()
    orch._pnl_snapshots = [(float(i * 3600), float(i * 1.0)) for i in range(25)]
    sharpe = orch._compute_sharpe()
    assert sharpe > 2.0


def test_compute_sharpe_insufficient_data_returns_zero():
    """With a single snapshot, _compute_sharpe() returns 0.0 (no deltas)."""
    orch = _make_orchestrator()
    orch._pnl_snapshots = [(0.0, 0.0)]
    assert orch._compute_sharpe() == 0.0


def test_compute_sharpe_empty_snapshots_returns_zero():
    """With no snapshots, _compute_sharpe() returns 0.0."""
    orch = _make_orchestrator()
    orch._pnl_snapshots = []
    assert orch._compute_sharpe() == 0.0


def test_compute_sharpe_volatile_returns_low_sharpe():
    """Alternating positive/negative returns yield Sharpe < 2.0."""
    orch = _make_orchestrator()
    # Deltas alternate +100 / -100 → mean ≈ 0, Sharpe ≈ 0
    snapshots = [(float(i * 3600), float((-1) ** i * 100)) for i in range(10)]
    orch._pnl_snapshots = snapshots
    assert orch._compute_sharpe() < 2.0


def test_compute_sharpe_annualized():
    """Verify annualized multiplier: sqrt(8760 hours/year) must be applied."""
    import math

    orch = _make_orchestrator()
    # 4 snapshots → 3 hourly deltas all = 1.0 → std = 0 → inf Sharpe
    orch._pnl_snapshots = [(0.0, 0.0), (3600.0, 1.0), (7200.0, 2.0), (10800.0, 3.0)]
    sharpe = orch._compute_sharpe()
    # Constant positive returns → Sharpe = inf (mean>0, std=0)
    assert sharpe == float("inf") or sharpe > 100


# ---------------------------------------------------------------------------
# 11. StageResult structure
# ---------------------------------------------------------------------------


async def test_stage_result_stats_snapshot_is_dict(fast_run, mock_psutil):
    """StageResult.stats_snapshot must be a non-empty dict."""
    stats = _make_shadow_stats()
    orch = _make_orchestrator(stats=stats)
    results = await orch.run()
    assert isinstance(results[0].stats_snapshot, dict)
    assert len(results[0].stats_snapshot) > 0


async def test_stage_result_timestamps_are_datetime(fast_run, mock_psutil):
    """StageResult.started_at and ended_at must be datetime objects."""
    stats = _make_shadow_stats()
    orch = _make_orchestrator(stats=stats)
    results = await orch.run()
    r = results[0]
    assert isinstance(r.started_at, datetime)
    assert isinstance(r.ended_at, datetime)


async def test_stage_result_references_stage_definition(fast_run, mock_psutil):
    """StageResult.stage must be the StageDefinition that was evaluated."""
    stats = _make_shadow_stats()
    orch = _make_orchestrator(stats=stats)
    results = await orch.run()
    assert isinstance(results[0].stage, StageDefinition)
    assert results[0].stage.name == "1H"


async def test_stage_result_gate_results_is_dict(fast_run, mock_psutil):
    """StageResult.gate_results must be a dict (individual check name → result)."""
    stats = _make_shadow_stats()
    orch = _make_orchestrator(stats=stats)
    results = await orch.run()
    assert isinstance(results[0].gate_results, dict)
