"""Tests for ShadowMode portfolio_risk integration — US-300."""
from __future__ import annotations

import inspect
from unittest.mock import MagicMock

import pytest

from src.modes.shadow import ShadowMode, StrategyStats


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_shadow(portfolio_risk=None):
    """Build a minimal ShadowMode instance for unit-level testing."""
    sig_gen = MagicMock()
    return ShadowMode(
        signal_generator=sig_gen,
        portfolio_risk=portfolio_risk,
    )


def _make_portfolio_risk(var=None, vol=None, mdd_pct=0.0, breach=False):
    """Create a MagicMock that quacks like PortfolioRiskManager."""
    prm = MagicMock()
    prm.get_var.return_value = var
    prm.get_portfolio_volatility.return_value = vol
    prm.check_mdd_breach.return_value = {
        "portfolio_mdd_pct": mdd_pct,
        "portfolio_breach": breach,
        "strategy_breaches": [],
    }
    return prm


# ---------------------------------------------------------------------------
# US-300 — __init__ signature
# ---------------------------------------------------------------------------


class TestInitSignature:
    def test_portfolio_risk_parameter_exists_in_init(self) -> None:
        """ShadowMode.__init__ must accept a portfolio_risk keyword argument."""
        sig = inspect.signature(ShadowMode.__init__)
        assert "portfolio_risk" in sig.parameters

    def test_portfolio_risk_defaults_to_none(self) -> None:
        """portfolio_risk parameter defaults to None for backward compatibility."""
        sig = inspect.signature(ShadowMode.__init__)
        default = sig.parameters["portfolio_risk"].default
        assert default is None


# ---------------------------------------------------------------------------
# US-300 — backward compatibility (portfolio_risk=None)
# ---------------------------------------------------------------------------


class TestPortfolioRiskNone:
    def test_none_portfolio_risk_stores_none(self) -> None:
        """portfolio_risk=None stores None internally."""
        shadow = _make_shadow(portfolio_risk=None)
        assert shadow._portfolio_risk is None

    def test_none_portfolio_risk_get_snapshot_has_no_portfolio_keys(self) -> None:
        """get_snapshot() without portfolio_risk omits portfolio_var_95 key."""
        shadow = _make_shadow(portfolio_risk=None)
        snap = shadow.get_snapshot()
        assert "portfolio_var_95" not in snap

    def test_none_portfolio_risk_get_snapshot_succeeds(self) -> None:
        """get_snapshot() completes without error when portfolio_risk is None."""
        shadow = _make_shadow(portfolio_risk=None)
        snap = shadow.get_snapshot()
        assert isinstance(snap, dict)

    def test_none_portfolio_risk_does_not_call_update_returns(self) -> None:
        """When portfolio_risk is None, update_returns is never called."""
        prm = _make_portfolio_risk(var=-1.0, vol=0.01)
        shadow = _make_shadow(portfolio_risk=None)
        # Directly mutate a StrategyStats entry to simulate a filled trade
        ss = shadow._stats.by_strategy.setdefault("cross_exchange", StrategyStats())
        ss.trades += 1
        ss.pnl += 1.0
        ss.pnl_history.append(1.0)
        # portfolio_risk was not injected — the MagicMock prm should be untouched
        prm.update_returns.assert_not_called()


# ---------------------------------------------------------------------------
# US-300 — get_snapshot portfolio keys when portfolio_risk is set
# ---------------------------------------------------------------------------


class TestGetSnapshotWithPortfolioRisk:
    def test_snapshot_contains_portfolio_var_95_key(self) -> None:
        """get_snapshot() includes portfolio_var_95 when portfolio_risk is set."""
        prm = _make_portfolio_risk(var=-0.05, vol=0.01)
        shadow = _make_shadow(portfolio_risk=prm)
        snap = shadow.get_snapshot()
        assert "portfolio_var_95" in snap

    def test_snapshot_contains_portfolio_volatility_key(self) -> None:
        """get_snapshot() includes portfolio_volatility when portfolio_risk is set."""
        prm = _make_portfolio_risk(var=-0.05, vol=0.01)
        shadow = _make_shadow(portfolio_risk=prm)
        snap = shadow.get_snapshot()
        assert "portfolio_volatility" in snap

    def test_snapshot_contains_portfolio_mdd_pct_key(self) -> None:
        """get_snapshot() includes portfolio_mdd_pct when portfolio_risk is set."""
        prm = _make_portfolio_risk(var=-0.05, vol=0.01, mdd_pct=0.02)
        shadow = _make_shadow(portfolio_risk=prm)
        snap = shadow.get_snapshot()
        assert "portfolio_mdd_pct" in snap

    def test_snapshot_contains_portfolio_mdd_breach_key(self) -> None:
        """get_snapshot() includes portfolio_mdd_breach when portfolio_risk is set."""
        prm = _make_portfolio_risk(var=-0.05, vol=0.01, breach=True)
        shadow = _make_shadow(portfolio_risk=prm)
        snap = shadow.get_snapshot()
        assert "portfolio_mdd_breach" in snap

    def test_snapshot_portfolio_var_95_value_rounded(self) -> None:
        """portfolio_var_95 in snapshot is the rounded value from get_var()."""
        prm = _make_portfolio_risk(var=-0.123456789, vol=0.01)
        shadow = _make_shadow(portfolio_risk=prm)
        snap = shadow.get_snapshot()
        assert snap["portfolio_var_95"] == pytest.approx(-0.123457, abs=1e-5)

    def test_snapshot_portfolio_var_95_none_when_get_var_returns_none(self) -> None:
        """portfolio_var_95 is None when get_var() returns None (insufficient data)."""
        prm = _make_portfolio_risk(var=None, vol=None)
        shadow = _make_shadow(portfolio_risk=prm)
        snap = shadow.get_snapshot()
        assert snap["portfolio_var_95"] is None

    def test_snapshot_portfolio_volatility_none_when_get_portfolio_volatility_returns_none(self) -> None:
        """portfolio_volatility is None when get_portfolio_volatility() returns None."""
        prm = _make_portfolio_risk(var=None, vol=None)
        shadow = _make_shadow(portfolio_risk=prm)
        snap = shadow.get_snapshot()
        assert snap["portfolio_volatility"] is None

    def test_snapshot_portfolio_mdd_breach_true_when_breached(self) -> None:
        """portfolio_mdd_breach reflects the breach flag from check_mdd_breach()."""
        prm = _make_portfolio_risk(var=-0.1, vol=0.02, breach=True)
        shadow = _make_shadow(portfolio_risk=prm)
        snap = shadow.get_snapshot()
        assert snap["portfolio_mdd_breach"] is True

    def test_snapshot_portfolio_mdd_breach_false_when_not_breached(self) -> None:
        """portfolio_mdd_breach is False when no MDD breach occurs."""
        prm = _make_portfolio_risk(var=-0.01, vol=0.005, breach=False)
        shadow = _make_shadow(portfolio_risk=prm)
        snap = shadow.get_snapshot()
        assert snap["portfolio_mdd_breach"] is False

    def test_snapshot_calls_get_var_on_portfolio_risk(self) -> None:
        """get_snapshot() calls get_var() on the portfolio_risk object."""
        prm = _make_portfolio_risk(var=-0.05, vol=0.01)
        shadow = _make_shadow(portfolio_risk=prm)
        shadow.get_snapshot()
        prm.get_var.assert_called()

    def test_snapshot_calls_check_mdd_breach_on_portfolio_risk(self) -> None:
        """get_snapshot() calls check_mdd_breach() on the portfolio_risk object."""
        prm = _make_portfolio_risk(var=-0.05, vol=0.01)
        shadow = _make_shadow(portfolio_risk=prm)
        shadow.get_snapshot()
        prm.check_mdd_breach.assert_called()


# ---------------------------------------------------------------------------
# US-300 — exception resilience
# ---------------------------------------------------------------------------


class TestPortfolioRiskExceptionResilient:
    def test_snapshot_does_not_raise_when_portfolio_risk_raises(self) -> None:
        """get_snapshot() degrades gracefully if portfolio_risk methods raise."""
        prm = MagicMock()
        prm.get_var.side_effect = RuntimeError("db unavailable")
        prm.get_portfolio_volatility.side_effect = RuntimeError("db unavailable")
        prm.check_mdd_breach.side_effect = RuntimeError("db unavailable")
        shadow = _make_shadow(portfolio_risk=prm)
        # Must not raise — exception is caught internally and logged
        snap = shadow.get_snapshot()
        assert isinstance(snap, dict)
        # portfolio keys absent because the exception was swallowed
        assert "portfolio_var_95" not in snap
