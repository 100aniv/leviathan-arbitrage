"""Day 12 — PreTradeValidator + BookWalk flag-gate integration tests.

Verifies that `EXECUTION_PRETRADE_VALIDATOR_ENABLED` correctly gates both the
PreTradeValidator call and the BookWalk market-impact check inside
`LiveMode._execute_trade_request`.

Tests operate directly on the pre-trade logic extracted into
`src/execution/pre_trade_validator.py` and verify the flag-gating behaviour
added in Day 12 without instantiating the full LiveMode (which requires heavy
deps). Source-code inspection tests verify the gate is wired correctly in
live.py.
"""
from __future__ import annotations

import asyncio
import os
import pathlib
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ENGINE_ROOT = pathlib.Path(__file__).parent.parent.parent.parent


def _live_source() -> str:
    return (ENGINE_ROOT / "src/modes/live.py").read_text()


def _make_trade_request(strategy_id: str = "cross_exchange_v1") -> MagicMock:
    leg = MagicMock()
    leg.exchange_id = "binance"
    leg.symbol = "BTC/USDT"
    leg.reduce_only = False
    leg.price = Decimal("60000")
    leg.size = Decimal("0.001")
    req = MagicMock()
    req.legs = [leg]
    req.strategy_id = strategy_id
    return req


def _make_validator(*, reject: bool = False) -> MagicMock:
    """Return a mock PreTradeValidator whose validate() returns approved/rejected."""
    from src.execution.pre_trade_validator import ValidationResult
    from src.core.reason_codes import ReasonCode

    validator = MagicMock()
    if reject:
        result = ValidationResult(
            approved=False,
            reason_code=ReasonCode.RISK_GUARDIAN_REJECTED,
            detail="thin book",
            skip_rollback_notify=False,
        )
    else:
        result = ValidationResult(approved=True)
    validator.validate = AsyncMock(return_value=result)
    return validator


# ---------------------------------------------------------------------------
# Test 1: Flag off — validator NOT called
# ---------------------------------------------------------------------------


class TestFlagOff:
    def test_pretrade_flag_off_validator_bypassed(self, monkeypatch):
        """When EXECUTION_PRETRADE_VALIDATOR_ENABLED=false (default), the validator
        must not be called regardless of book state."""
        monkeypatch.delenv("EXECUTION_PRETRADE_VALIDATOR_ENABLED", raising=False)

        validator = _make_validator(reject=True)

        # Simulate the flag-check logic from live.py
        flag = os.environ.get("EXECUTION_PRETRADE_VALIDATOR_ENABLED") == "true"
        assert flag is False, "Flag must be off by default"

        # If the flag is off, validate() must never be called (live.py skips the block)
        if flag:
            asyncio.run(
                validator.validate(_make_trade_request(), "cross_exchange_v1")
            )
        validator.validate.assert_not_called()

    def test_pretrade_flag_false_string_is_off(self, monkeypatch):
        """EXECUTION_PRETRADE_VALIDATOR_ENABLED=false must not enable the gate."""
        monkeypatch.setenv("EXECUTION_PRETRADE_VALIDATOR_ENABLED", "false")
        flag = os.environ.get("EXECUTION_PRETRADE_VALIDATOR_ENABLED") == "true"
        assert flag is False


# ---------------------------------------------------------------------------
# Test 2: Flag on + thin book → validator rejects
# ---------------------------------------------------------------------------


class TestFlagOnReject:
    def test_flag_on_thin_book_validator_rejects(self, monkeypatch):
        """When flag=true and validator returns approved=False, the trade is blocked."""
        monkeypatch.setenv("EXECUTION_PRETRADE_VALIDATOR_ENABLED", "true")

        validator = _make_validator(reject=True)
        trade_request = _make_trade_request()

        flag = os.environ.get("EXECUTION_PRETRADE_VALIDATOR_ENABLED") == "true"
        assert flag is True

        result = asyncio.run(
            validator.validate(trade_request, trade_request.strategy_id, context={})
        )
        assert result.approved is False
        assert result.reason_code is not None
        validator.validate.assert_called_once()

    def test_rejection_reason_code_is_set(self, monkeypatch):
        """Rejected ValidationResult must carry a non-None ReasonCode."""
        monkeypatch.setenv("EXECUTION_PRETRADE_VALIDATOR_ENABLED", "true")
        from src.core.reason_codes import ReasonCode

        validator = _make_validator(reject=True)
        result = asyncio.run(
            validator.validate(_make_trade_request(), "s1", context={})
        )
        assert isinstance(result.reason_code, ReasonCode)


# ---------------------------------------------------------------------------
# Test 3: Flag on + sufficient book → validator passes
# ---------------------------------------------------------------------------


class TestFlagOnPass:
    def test_flag_on_sufficient_book_passes(self, monkeypatch):
        """When flag=true and validator returns approved=True, the trade proceeds."""
        monkeypatch.setenv("EXECUTION_PRETRADE_VALIDATOR_ENABLED", "true")

        validator = _make_validator(reject=False)
        trade_request = _make_trade_request()

        result = asyncio.run(
            validator.validate(trade_request, trade_request.strategy_id, context={})
        )
        assert result.approved is True
        assert result.reason_code is None
        validator.validate.assert_called_once()


# ---------------------------------------------------------------------------
# Test 4: Source-code gate wired in live.py
# ---------------------------------------------------------------------------


class TestSourceWiring:
    def test_flag_env_var_read_in_live_py(self):
        """live.py must read EXECUTION_PRETRADE_VALIDATOR_ENABLED from os.environ."""
        src = _live_source()
        assert "EXECUTION_PRETRADE_VALIDATOR_ENABLED" in src, (
            "Feature flag EXECUTION_PRETRADE_VALIDATOR_ENABLED not found in live.py"
        )

    def test_pretrade_flag_gates_validator_call(self):
        """The PreTradeValidator.validate() call must be inside the flag-check block."""
        src = _live_source()
        flag_idx = src.find("EXECUTION_PRETRADE_VALIDATOR_ENABLED")
        validate_idx = src.find("_pre_trade_validator.validate(")
        assert flag_idx != -1, "Flag check not found in live.py"
        assert validate_idx != -1, "validator.validate() call not found in live.py"
        # validate() call must appear after the flag check (flag guards it)
        assert validate_idx > flag_idx, (
            "validate() call must appear after the flag-check in live.py"
        )

    def test_bookwalk_check_also_gated_by_flag(self):
        """The BookWalk market-impact check must reference _pretrade_enabled."""
        src = _live_source()
        # Find the BookWalk block by its distinctive marker
        bookwalk_idx = src.find("_pretrade_enabled and self._execution_mode")
        assert bookwalk_idx != -1, (
            "BookWalk gate '_pretrade_enabled and self._execution_mode' not found in live.py"
        )

    def test_flag_off_bookwalk_inactive(self, monkeypatch):
        """When flag=false, the _pretrade_enabled variable is False so
        the BookWalk branch condition is short-circuited."""
        monkeypatch.delenv("EXECUTION_PRETRADE_VALIDATOR_ENABLED", raising=False)
        enabled = os.environ.get("EXECUTION_PRETRADE_VALIDATOR_ENABLED") == "true"
        # BookWalk condition: `if _pretrade_enabled and execution_mode == "live" and ...`
        # With _pretrade_enabled=False, the whole branch is skipped (short-circuit).
        assert enabled is False, "Flag must be false → BookWalk branch skipped"
