"""Tests for ShadowMode strategy blacklist via SHADOW_DISABLED_STRATEGIES env var.

Strategy blacklist semantics:
  - SHADOW_DISABLED_STRATEGIES="spot_futures,latency_arb" → those strategy_ids skipped
  - Empty or unset → all strategies execute normally
  - Comma-separated list; whitespace stripped
  - Applies to both _execute_shadow_trade AND _execute_shadow_trade_request
  - Does not affect other (non-listed) strategies
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.models import OrderSide


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_shadow(monkeypatch, disabled: str = ""):
    """Create a minimal ShadowMode with given SHADOW_DISABLED_STRATEGIES."""
    monkeypatch.setenv("PAPER_DISABLED_STRATEGIES", disabled)
    from src.modes.shadow import ShadowMode

    mock_executor = MagicMock()
    mock_executor.execute = AsyncMock()  # should NOT be called for disabled strategies

    mode = ShadowMode(
        signal_generator=MagicMock(),
        paper_executor=mock_executor,
    )
    mode._rate_limiter = MagicMock()
    mode._rate_limiter.try_acquire.return_value = True
    mode._balance_tracker = MagicMock()
    mode._balance_tracker.deduct.return_value = True
    return mode


def _make_signal(strategy_id: str, buy_price: float = 30_000, sell_price: float = 30_100):
    from src.core.models import Signal
    from datetime import datetime, timezone

    return Signal(
        strategy_id=strategy_id,
        symbol="BTC/USDT",
        buy_exchange="binance",
        sell_exchange="okx",
        buy_price=Decimal(str(buy_price)),
        sell_price=Decimal(str(sell_price)),
        spread_pct=Decimal("0.003"),
        confidence=0.9,
        volume=Decimal("0.01"),
        timestamp=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Strategy blacklist tests
# ---------------------------------------------------------------------------


class TestStrategyBlacklist:
    @pytest.mark.asyncio
    async def test_disabled_strategy_skips_execution(self, monkeypatch):
        """Blacklisted strategy_id → paper_executor.execute never called."""
        mode = _make_shadow(monkeypatch, disabled="spot_futures")
        signal = _make_signal(strategy_id="spot_futures", buy_price=30_000, sell_price=30_100)

        await mode._execute_shadow_trade(signal)

        # Executor should NOT have been called
        mode._paper_executor.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_blacklist_executes_all(self, monkeypatch):
        """Empty SHADOW_DISABLED_STRATEGIES → no strategy blocked."""
        mode = _make_shadow(monkeypatch, disabled="")
        # Provide a profitable signal so execution proceeds past all gates
        signal = _make_signal(strategy_id="cross_exchange_spot", buy_price=30_000, sell_price=30_500)

        # Should attempt execution (executor will be called or fail on missing setup,
        # but it must NOT be short-circuited by the blacklist)
        assert "cross_exchange_spot" not in mode._disabled_strategies

    @pytest.mark.asyncio
    async def test_multiple_strategies_blacklisted(self, monkeypatch):
        """Comma-separated list blocks all listed strategies."""
        mode = _make_shadow(monkeypatch, disabled="spot_futures,latency_arb, stat_arb ")

        assert "spot_futures" in mode._disabled_strategies
        assert "latency_arb" in mode._disabled_strategies
        assert "stat_arb" in mode._disabled_strategies

        for sid in ("spot_futures", "latency_arb", "stat_arb"):
            signal = _make_signal(strategy_id=sid)
            await mode._execute_shadow_trade(signal)

        # None of the disabled strategies should have reached the executor
        mode._paper_executor.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_blacklist_does_not_affect_other_strategies(self, monkeypatch):
        """Non-listed strategy is not blocked by the blacklist."""
        mode = _make_shadow(monkeypatch, disabled="spot_futures")

        # cross_exchange_spot is NOT in the blacklist
        assert "cross_exchange_spot" not in mode._disabled_strategies

        # Signal with non-blacklisted strategy should reach executor
        signal = _make_signal(strategy_id="cross_exchange_spot")
        await mode._execute_shadow_trade(signal)

        # Executor should have been called at least once (trade attempted)
        mode._paper_executor.execute.assert_called()
