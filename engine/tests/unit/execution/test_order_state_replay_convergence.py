"""Day 7 — Replay convergence (Codex/Gemini consensus ask).

Two independent consumers reading the same journal must reach the
identical end-state regardless of replay order preference. Concurrent
transitions on *different* order_ids must commit in journal (seq) order.

Covers:
1. Two consumers replay the same order_id and derive identical OrderState.
2. Concurrent transitions on different order_ids serialise via journal.seq;
   all events committed, chain verifies, per-order end states stable.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from src.execution.journal import ExecutionJournal
from src.execution.order_state import OrderState, OrderStateMachine


@pytest.fixture
def enable_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXECUTION_JOURNAL_ENABLED", "true")
    monkeypatch.setenv("EXECUTION_STATE_MACHINE_ENABLED", "true")


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "sm_convergence.db"


@pytest.mark.asyncio
async def test_two_consumers_same_journal_agree_on_end_state(
    enable_flags: None, db_path: Path
) -> None:
    """Consumers reading the same journal converge on identical end-state.

    Writer writes PENDING → SENT → ACKED → FILLED for order A.
    Two consumers (each with their own OrderStateMachine instance pointed
    at the same journal) call current_state("A"). They must both return
    OrderState.FILLED regardless of construction order.
    """
    journal_w = ExecutionJournal(db_path=db_path)
    await journal_w.start()
    writer = OrderStateMachine(journal=journal_w)
    try:
        oid = "ORD-CONVERGE"
        await writer.transition(oid, OrderState.PENDING, OrderState.SENT, {"n": 1})
        await writer.transition(oid, OrderState.SENT, OrderState.ACKED, {"n": 2})
        await writer.transition(oid, OrderState.ACKED, OrderState.FILLED, {"n": 3})
    finally:
        await journal_w.stop()

    # Two fresh consumers open the same DB — both must read FILLED.
    journal_c1 = ExecutionJournal(db_path=db_path)
    await journal_c1.start()
    journal_c2 = ExecutionJournal(db_path=db_path)
    await journal_c2.start()
    try:
        consumer1 = OrderStateMachine(journal=journal_c1)
        consumer2 = OrderStateMachine(journal=journal_c2)
        # Consumer order deliberately differs per task.
        s2 = await consumer2.current_state(oid)
        s1 = await consumer1.current_state(oid)
        assert s1 == OrderState.FILLED
        assert s2 == OrderState.FILLED
        assert s1 == s2
        # Hash chain integrity holds across consumers.
        assert await journal_c1.verify_chain() is True
        assert await journal_c2.verify_chain() is True
    finally:
        await journal_c1.stop()
        await journal_c2.stop()


@pytest.mark.asyncio
async def test_concurrent_transitions_on_different_order_ids(
    enable_flags: None, db_path: Path
) -> None:
    """Concurrent transitions on distinct order_ids all commit in journal order.

    Launches 20 concurrent tasks, each driving its own order_id through
    PENDING → SENT. All 20 events must appear in the journal with a
    unique sequential `seq`; per-order end state is SENT; chain verifies.
    """
    journal = ExecutionJournal(db_path=db_path)
    await journal.start()
    sm = OrderStateMachine(journal=journal)
    try:
        async def _drive(i: int) -> None:
            oid = f"ORD-{i:04d}"
            await sm.transition(oid, OrderState.PENDING, OrderState.SENT, {"i": i})

        await asyncio.gather(*(_drive(i) for i in range(20)))

        events = await journal.replay()
        assert len(events) == 20
        # `seq` is a total order across all 20 transitions.
        seqs = sorted(e.seq for e in events)
        assert seqs == list(range(1, 21))
        # Each order_id ends in SENT.
        for i in range(20):
            oid = f"ORD-{i:04d}"
            assert await sm.current_state(oid) == OrderState.SENT
        # Hash chain holds under concurrency.
        assert await journal.verify_chain() is True
    finally:
        await journal.stop()
