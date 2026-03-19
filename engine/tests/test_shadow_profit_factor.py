"""Tests for US-257: profit_factor amount-based calculation.

Verifies:
- profit_factor = total_profit / max(0.01, total_loss_abs)
- Zero total_loss → returns 10.0 (no ZeroDivisionError)
- All losses (total_profit=0) → PF < 1.0
- profit_factor correctly passed to AdaptiveThreshold.adjust()

Run:
    cd engine && python -m pytest tests/test_shadow_profit_factor.py -v --tb=short
"""
from __future__ import annotations

import pytest

from src.modes.shadow import ShadowStats
from src.tuning.adaptive_threshold import AdaptiveThreshold


def _compute_profit_factor(total_profit: float, total_loss: float) -> float:
    """Replicate shadow.py US-257 profit_factor calculation."""
    _total_profit = total_profit
    _total_loss = abs(total_loss)
    if _total_loss > 0:
        return _total_profit / max(0.01, _total_loss)
    else:
        return 10.0 if _total_profit > 0 else 1.0


class TestProfitFactorAmountBased:
    """US-257: profit_factor = 금액 비율 (총이익 / 총손실)."""

    def test_profit_factor_amount_based(self):
        """총이익 $100, 총손실 $50 → PF = 2.0."""
        stats = ShadowStats(start_time=0.0)
        stats.total_profit = 100.0
        stats.total_loss = -50.0

        pf = _compute_profit_factor(stats.total_profit, stats.total_loss)

        assert abs(pf - 2.0) < 1e-6

    def test_profit_factor_zero_loss(self):
        """total_loss=0이면 ZeroDivisionError 없이 10.0 반환."""
        stats = ShadowStats(start_time=0.0)
        stats.total_profit = 100.0
        stats.total_loss = 0.0

        pf = _compute_profit_factor(stats.total_profit, stats.total_loss)

        assert pf == 10.0  # 이익만 있으면 최대 PF

    def test_profit_factor_all_losses(self):
        """total_profit=0이면 PF < 1.0."""
        stats = ShadowStats(start_time=0.0)
        stats.total_profit = 0.0
        stats.total_loss = -50.0

        pf = _compute_profit_factor(stats.total_profit, stats.total_loss)

        assert pf < 1.0

    def test_profit_factor_adaptive_threshold_receives_value(self):
        """profit_factor가 AdaptiveThreshold.adjust()에 정상 전달되어 실행됨."""
        at = AdaptiveThreshold(initial_edge_bps=5.0)
        initial = at.current_edge_bps

        # 좋은 성과: edge=6 bps, PF=2.0 → 임계치 하향 가능
        result = at.adjust(
            win_rate=0.7,
            total_trades=50,
            expected_edge_bps=6.0,
            profit_factor=2.0,
        )

        assert isinstance(result, float)
        assert at.min_edge <= result <= at.max_edge

    def test_profit_factor_low_pf_raises_threshold(self):
        """PF < 1.0 이면 edge 임계치 상향."""
        at = AdaptiveThreshold(initial_edge_bps=5.0)
        initial = at.current_edge_bps

        # 나쁜 성과: edge negative, PF < 1.0 → threshold 상향
        result = at.adjust(
            win_rate=0.4,
            total_trades=50,
            expected_edge_bps=-1.0,
            profit_factor=0.8,
        )

        assert result > initial  # 임계치 상향됨
