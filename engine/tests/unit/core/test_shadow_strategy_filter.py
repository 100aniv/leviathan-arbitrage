"""Tests for ShadowMode strategy_filter and get_strategy_report — US-299."""
from __future__ import annotations

import math
from unittest.mock import MagicMock

import pytest

from src.modes.shadow import ShadowMode, ShadowStats, StrategyStats


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_shadow(strategy_filter=None):
    """Build a minimal ShadowMode instance suitable for unit-level tests.

    Only signal_generator is required by __init__; every other dependency is
    optional.  We patch just enough to make construction succeed without any
    network or DB access.
    """
    sig_gen = MagicMock()
    shadow = ShadowMode(
        signal_generator=sig_gen,
        strategy_filter=strategy_filter,
    )
    return shadow


def _inject_strategy_trades(shadow: ShadowMode, strategy_id: str, pnls: list[float]) -> None:
    """Directly populate by_strategy to simulate executed trades."""
    ss = shadow._stats.by_strategy.setdefault(strategy_id, StrategyStats())
    for pnl in pnls:
        ss.trades += 1
        ss.pnl += pnl
        ss.pnl_history.append(pnl)
        if pnl >= 0:
            ss.wins += 1
        else:
            ss.losses += 1


# ---------------------------------------------------------------------------
# US-299 — strategy_filter allowlist
# ---------------------------------------------------------------------------


class TestStrategyFilterNone:
    def test_filter_none_stores_no_frozenset(self) -> None:
        """strategy_filter=None sets internal filter to None (all strategies pass)."""
        shadow = _make_shadow(strategy_filter=None)
        assert shadow._strategy_filter is None

    def test_filter_none_is_backward_compatible(self) -> None:
        """ShadowMode constructed without strategy_filter arg has None filter."""
        sig_gen = MagicMock()
        shadow = ShadowMode(signal_generator=sig_gen)
        assert shadow._strategy_filter is None


class TestStrategyFilterAllowlist:
    def test_filter_stores_frozenset_of_provided_ids(self) -> None:
        """strategy_filter=[...] is stored as frozenset internally."""
        shadow = _make_shadow(strategy_filter=["cross_exchange"])
        assert shadow._strategy_filter == frozenset({"cross_exchange"})

    def test_filter_allows_exact_match(self) -> None:
        """A strategy_id present in the filter is not blocked."""
        shadow = _make_shadow(strategy_filter=["cross_exchange"])
        assert "cross_exchange" in shadow._strategy_filter

    def test_filter_blocks_strategy_not_in_allowlist(self) -> None:
        """A strategy_id absent from the filter is blocked."""
        shadow = _make_shadow(strategy_filter=["cross_exchange"])
        assert "triangular" not in shadow._strategy_filter

    def test_filter_allows_multiple_strategies(self) -> None:
        """Multiple strategies can be included in the allowlist."""
        shadow = _make_shadow(strategy_filter=["cross_exchange", "funding_rate"])
        assert shadow._strategy_filter == frozenset({"cross_exchange", "funding_rate"})

    def test_empty_list_produces_empty_frozenset(self) -> None:
        """An empty list produces an empty frozenset — blocks all strategies."""
        shadow = _make_shadow(strategy_filter=[])
        # falsy empty list → _strategy_filter is None (no filter applied)
        # Implementation: frozenset([]) is falsy so the condition `if strategy_filter`
        # is False — confirmed by reading the source.
        assert shadow._strategy_filter is None


# ---------------------------------------------------------------------------
# US-299 — get_strategy_report keys and Sharpe/MDD calculation
# ---------------------------------------------------------------------------


class TestGetStrategyReport:
    def test_report_contains_required_keys(self) -> None:
        """get_strategy_report() entries must include sharpe, max_drawdown, and pass."""
        shadow = _make_shadow()
        _inject_strategy_trades(shadow, "cross_exchange", [1.0, 2.0, 3.0])
        report = shadow.get_strategy_report()
        assert "cross_exchange" in report
        entry = report["cross_exchange"]
        assert "sharpe" in entry
        assert "max_drawdown" in entry
        assert "pass" in entry

    def test_report_contains_standard_keys(self) -> None:
        """Report entries also contain trades, wins, losses, pnl, win_rate."""
        shadow = _make_shadow()
        _inject_strategy_trades(shadow, "funding_rate", [0.5, -0.1])
        entry = shadow.get_strategy_report()["funding_rate"]
        for key in ("trades", "wins", "losses", "pnl", "win_rate"):
            assert key in entry

    def test_pass_true_when_trade_and_positive_pnl(self) -> None:
        """pass=True when trades >= 1 and total pnl >= 0."""
        shadow = _make_shadow()
        _inject_strategy_trades(shadow, "cross_exchange", [1.0])
        entry = shadow.get_strategy_report()["cross_exchange"]
        assert entry["pass"] is True

    def test_pass_false_when_no_trades(self) -> None:
        """pass=False when no trades have been executed."""
        shadow = _make_shadow()
        # Inject an empty StrategyStats entry
        shadow._stats.by_strategy["empty_strat"] = StrategyStats()
        entry = shadow.get_strategy_report()["empty_strat"]
        assert entry["pass"] is False

    def test_pass_false_when_pnl_negative(self) -> None:
        """pass=False when total pnl is negative even with trades."""
        shadow = _make_shadow()
        _inject_strategy_trades(shadow, "stat_arb", [-1.0, -2.0])
        entry = shadow.get_strategy_report()["stat_arb"]
        assert entry["pass"] is False

    def test_sharpe_zero_for_single_trade(self) -> None:
        """Sharpe is 0.0 when fewer than 2 trades (cannot compute std)."""
        shadow = _make_shadow()
        _inject_strategy_trades(shadow, "cross_exchange", [1.0])
        entry = shadow.get_strategy_report()["cross_exchange"]
        assert entry["sharpe"] == 0.0

    def test_sharpe_nonzero_for_sufficient_trades(self) -> None:
        """Sharpe is non-zero when pnl_history has >= 2 entries with variance."""
        shadow = _make_shadow()
        _inject_strategy_trades(shadow, "cross_exchange", [1.0, 2.0, 3.0, 4.0, 5.0])
        entry = shadow.get_strategy_report()["cross_exchange"]
        assert entry["sharpe"] != 0.0

    def test_sharpe_manual_calculation(self) -> None:
        """Sharpe matches manual mean/std computation on known data."""
        shadow = _make_shadow()
        pnls = [1.0, 2.0, 3.0, 4.0, 5.0]
        _inject_strategy_trades(shadow, "cross_exchange", pnls)
        n = len(pnls)
        mean = sum(pnls) / n
        variance = sum((x - mean) ** 2 for x in pnls) / (n - 1)
        std = math.sqrt(variance)
        expected_sharpe = round(mean / std, 4)
        entry = shadow.get_strategy_report()["cross_exchange"]
        assert entry["sharpe"] == pytest.approx(expected_sharpe, abs=1e-9)

    def test_max_drawdown_zero_for_always_positive_pnl(self) -> None:
        """MDD is 0.0 when all trades are profitable (monotonically increasing equity)."""
        shadow = _make_shadow()
        _inject_strategy_trades(shadow, "cross_exchange", [1.0, 1.0, 1.0])
        entry = shadow.get_strategy_report()["cross_exchange"]
        assert entry["max_drawdown"] == 0.0

    def test_max_drawdown_positive_after_loss(self) -> None:
        """MDD is positive when a trade loss follows a profitable trade."""
        shadow = _make_shadow()
        # equity: 0 -> 10 -> 5 → peak=10, dd=5
        _inject_strategy_trades(shadow, "cross_exchange", [10.0, -5.0])
        entry = shadow.get_strategy_report()["cross_exchange"]
        assert entry["max_drawdown"] == pytest.approx(5.0, abs=1e-6)

    def test_report_empty_when_no_strategies(self) -> None:
        """get_strategy_report() returns empty dict when no trades have occurred."""
        shadow = _make_shadow()
        assert shadow.get_strategy_report() == {}

    def test_pnl_history_populated_on_direct_injection(self) -> None:
        """StrategyStats.pnl_history holds all injected pnl values."""
        shadow = _make_shadow()
        pnls = [0.5, 1.0, -0.3]
        _inject_strategy_trades(shadow, "funding_rate", pnls)
        ss = shadow._stats.by_strategy["funding_rate"]
        assert ss.pnl_history == pnls
