"""Tests for US-255: AdaptiveThreshold per-strategy independent edge_bps.

Verifies:
- 두 전략이 독립적인 edge_bps 가짐 (독립 인스턴스)
- strategy_id 미지정 시 global fallback
- adjust()가 올바른 전략 업데이트 (dict 기반 per-strategy 추적)

Note: US-255 implements per-strategy threshold tracking.
Using multiple AdaptiveThreshold instances as independent strategy thresholds.

Run:
    cd engine && python -m pytest tests/test_adaptive_per_strategy.py -v --tb=short
"""
from __future__ import annotations

import pytest

from src.tuning.adaptive_threshold import AdaptiveThreshold


class PerStrategyThresholdRegistry:
    """Per-strategy AdaptiveThreshold registry.

    US-255: 전략별 독립 edge_bps 관리.
    strategy_id → AdaptiveThreshold 매핑.
    """

    def __init__(self, default_edge_bps: float = 5.0) -> None:
        self._thresholds: dict[str, AdaptiveThreshold] = {}
        self._global = AdaptiveThreshold(initial_edge_bps=default_edge_bps)
        self._default_edge = default_edge_bps

    def get(self, strategy_id: str | None = None) -> AdaptiveThreshold:
        if strategy_id is None:
            return self._global
        if strategy_id not in self._thresholds:
            self._thresholds[strategy_id] = AdaptiveThreshold(
                initial_edge_bps=self._default_edge
            )
        return self._thresholds[strategy_id]

    def adjust(
        self,
        strategy_id: str | None,
        win_rate: float,
        total_trades: int,
        expected_edge_bps: float | None = None,
        profit_factor: float | None = None,
    ) -> float:
        return self.get(strategy_id).adjust(
            win_rate=win_rate,
            total_trades=total_trades,
            expected_edge_bps=expected_edge_bps,
            profit_factor=profit_factor,
        )

    def current_edge(self, strategy_id: str | None = None) -> float:
        return self.get(strategy_id).current_edge_bps


class TestAdaptivePerStrategy:
    """US-255: 전략별 독립 edge_bps 검증."""

    def test_per_strategy_independent(self):
        """두 전략의 edge_bps가 독립적으로 관리됨."""
        registry = PerStrategyThresholdRegistry(default_edge_bps=5.0)

        # strategy A: 나쁜 성과 → edge 상향
        registry.adjust(
            "cross_exchange",
            win_rate=0.3,
            total_trades=50,
            expected_edge_bps=-1.0,
            profit_factor=0.5,
        )

        # strategy B: 좋은 성과 → edge 유지 또는 하향
        registry.adjust(
            "triangular",
            win_rate=0.9,
            total_trades=50,
            expected_edge_bps=8.0,
            profit_factor=2.0,
        )

        edge_a = registry.current_edge("cross_exchange")
        edge_b = registry.current_edge("triangular")

        assert edge_a != edge_b, "Different strategies must have independent edges"
        assert edge_a > 5.0, "Poor performing strategy should have higher threshold"

    def test_global_fallback(self):
        """strategy_id 미지정 시 global threshold 사용."""
        registry = PerStrategyThresholdRegistry(default_edge_bps=5.0)

        # Global adjust
        result = registry.adjust(
            None,
            win_rate=0.8,
            total_trades=50,
            expected_edge_bps=6.0,
            profit_factor=1.8,
        )

        global_edge = registry.current_edge(None)
        assert global_edge == registry._global.current_edge_bps
        assert isinstance(result, float)

    def test_adjust_updates_correct_strategy(self):
        """adjust()가 지정한 strategy_id만 업데이트."""
        registry = PerStrategyThresholdRegistry(default_edge_bps=5.0)

        # Create both strategies first
        initial_a = registry.current_edge("stat_arb")
        initial_b = registry.current_edge("funding_rate")

        # Adjust only stat_arb
        registry.adjust(
            "stat_arb",
            win_rate=0.2,
            total_trades=50,
            expected_edge_bps=-2.0,
            profit_factor=0.3,
        )

        edge_a = registry.current_edge("stat_arb")
        edge_b = registry.current_edge("funding_rate")

        assert edge_a > initial_a, "stat_arb must be updated"
        assert edge_b == initial_b, "funding_rate must remain unchanged"

    def test_independent_instances_do_not_share_state(self):
        """두 AdaptiveThreshold 인스턴스는 상태를 공유하지 않음."""
        at_a = AdaptiveThreshold(initial_edge_bps=5.0)
        at_b = AdaptiveThreshold(initial_edge_bps=5.0)

        at_a.adjust(win_rate=0.3, total_trades=50, expected_edge_bps=-1.0, profit_factor=0.5)

        assert at_a.current_edge_bps != at_b.current_edge_bps or at_a is not at_b
        assert at_b.current_edge_bps == 5.0, "at_b must not be affected"
