"""Tests for US-246: LiveGate enforce — blocks ineligible, passes eligible, sends Telegram.

Verifies:
- is_live_eligible() returns False when no evaluation run yet
- is_live_eligible() returns True when latest_result.eligible is True
- _send_telegram_notification() called on block (via evaluate mock)

Run:
    cd engine && python -m pytest tests/test_live_gate_enforce.py -v --tb=short
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.modes.live_gate import LiveGate, LiveGateCheck, LiveGateResult


def _make_gate(telegram=None, kill_switch=None, circuit_breaker=None) -> LiveGate:
    """Build a LiveGate with mocked pool (no real DB)."""
    pool = MagicMock()
    return LiveGate(
        pool=pool,
        telegram=telegram,
        kill_switch=kill_switch,
        circuit_breaker=circuit_breaker,
    )


def _passing_result() -> LiveGateResult:
    return LiveGateResult(
        timestamp=datetime.now(timezone.utc),
        eligible=True,
        checks=[
            LiveGateCheck(name="Sharpe Ratio", passed=True, value="3.0", threshold=">= 2.5"),
        ],
        block_reasons=[],
    )


def _failing_result() -> LiveGateResult:
    return LiveGateResult(
        timestamp=datetime.now(timezone.utc),
        eligible=False,
        checks=[
            LiveGateCheck(name="Sharpe Ratio", passed=False, value="1.0", threshold=">= 2.5"),
        ],
        block_reasons=["Sharpe 1.00 < 2.5"],
    )


class TestLiveGateEnforce:
    """US-246: LiveGate 통과/차단 검증."""

    def test_enforce_blocks_when_ineligible(self):
        """평가 미실행 → False 반환 (초기 상태는 미통과)."""
        gate = _make_gate()

        result = gate.is_live_eligible()

        assert result is False

    def test_enforce_passes_when_eligible(self):
        """LiveGate 통과 결과 주입 → True 반환."""
        gate = _make_gate()
        gate._latest_result = _passing_result()

        result = gate.is_live_eligible()

        assert result is True

    def test_enforce_returns_false_for_failing_result(self):
        """차단 결과 주입 → False 반환."""
        gate = _make_gate()
        gate._latest_result = _failing_result()

        result = gate.is_live_eligible()

        assert result is False

    @pytest.mark.asyncio
    async def test_enforce_sends_telegram_on_block(self):
        """차단 시 Telegram 알림 send_alert() 호출."""
        mock_telegram = AsyncMock()
        mock_telegram.send_alert = AsyncMock()

        gate = _make_gate(telegram=mock_telegram)

        # _send_telegram_notification 직접 호출로 알림 발송 검증
        result = _failing_result()
        await gate._send_telegram_notification(result, strategy_id="test_strategy")

        mock_telegram.send_alert.assert_called_once()
        call_args = mock_telegram.send_alert.call_args
        # WARNING level로 차단 알림 전송
        assert call_args.kwargs.get("level") == "WARNING" or "WARNING" in str(call_args)

    @pytest.mark.asyncio
    async def test_no_telegram_when_none(self):
        """Telegram=None이면 알림 없음 (에러 없음)."""
        gate = _make_gate(telegram=None)
        result = _failing_result()

        # Should not raise
        await gate._send_telegram_notification(result, strategy_id="test_strategy")
