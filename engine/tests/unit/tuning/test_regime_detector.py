"""Tests for RegimeDetector (TDD - US-047).

Behavioral contracts:
  - detect([very small returns]) → LOW    (std < 0.5%)
  - detect([medium returns])    → MEDIUM (0.5% <= std < 3%)
  - detect([high returns])      → HIGH   (3% <= std < 8%)
  - detect([extreme returns])   → CRISIS (std >= 8%)
  - detect([])                  → 현재 체제 유지 (current_regime unchanged)
  - 체제 변경 시 history 기록, 동일 체제면 미기록
  - should_kill_switch() → True only on CRISIS
  - save_history → asyncpg conn.executemany 호출
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from src.tuning.regime_detector import MarketRegime, RegimeDetector


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------


class TestRegimeDetectorInit:
    def test_default_thresholds_and_regime_exist(self):
        """RegimeDetector initializes with thresholds dict, MEDIUM regime, empty history."""
        rd = RegimeDetector()
        assert rd.current_regime is not None
        assert "low" in rd.thresholds
        assert "high" in rd.thresholds
        assert "crisis" in rd.thresholds
        assert rd.history == []


# ---------------------------------------------------------------------------
# detect — regime classification
# ---------------------------------------------------------------------------

# Return series whose std clearly falls in each bucket (default thresholds):
#   LOW   : std < 0.005  → use ±0.0001 → std ≈ 0.0001
#   MEDIUM: 0.005..0.03  → use ±0.010  → std ≈ 0.010
#   HIGH  : 0.03..0.08   → use ±0.050  → std ≈ 0.050
#   CRISIS: ≥ 0.08       → use ±0.150  → std ≈ 0.150

_LOW_RETURNS = [0.0001, -0.0001, 0.00005, -0.00005] * 10
_MEDIUM_RETURNS = [0.010, -0.010, 0.008, -0.008] * 10
_HIGH_RETURNS = [0.050, -0.050, 0.040, -0.040] * 10
_CRISIS_RETURNS = [0.150, -0.150, 0.120, -0.120] * 10


class TestRegimeDetectorDetect:
    def test_low_volatility_returns_low_regime(self):
        """detect returns MarketRegime.LOW when return std is below low threshold."""
        rd = RegimeDetector()
        assert rd.detect(_LOW_RETURNS) == MarketRegime.LOW

    def test_medium_volatility_returns_medium_regime(self):
        """detect returns MarketRegime.MEDIUM when return std is in mid range."""
        rd = RegimeDetector()
        assert rd.detect(_MEDIUM_RETURNS) == MarketRegime.MEDIUM

    def test_high_volatility_returns_high_regime(self):
        """detect returns MarketRegime.HIGH when return std exceeds high threshold."""
        rd = RegimeDetector()
        assert rd.detect(_HIGH_RETURNS) == MarketRegime.HIGH

    def test_crisis_volatility_returns_crisis_regime(self):
        """detect returns MarketRegime.CRISIS when return std exceeds crisis threshold."""
        rd = RegimeDetector()
        assert rd.detect(_CRISIS_RETURNS) == MarketRegime.CRISIS

    def test_empty_returns_keeps_current_regime(self):
        """detect returns unchanged current_regime when returns list is empty."""
        rd = RegimeDetector()
        rd.current_regime = MarketRegime.HIGH
        result = rd.detect([])
        assert result == MarketRegime.HIGH

    def test_records_history_entry_when_regime_changes(self):
        """detect appends to history when current_regime transitions to a new value."""
        rd = RegimeDetector()
        rd.current_regime = MarketRegime.LOW
        rd.history.clear()

        rd.detect(_CRISIS_RETURNS)  # LOW → CRISIS: should record change
        assert len(rd.history) == 1

    def test_no_history_entry_when_regime_unchanged(self):
        """detect does not append to history when regime stays the same."""
        rd = RegimeDetector()
        rd.current_regime = MarketRegime.MEDIUM
        rd.history.clear()

        rd.detect(_MEDIUM_RETURNS)  # stays MEDIUM
        assert len(rd.history) == 0


# ---------------------------------------------------------------------------
# should_kill_switch
# ---------------------------------------------------------------------------


class TestRegimeDetectorKillSwitch:
    def test_returns_true_when_current_regime_is_crisis(self):
        """should_kill_switch returns True when current_regime is CRISIS."""
        rd = RegimeDetector()
        rd.current_regime = MarketRegime.CRISIS
        assert rd.should_kill_switch() is True

    def test_returns_false_for_low_regime(self):
        """should_kill_switch returns False when current_regime is LOW."""
        rd = RegimeDetector()
        rd.current_regime = MarketRegime.LOW
        assert rd.should_kill_switch() is False

    def test_returns_false_for_medium_regime(self):
        """should_kill_switch returns False when current_regime is MEDIUM."""
        rd = RegimeDetector()
        rd.current_regime = MarketRegime.MEDIUM
        assert rd.should_kill_switch() is False

    def test_returns_false_for_high_regime(self):
        """should_kill_switch returns False when current_regime is HIGH."""
        rd = RegimeDetector()
        rd.current_regime = MarketRegime.HIGH
        assert rd.should_kill_switch() is False


# ---------------------------------------------------------------------------
# save_history
# ---------------------------------------------------------------------------


class TestRegimeDetectorSaveHistory:
    @pytest.mark.asyncio
    async def test_save_history_calls_conn_executemany_with_history(self):
        """save_history persists history entries via asyncpg conn.executemany."""
        rd = RegimeDetector()
        rd.current_regime = MarketRegime.LOW
        rd.detect(_CRISIS_RETURNS)  # LOW → CRISIS: creates one history entry

        mock_conn = MagicMock()
        mock_conn.executemany = AsyncMock()

        await rd.save_history(mock_conn)

        mock_conn.executemany.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_save_history_clears_history_after_success(self):
        """save_history clears self.history after successful write to prevent re-send."""
        rd = RegimeDetector()
        rd.current_regime = MarketRegime.LOW
        rd.detect(_CRISIS_RETURNS)
        assert len(rd.history) == 1

        mock_conn = MagicMock()
        mock_conn.executemany = AsyncMock()

        await rd.save_history(mock_conn)
        assert len(rd.history) == 0
