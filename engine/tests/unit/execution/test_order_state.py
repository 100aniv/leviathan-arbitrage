"""Day 7 — OrderStateMachine lifecycle + journal emission tests.

Covers:
1. Legal path PENDING → SENT → ACKED → PARTIAL → FILLED emits 4 journal events.
2. Illegal transition (FILLED → SENT) raises TransitionError.
3. Every transition emits exactly one journal event with matching state.
4. Flag OFF → transitions no-op, no events emitted (even for illegal targets).
5. STRANDED terminal state — any outgoing transition raises TransitionError.
6. current_state() reads last journal event for order_id.
7. Flag dependency — state-machine flag ON with journal flag OFF raises ConfigError.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.execution.journal import ExecutionJournal
from src.execution.order_state import (
    ConfigError,
    OrderState,
    OrderStateMachine,
    TransitionError,
)


@pytest.fixture
def enable_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXECUTION_JOURNAL_ENABLED", "true")
    monkeypatch.setenv("EXECUTION_STATE_MACHINE_ENABLED", "true")


@pytest.fixture
def disable_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    # Journal on, state-machine off → transitions must be no-op.
    monkeypatch.setenv("EXECUTION_JOURNAL_ENABLED", "true")
    monkeypatch.setenv("EXECUTION_STATE_MACHINE_ENABLED", "false")


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "sm_journal.db"


@pytest.mark.asyncio
async def test_legal_path_pending_to_filled(
    enable_flags: None, db_path: Path
) -> None:
    journal = ExecutionJournal(db_path=db_path)
    await journal.start()
    sm = OrderStateMachine(journal=journal)
    try:
        oid = "ORD-A"
        e1 = await sm.transition(oid, OrderState.PENDING, OrderState.SENT, {"qty": 10})
        e2 = await sm.transition(oid, OrderState.SENT, OrderState.ACKED, {"exch_id": "X1"})
        e3 = await sm.transition(oid, OrderState.ACKED, OrderState.PARTIAL, {"filled": 4})
        e4 = await sm.transition(oid, OrderState.PARTIAL, OrderState.FILLED, {"filled": 10})
        for e in (e1, e2, e3, e4):
            assert e is not None
        assert [e.state for e in (e1, e2, e3, e4)] == [
            OrderState.SENT.value,
            OrderState.ACKED.value,
            OrderState.PARTIAL.value,
            OrderState.FILLED.value,
        ]
        replayed = await journal.replay(order_id=oid)
        assert [r.state for r in replayed] == ["SENT", "ACKED", "PARTIAL", "FILLED"]
        assert await sm.current_state(oid) == OrderState.FILLED
    finally:
        await journal.stop()


@pytest.mark.asyncio
async def test_illegal_transition_raises_transition_error(
    enable_flags: None, db_path: Path
) -> None:
    journal = ExecutionJournal(db_path=db_path)
    await journal.start()
    sm = OrderStateMachine(journal=journal)
    try:
        oid = "ORD-B"
        # Legal: PENDING → SENT
        await sm.transition(oid, OrderState.PENDING, OrderState.SENT, {})
        # Illegal: FILLED → SENT (terminal state has no outgoing transitions)
        with pytest.raises(TransitionError):
            await sm.transition(oid, OrderState.FILLED, OrderState.SENT, {})
        # Illegal: PENDING → FILLED (no direct edge)
        with pytest.raises(TransitionError):
            await sm.transition(oid, OrderState.PENDING, OrderState.FILLED, {})
        # Journal untouched by illegal attempts (only the 1 legal SENT event).
        replayed = await journal.replay(order_id=oid)
        assert len(replayed) == 1
        assert replayed[0].state == "SENT"
    finally:
        await journal.stop()


@pytest.mark.asyncio
async def test_every_transition_emits_one_journal_event(
    enable_flags: None, db_path: Path
) -> None:
    journal = ExecutionJournal(db_path=db_path)
    await journal.start()
    sm = OrderStateMachine(journal=journal)
    try:
        oid = "ORD-C"
        # 3 legal transitions.
        await sm.transition(oid, OrderState.PENDING, OrderState.SENT, {"n": 1})
        await sm.transition(oid, OrderState.SENT, OrderState.ACKED, {"n": 2})
        await sm.transition(oid, OrderState.ACKED, OrderState.CANCELLED, {"n": 3})
        events = await journal.replay(order_id=oid)
        assert len(events) == 3
        assert [e.state for e in events] == ["SENT", "ACKED", "CANCELLED"]
        assert [e.payload["n"] for e in events] == [1, 2, 3]
        # Hash chain is self-consistent.
        assert await journal.verify_chain() is True
    finally:
        await journal.stop()


@pytest.mark.asyncio
async def test_flag_off_is_pure_noop(
    disable_flag: None, db_path: Path
) -> None:
    journal = ExecutionJournal(db_path=db_path)
    await journal.start()
    sm = OrderStateMachine(journal=journal)
    try:
        oid = "ORD-D"
        # Flag off → returns None, no journal write, no exception for illegal.
        r1 = await sm.transition(oid, OrderState.PENDING, OrderState.SENT, {})
        r2 = await sm.transition(oid, OrderState.FILLED, OrderState.SENT, {})  # illegal
        assert r1 is None
        assert r2 is None
        # current_state returns None because no journal events.
        assert await sm.current_state(oid) is None
        # Journal itself has zero state-machine-authored events.
        events = await journal.replay(order_id=oid)
        assert events == []
    finally:
        await journal.stop()


@pytest.mark.asyncio
async def test_stranded_terminal_no_outgoing_transitions(
    enable_flags: None, db_path: Path
) -> None:
    journal = ExecutionJournal(db_path=db_path)
    await journal.start()
    sm = OrderStateMachine(journal=journal)
    try:
        oid = "ORD-E"
        # Enter STRANDED from SENT (legal rollback-failure path).
        await sm.transition(oid, OrderState.PENDING, OrderState.SENT, {})
        evt = await sm.transition(
            oid,
            OrderState.SENT,
            OrderState.STRANDED,
            {
                "exchange": "bitget",
                "symbol": "ETH/USDT",
                "side": "sell",
                "size": 0.1,
                "value_usd": 300.0,
                "reason": "network timeout",
            },
        )
        assert evt is not None
        assert evt.state == "STRANDED"
        # STRANDED is terminal — no outgoing transitions allowed.
        for target in (
            OrderState.SENT,
            OrderState.ACKED,
            OrderState.FILLED,
            OrderState.CANCELLED,
            OrderState.ROLLED_BACK,
        ):
            with pytest.raises(TransitionError):
                await sm.transition(oid, OrderState.STRANDED, target, {})
        assert await sm.current_state(oid) == OrderState.STRANDED
    finally:
        await journal.stop()


@pytest.mark.asyncio
async def test_current_state_returns_none_when_no_events(
    enable_flags: None, db_path: Path
) -> None:
    journal = ExecutionJournal(db_path=db_path)
    await journal.start()
    sm = OrderStateMachine(journal=journal)
    try:
        assert await sm.current_state("NONEXISTENT") is None
    finally:
        await journal.stop()


def test_flag_dependency_state_machine_requires_journal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """EXECUTION_STATE_MACHINE_ENABLED=true + EXECUTION_JOURNAL_ENABLED=false → ConfigError."""
    monkeypatch.setenv("EXECUTION_STATE_MACHINE_ENABLED", "true")
    monkeypatch.setenv("EXECUTION_JOURNAL_ENABLED", "false")
    journal = ExecutionJournal(db_path=tmp_path / "j.db")
    with pytest.raises(ConfigError):
        OrderStateMachine(journal=journal)


@pytest.mark.asyncio
async def test_partial_to_partial_self_loop_allowed(
    enable_flags: None, db_path: Path
) -> None:
    """H-2 fix: PARTIAL → PARTIAL is legal (supports incremental fills).

    Upstream adapters emit one PARTIAL event per exchange trade update. Before
    H-2 the second update raised TransitionError and the order's true state
    diverged from the journal.
    """
    journal = ExecutionJournal(db_path=db_path)
    await journal.start()
    sm = OrderStateMachine(journal=journal)
    try:
        oid = "ORD-PARTIAL-LOOP"
        # PENDING → SENT → ACKED → PARTIAL establishes the entry point.
        await sm.transition(oid, OrderState.PENDING, OrderState.SENT, {})
        await sm.transition(oid, OrderState.SENT, OrderState.ACKED, {})
        e1 = await sm.transition(oid, OrderState.ACKED, OrderState.PARTIAL, {"filled": 3})
        # Self-loop: subsequent incremental fill still classified as PARTIAL.
        e2 = await sm.transition(oid, OrderState.PARTIAL, OrderState.PARTIAL, {"filled": 6})
        e3 = await sm.transition(oid, OrderState.PARTIAL, OrderState.PARTIAL, {"filled": 9})
        for e in (e1, e2, e3):
            assert e is not None
        events = await journal.replay(order_id=oid)
        states = [e.state for e in events]
        assert states.count("PARTIAL") == 3
        # Can still exit to FILLED.
        await sm.transition(oid, OrderState.PARTIAL, OrderState.FILLED, {"filled": 10})
        assert await sm.current_state(oid) == OrderState.FILLED
    finally:
        await journal.stop()


@pytest.mark.asyncio
async def test_acked_to_acked_self_loop_allowed(
    enable_flags: None, db_path: Path
) -> None:
    """H-2 fix: ACKED → ACKED is legal (supports repeated status updates).

    Some adapters refresh the order status multiple times before the first
    fill (e.g. amend_price events). Re-entering ACKED must not be classified
    illegal.
    """
    journal = ExecutionJournal(db_path=db_path)
    await journal.start()
    sm = OrderStateMachine(journal=journal)
    try:
        oid = "ORD-ACKED-LOOP"
        await sm.transition(oid, OrderState.PENDING, OrderState.SENT, {})
        e1 = await sm.transition(oid, OrderState.SENT, OrderState.ACKED, {"exch_id": "A"})
        # Self-loop: re-ACK after an amend/refresh.
        e2 = await sm.transition(oid, OrderState.ACKED, OrderState.ACKED, {"exch_id": "A'"})
        for e in (e1, e2):
            assert e is not None
        events = await journal.replay(order_id=oid)
        assert [e.state for e in events] == ["SENT", "ACKED", "ACKED"]
        # Downstream transitions still work from ACKED.
        await sm.transition(oid, OrderState.ACKED, OrderState.FILLED, {})
        assert await sm.current_state(oid) == OrderState.FILLED
    finally:
        await journal.stop()
