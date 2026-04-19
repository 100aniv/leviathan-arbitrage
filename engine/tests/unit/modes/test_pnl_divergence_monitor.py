"""WS-D1 unit tests — PnL divergence monitor loop.

Directly exercises the divergence math + 3-consecutive-breaches gate + HALT
without spinning up a full LiveMode. We construct a LiveMode shell via
``__new__`` and invoke the loop body inline by monkeypatching asyncio.sleep.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.modes.live import LiveMode, LiveModeStats


@pytest.fixture(autouse=True)
def _reset_halt() -> None:
    """Ensure halt flag is clean before each test (module-level global)."""
    from src.risk.kill_switch import clear_halt
    clear_halt()
    yield
    clear_halt()


def _make_live(total_pnl: float = 0.0, exchange_pnl: float = 0.0) -> LiveMode:
    """Construct a minimal LiveMode shell for loop testing."""
    live = LiveMode.__new__(LiveMode)
    live._stats = LiveModeStats(total_pnl=total_pnl)
    live._running = False
    live._pnl_divergence_breach_count = 0
    live._execution_mode = "live"
    live._telegram = AsyncMock()
    live._telegram.send_alert = AsyncMock(return_value=True)

    # Patch EXCHANGE_INCOME_TOTAL to return a deterministic exchange_pnl sum.
    import src.infra.metrics as metrics
    fake_sample = MagicMock()
    fake_sample.name = "leviathan_exchange_income_total_usdt_total"
    fake_sample.value = exchange_pnl
    fake_metric = MagicMock()
    fake_metric.samples = [fake_sample]
    metrics.EXCHANGE_INCOME_TOTAL.collect = MagicMock(return_value=[fake_metric])

    return live


async def _run_loop_once(live: LiveMode, iterations: int) -> None:
    """Run the monitor loop for N iterations then stop."""
    # Inject a kill switch via monkeypatch on asyncio.sleep to stop after N iters.
    call_count = {"n": 0}
    real_sleep = asyncio.sleep

    async def counting_sleep(_delay: float) -> None:
        call_count["n"] += 1
        if call_count["n"] >= iterations:
            live._running = False
        await real_sleep(0)

    live._running = True
    import src.modes.live as live_mod
    orig_sleep = live_mod.asyncio.sleep
    live_mod.asyncio.sleep = counting_sleep  # type: ignore
    try:
        await live._pnl_divergence_monitor_loop()
    finally:
        live_mod.asyncio.sleep = orig_sleep  # type: ignore


class TestPnlDivergenceMonitor:
    @pytest.mark.asyncio
    async def test_no_breach_when_within_threshold(self) -> None:
        """engine=$10, exchange=$9.8 → divergence 2% < 5% → no breach."""
        live = _make_live(total_pnl=10.0, exchange_pnl=9.8)
        await _run_loop_once(live, iterations=2)
        assert live._pnl_divergence_breach_count == 0
        from src.risk.kill_switch import is_halted
        assert not is_halted()

    @pytest.mark.asyncio
    async def test_single_breach_does_not_halt(self) -> None:
        """One breach → counter increments but no HALT."""
        live = _make_live(total_pnl=10.0, exchange_pnl=0.0)  # 100% divergence
        await _run_loop_once(live, iterations=1)
        assert live._pnl_divergence_breach_count == 1
        from src.risk.kill_switch import is_halted
        assert not is_halted()

    @pytest.mark.asyncio
    async def test_three_consecutive_breaches_triggers_halt(self) -> None:
        """3 consecutive breaches → HALT + Telegram + counter stays >=3."""
        live = _make_live(total_pnl=10.0, exchange_pnl=0.0)
        await _run_loop_once(live, iterations=3)
        from src.risk.kill_switch import is_halted
        assert is_halted()
        assert live._telegram.send_alert.called

    @pytest.mark.asyncio
    async def test_recovery_resets_counter(self) -> None:
        """Breach then recovery on next poll → counter back to 0."""
        # Two separate mock metrics: first poll diverges, second is clean
        live = _make_live(total_pnl=10.0, exchange_pnl=0.0)

        call_count = {"n": 0}
        import src.infra.metrics as metrics

        def _side_effect() -> list:
            call_count["n"] += 1
            fake_sample = MagicMock()
            fake_sample.name = "leviathan_exchange_income_total_usdt_total"
            # First call: divergent; second call: clean
            fake_sample.value = 0.0 if call_count["n"] == 1 else 10.0
            fake_metric = MagicMock()
            fake_metric.samples = [fake_sample]
            return [fake_metric]

        metrics.EXCHANGE_INCOME_TOTAL.collect = MagicMock(side_effect=_side_effect)
        await _run_loop_once(live, iterations=2)
        assert live._pnl_divergence_breach_count == 0


if __name__ == "__main__":
    pytest.main([__file__, "-x", "--tb=short", "--no-cov"])
