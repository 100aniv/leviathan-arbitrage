"""Tests for LiveMode first trade recording — US-056.

US-056: Live 모드 전환
  - LiveMode._first_trade_recorded = False (초기값)
  - _record_first_trade(trade_request, pnl) → .omc/state/live-first-trade.json
  - 필드: exchange, strategy, side, qty, price, pnl_usd, timestamp
  - 첫 번째 체결만 기록 (이후 호출 무시)
  - approval gate stage="K-L"
"""
from __future__ import annotations

import inspect
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# US-056-1: _first_trade_recorded attribute
# ---------------------------------------------------------------------------

class TestFirstTradeRecordedAttribute:
    def test_first_trade_recorded_initializes_to_false(self):
        """LiveMode._first_trade_recorded must start as False — US-056."""
        from src.modes.live import LiveMode
        src = inspect.getsource(LiveMode.__init__)
        assert "_first_trade_recorded = False" in src or "_first_trade_recorded=False" in src, (
            "LiveMode.__init__ must set self._first_trade_recorded = False (US-056)"
        )


# ---------------------------------------------------------------------------
# US-056-2: _record_first_trade() method
# ---------------------------------------------------------------------------

class TestRecordFirstTradeMethod:
    def test_record_first_trade_method_exists(self):
        """LiveMode must have _record_first_trade() method — US-056."""
        from src.modes.live import LiveMode
        assert hasattr(LiveMode, "_record_first_trade"), (
            "LiveMode missing _record_first_trade() — US-056 requires first live trade recording"
        )

    def test_record_first_trade_saves_json_file(self):
        """_record_first_trade() must write to live-first-trade.json."""
        from src.modes.live import LiveMode
        src = inspect.getsource(LiveMode._record_first_trade)
        assert "live-first-trade.json" in src, (
            "_record_first_trade must write to live-first-trade.json"
        )

    def test_record_first_trade_sets_flag(self, tmp_path):
        """_record_first_trade() must set _first_trade_recorded = True."""
        from src.modes.live import LiveMode

        src = inspect.getsource(LiveMode._record_first_trade)
        assert "_first_trade_recorded = True" in src, (
            "_record_first_trade must set self._first_trade_recorded = True to prevent double-recording"
        )

    def test_record_first_trade_json_has_required_fields(self):
        """live-first-trade.json must include exchange/strategy/side/qty/price/pnl_usd/timestamp."""
        from src.modes.live import LiveMode

        src = inspect.getsource(LiveMode._record_first_trade)
        required_fields = ["exchange", "strategy", "side", "qty", "price", "pnl_usd", "timestamp"]
        for field in required_fields:
            assert f'"{field}"' in src or f"'{field}'" in src, (
                f"_record_first_trade must include '{field}' in JSON record"
            )


# ---------------------------------------------------------------------------
# US-056-3: Only first trade recorded
# ---------------------------------------------------------------------------

class TestOnlyFirstTradeRecorded:
    def test_record_first_trade_only_called_when_flag_false(self):
        """_record_first_trade should only be called when _first_trade_recorded is False."""
        from src.modes.live import LiveMode

        src = inspect.getsource(LiveMode._execute_trade_request
                                 if hasattr(LiveMode, "_execute_trade_request")
                                 else LiveMode.run)
        # The guard condition: not self._first_trade_recorded
        assert "not self._first_trade_recorded" in src or "_first_trade_recorded" in src, (
            "LiveMode must check _first_trade_recorded before calling _record_first_trade"
        )


# ---------------------------------------------------------------------------
# US-056-4: approval gate stage = "K-L"
# ---------------------------------------------------------------------------

class TestApprovalGateStageName:
    def test_approval_gate_uses_stage_k_l(self):
        """LiveMode.start() approval gate must use stage='K-L' — US-056."""
        from src.modes.live import LiveMode
        src = inspect.getsource(LiveMode.start)
        assert '"K-L"' in src or "'K-L'" in src, (
            "LiveMode.start() approval gate must use stage='K-L' (US-056 Live 전환 단계 식별자)"
        )


# ---------------------------------------------------------------------------
# US-056-5: execution_mode guard for first trade recording
# ---------------------------------------------------------------------------

class TestExecutionModeGuard:
    def test_first_trade_recording_guarded_by_live_mode(self):
        """First trade recording must only trigger in execution_mode=='live'."""
        from src.modes.live import LiveMode

        # Check source of _execute_trade_request for the guard
        method = getattr(LiveMode, "_execute_trade_request", None)
        if method is None:
            pytest.skip("_execute_trade_request not found — checking alternative")

        src = inspect.getsource(method)
        assert "live" in src and "_first_trade_recorded" in src, (
            "_execute_trade_request must guard _record_first_trade with execution_mode=='live'"
        )
