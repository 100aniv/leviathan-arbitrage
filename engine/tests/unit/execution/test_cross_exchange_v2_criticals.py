"""C-1 + H-4 — review blocker fixes for Path-B v2 Gate-48H canary.

Covers the three CRITICAL/HIGH review findings promoted pre-Gate:

- C-1: ``CrossExchangeV2Executor(flag=True, state_machine=None)`` raises
  ``ConfigError`` (was logger.warning + silent skip).
- C-1: ``_safe_transition`` promotes ``TransitionError`` handling from DEBUG
  to ERROR so illegal transitions are surfaced to operators.
- H-4: ``_normalize_side`` handles adapter/strategy variants uniformly
  ("BUY"/"Buy"/"long"/"bid" → "buy"; "SELL"/"Sell"/"short"/"ask" → "sell").
"""
from __future__ import annotations

from pathlib import Path

import pytest
import structlog.testing

from src.execution.cross_exchange_v2 import (
    ConfigError,
    CrossExchangeV2Executor,
    _normalize_side,
)
from src.execution.journal import ExecutionJournal
from src.execution.order_state import OrderState, OrderStateMachine
from src.execution.router import OrderRouter
from src.execution.stranded import StrandedPositionTracker
from tests.unit.execution._parallel_legs_conftest import (  # type: ignore[import-not-found]
    enable_all_flags,
)


# ---------------------------------------------------------------------------
# C-1: ConfigError when flag=True and state_machine=None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_c1_config_error_when_flag_on_and_state_machine_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """C-1: flag ON without state_machine must fail fast (§22.3).

    Before the fix the executor only logged a warning, allowing STRANDED
    transitions to be dropped silently. ConfigError prevents boot.
    """
    enable_all_flags(monkeypatch)

    router = OrderRouter()
    stranded = StrandedPositionTracker()
    with pytest.raises(ConfigError, match="state_machine"):
        CrossExchangeV2Executor(
            router=router,
            stranded=stranded,
            state_machine=None,
            ttl_ms=500,
        )


@pytest.mark.asyncio
async def test_c1_flag_off_with_state_machine_none_does_not_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag OFF path stays byte-identical: state_machine=None is harmless."""
    monkeypatch.delenv("EXECUTION_PARALLEL_LEGS_ENABLED", raising=False)

    router = OrderRouter()
    stranded = StrandedPositionTracker()
    # No ConfigError raised.
    executor = CrossExchangeV2Executor(
        router=router,
        stranded=stranded,
        state_machine=None,
        ttl_ms=500,
    )
    assert executor is not None


# ---------------------------------------------------------------------------
# C-1: TransitionError log level promoted to ERROR
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_c1_transition_error_logs_at_error_level(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """C-1: _safe_transition logs illegal transitions at ERROR (not DEBUG).

    Silent DEBUG logs hid illegal transitions (§12.3 ban on silent-debug
    rejects). Promoting to ERROR ensures runbook/operators see the violation.
    Uses ``structlog.testing.capture_logs`` because the module emits via
    structlog, not the stdlib logging surface caplog inspects.
    """
    enable_all_flags(monkeypatch)

    journal = ExecutionJournal(db_path=tmp_path / "c1_transition_error.db")
    await journal.start()
    sm = OrderStateMachine(journal=journal)
    try:
        executor = CrossExchangeV2Executor(
            router=OrderRouter(),
            stranded=StrandedPositionTracker(),
            state_machine=sm,
            ttl_ms=500,
        )
        # Trigger illegal transition: FILLED (terminal) → SENT.
        with structlog.testing.capture_logs() as cap_logs:
            await executor._safe_transition(
                "ORD-ILLEGAL",
                OrderState.FILLED,
                OrderState.SENT,
                {"trace": "c1-test"},
            )
        illegal_events = [
            entry for entry in cap_logs
            if entry.get("event") == "cross_exchange_v2.transition_illegal"
        ]
        assert illegal_events, (
            f"expected transition_illegal event to be emitted; got {cap_logs}"
        )
        # C-1 fix: must be ERROR level (previously DEBUG = silent-reject).
        assert illegal_events[0]["log_level"] == "error", (
            f"expected ERROR level, got {illegal_events[0]!r}"
        )
    finally:
        await journal.stop()


# ---------------------------------------------------------------------------
# H-4: _normalize_side handles adapter/strategy variants
# ---------------------------------------------------------------------------


def test_h4_normalize_side_buy_uppercase() -> None:
    """H-4: 'BUY' → 'buy'."""
    assert _normalize_side("BUY") == "buy"


def test_h4_normalize_side_buy_mixed_case() -> None:
    """H-4: 'Buy' → 'buy'."""
    assert _normalize_side("Buy") == "buy"


def test_h4_normalize_side_bid_alias() -> None:
    """H-4: 'bid' (orderbook-side alias) → 'buy'."""
    assert _normalize_side("bid") == "buy"


def test_h4_normalize_side_long_alias() -> None:
    """H-4: 'long' (futures alias) → 'buy'."""
    assert _normalize_side("long") == "buy"


def test_h4_normalize_side_sell_uppercase() -> None:
    """H-4: 'SELL' → 'sell'."""
    assert _normalize_side("SELL") == "sell"


def test_h4_normalize_side_ask_alias() -> None:
    """H-4: 'ask' (orderbook-side alias) → 'sell'."""
    assert _normalize_side("ask") == "sell"


def test_h4_normalize_side_short_alias() -> None:
    """H-4: 'short' (futures alias) → 'sell'."""
    assert _normalize_side("short") == "sell"


def test_h4_normalize_side_rejects_unknown() -> None:
    """H-4: unrecognised values raise ValueError (no silent mapping)."""
    with pytest.raises(ValueError, match="unrecognised side"):
        _normalize_side("hedge")
