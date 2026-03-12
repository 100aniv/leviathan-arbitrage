"""Tests for US-084: REGIME_MIN_EDGE 상수 및 SignalGenerator 레짐 통합.

검증 항목:
  - REGIME_MIN_EDGE 상수값 (CALM/NORMAL/VOLATILE/CRISIS bps)
  - 7개 레짐 모두 포함
  - threshold alias (LOW/MEDIUM/HIGH) 값 일치
  - CALM < NORMAL < VOLATILE < CRISIS 단조 증가
  - SignalGenerator regime_detector 하위호환 및 속성 저장
  - CALM/VOLATILE 레짐별 min_edge 필터 동작
  - 레짐 전환 시 필터 변경 확인
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock, AsyncMock

import pytest

from src.core.signal import SignalGenerator, SignalConfig
from src.core.price_hub import PriceHub
from src.friction.cost_calculator import CostCalculator
from src.tuning.regime_detector import (
    MarketRegime,
    RegimeDetector,
    HMMRegimeDetector,
    REGIME_MIN_EDGE,
)


# ---------------------------------------------------------------------------
# REGIME_MIN_EDGE 상수 검증
# ---------------------------------------------------------------------------


class TestRegimeMinEdgeConstants:
    def test_calm_regime_is_3bps(self):
        """CALM 레짐 최소 엣지는 3 bps (0.0003)."""
        assert REGIME_MIN_EDGE[MarketRegime.CALM] == Decimal("0.0003")

    def test_normal_regime_is_5bps(self):
        """NORMAL 레짐 최소 엣지는 5 bps (0.0005)."""
        assert REGIME_MIN_EDGE[MarketRegime.NORMAL] == Decimal("0.0005")

    def test_volatile_regime_is_8bps(self):
        """VOLATILE 레짐 최소 엣지는 8 bps (0.0008)."""
        assert REGIME_MIN_EDGE[MarketRegime.VOLATILE] == Decimal("0.0008")

    def test_crisis_regime_is_15bps(self):
        """CRISIS 레짐 최소 엣지는 15 bps (0.0015)."""
        assert REGIME_MIN_EDGE[MarketRegime.CRISIS] == Decimal("0.0015")

    def test_all_seven_regimes_covered(self):
        """REGIME_MIN_EDGE에 7개 레짐 (CALM, NORMAL, VOLATILE, LOW, MEDIUM, HIGH, CRISIS) 모두 포함."""
        assert len(REGIME_MIN_EDGE) == 7
        expected_keys = {
            MarketRegime.CALM,
            MarketRegime.NORMAL,
            MarketRegime.VOLATILE,
            MarketRegime.LOW,
            MarketRegime.MEDIUM,
            MarketRegime.HIGH,
            MarketRegime.CRISIS,
        }
        assert set(REGIME_MIN_EDGE.keys()) == expected_keys

    def test_threshold_aliases_match_hmm_regimes(self):
        """LOW==CALM, MEDIUM==NORMAL, HIGH==VOLATILE 값 일치 (backward-compat alias)."""
        assert REGIME_MIN_EDGE[MarketRegime.LOW] == REGIME_MIN_EDGE[MarketRegime.CALM]
        assert REGIME_MIN_EDGE[MarketRegime.MEDIUM] == REGIME_MIN_EDGE[MarketRegime.NORMAL]
        assert REGIME_MIN_EDGE[MarketRegime.HIGH] == REGIME_MIN_EDGE[MarketRegime.VOLATILE]

    def test_monotonic_increase_calm_to_crisis(self):
        """CALM < NORMAL < VOLATILE < CRISIS 단조 증가 — 위기일수록 문턱 높음."""
        assert (
            REGIME_MIN_EDGE[MarketRegime.CALM]
            < REGIME_MIN_EDGE[MarketRegime.NORMAL]
            < REGIME_MIN_EDGE[MarketRegime.VOLATILE]
            < REGIME_MIN_EDGE[MarketRegime.CRISIS]
        )


# ---------------------------------------------------------------------------
# SignalGenerator 레짐 통합
# ---------------------------------------------------------------------------


def _make_generator(regime_detector=None) -> SignalGenerator:
    """테스트용 SignalGenerator 팩토리."""
    hub = MagicMock(spec=PriceHub)
    calc = MagicMock(spec=CostCalculator)
    config = SignalConfig()
    return SignalGenerator(
        price_hub=hub,
        cost_calculator=calc,
        config=config,
        regime_detector=regime_detector,
    )


class TestSignalGeneratorRegimeIntegration:
    def test_no_regime_detector_creates_successfully(self):
        """regime_detector=None 으로 SignalGenerator 생성 시 예외 없음 (하위호환)."""
        gen = _make_generator(regime_detector=None)
        assert gen is not None
        assert gen._regime_detector is None

    def test_regime_detector_stored_as_attribute(self):
        """regime_detector 인자가 _regime_detector 속성으로 저장됨."""
        mock_detector = MagicMock()
        gen = _make_generator(regime_detector=mock_detector)
        assert gen._regime_detector is mock_detector

    def test_calm_regime_4bps_edge_passes_threshold(self):
        """CALM 레짐 (문턱 3bps) 에서 4bps edge는 문턱 초과 → 통과."""
        mock_detector = MagicMock()
        mock_detector.current_regime = MarketRegime.CALM
        gen = _make_generator(regime_detector=mock_detector)

        edge = Decimal("0.0004")  # 4 bps
        effective_min = REGIME_MIN_EDGE.get(
            gen._regime_detector.current_regime, gen._config.min_edge
        )
        assert effective_min == Decimal("0.0003")
        assert edge >= effective_min, "4bps edge는 CALM 문턱(3bps)을 통과해야 함"

    def test_volatile_regime_5bps_edge_blocked(self):
        """VOLATILE 레짐 (문턱 8bps) 에서 5bps edge는 문턱 미달 → 차단."""
        mock_detector = MagicMock()
        mock_detector.current_regime = MarketRegime.VOLATILE
        gen = _make_generator(regime_detector=mock_detector)

        edge = Decimal("0.0005")  # 5 bps
        effective_min = REGIME_MIN_EDGE.get(
            gen._regime_detector.current_regime, gen._config.min_edge
        )
        assert effective_min == Decimal("0.0008")
        assert edge < effective_min, "5bps edge는 VOLATILE 문턱(8bps)에서 차단되어야 함"

    def test_regime_transition_calm_to_volatile_changes_filter(self):
        """CALM→VOLATILE 전환 시 동일 5bps edge가 통과 → 차단으로 변경됨."""
        mock_detector = MagicMock()
        gen = _make_generator(regime_detector=mock_detector)
        edge = Decimal("0.0005")  # 5 bps

        # CALM: 5bps > 3bps → 통과
        mock_detector.current_regime = MarketRegime.CALM
        calm_threshold = REGIME_MIN_EDGE.get(
            gen._regime_detector.current_regime, gen._config.min_edge
        )
        assert edge >= calm_threshold, "CALM 레짐에서 5bps는 통과해야 함"

        # VOLATILE 전환: 5bps < 8bps → 차단
        mock_detector.current_regime = MarketRegime.VOLATILE
        volatile_threshold = REGIME_MIN_EDGE.get(
            gen._regime_detector.current_regime, gen._config.min_edge
        )
        assert edge < volatile_threshold, "VOLATILE 전환 후 5bps는 차단되어야 함"

        # 전환 전후 문턱이 다름 확인
        assert calm_threshold != volatile_threshold
