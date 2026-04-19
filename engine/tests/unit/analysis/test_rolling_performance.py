"""WS-D3 unit tests — 30-day rolling Sharpe + MDD."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import math
import pytest

from src.analysis.tca import RollingPerformance


def _day(n: int) -> datetime:
    """Return a UTC midnight n days after a fixed epoch."""
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return base + timedelta(days=n)


class TestRollingPerformance:
    def test_empty_returns_zero_sharpe_and_mdd(self) -> None:
        rp = RollingPerformance()
        assert rp.sharpe_30d() == 0.0
        assert rp.mdd_30d_pct() == 0.0

    def test_single_day_not_enough_for_sharpe(self) -> None:
        """Sharpe requires >= 2 committed days (std is degenerate with n<2)."""
        rp = RollingPerformance(initial_equity=100.0)
        rp.record_pnl(5.0, now_utc=_day(0))
        rp.roll_day(now_utc=_day(1))
        assert rp.sharpe_30d() == 0.0
        assert rp.mdd_30d_pct() == 0.0

    def test_two_days_positive_returns_positive_sharpe(self) -> None:
        """Two positive days → positive Sharpe, no drawdown."""
        rp = RollingPerformance(initial_equity=100.0)
        rp.record_pnl(1.0, now_utc=_day(0))
        rp.roll_day(now_utc=_day(1))
        rp.record_pnl(2.0, now_utc=_day(1))
        rp.roll_day(now_utc=_day(2))
        sharpe = rp.sharpe_30d()
        assert sharpe > 0
        assert rp.mdd_30d_pct() == 0.0

    def test_drawdown_after_peak(self) -> None:
        """Peak then loss → MDD > 0."""
        rp = RollingPerformance(initial_equity=100.0)
        # Day 0: +10 → equity 110 (peak)
        rp.record_pnl(10.0, now_utc=_day(0))
        rp.roll_day(now_utc=_day(1))
        # Day 1: -22 → equity 88 (drawdown 20%)
        rp.record_pnl(-22.0, now_utc=_day(1))
        rp.roll_day(now_utc=_day(2))
        mdd = rp.mdd_30d_pct()
        # DD = (110 - 88) / 110 = 20%
        assert mdd == pytest.approx(20.0, rel=1e-3)

    def test_window_caps_at_30_days(self) -> None:
        """More than 30 days → history stays bounded to window_days."""
        rp = RollingPerformance(initial_equity=100.0, window_days=30)
        for i in range(40):
            rp.record_pnl(1.0, now_utc=_day(i))
            rp.roll_day(now_utc=_day(i + 1))
        assert len(rp.history) == 30

    def test_auto_roll_on_day_change(self) -> None:
        """record_pnl across a day boundary auto-commits the prior day."""
        rp = RollingPerformance(initial_equity=100.0)
        rp.record_pnl(5.0, now_utc=_day(0))
        rp.record_pnl(3.0, now_utc=_day(1))  # triggers auto-commit of day 0
        rp.roll_day(now_utc=_day(2))
        assert len(rp.history) == 2
        assert rp.history[0].pnl_usd == 5.0
        assert rp.history[1].pnl_usd == 3.0


if __name__ == "__main__":
    pytest.main([__file__, "-x", "--tb=short", "--no-cov"])
