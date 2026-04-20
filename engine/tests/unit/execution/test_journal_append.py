"""Day 6 — ExecutionJournal append + hash-chain tests.

Covers:
1. Empty journal replay.
2. Genesis event (prev_hash = "0"*64).
3. Hash-chain linking across two appends.
4. Chain verification detects tamper.
5. Flag OFF → append no-op, replay empty, DB not created.
6. Concurrency — asyncio.Lock yields deterministic sequential seq.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
from pathlib import Path

import pytest

from src.execution.journal import (
    GENESIS_PREV_HASH,
    ExecutionEvent,
    ExecutionJournal,
)


@pytest.fixture
def enable_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXECUTION_JOURNAL_ENABLED", "true")


@pytest.fixture
def disable_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXECUTION_JOURNAL_ENABLED", "false")


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "journal.db"


@pytest.mark.asyncio
async def test_empty_journal_replay_returns_empty_list(
    enable_flag: None, db_path: Path
) -> None:
    journal = ExecutionJournal(db_path=db_path)
    await journal.start()
    try:
        events = await journal.replay()
        assert events == []
    finally:
        await journal.stop()


@pytest.mark.asyncio
async def test_first_append_uses_genesis_prev_hash(
    enable_flag: None, db_path: Path
) -> None:
    journal = ExecutionJournal(db_path=db_path)
    await journal.start()
    try:
        evt = await journal.append(
            order_id="A", state="SENT", payload={"qty": 10}
        )
        assert evt.prev_hash == GENESIS_PREV_HASH
        assert evt.seq == 1
        assert evt.order_id == "A"
        assert evt.state == "SENT"

        replayed = await journal.replay()
        assert len(replayed) == 1
        assert replayed[0].prev_hash == GENESIS_PREV_HASH
        assert replayed[0].self_hash == evt.self_hash
    finally:
        await journal.stop()


@pytest.mark.asyncio
async def test_hash_chain_links_two_appends(
    enable_flag: None, db_path: Path
) -> None:
    journal = ExecutionJournal(db_path=db_path)
    await journal.start()
    try:
        evt1 = await journal.append(
            order_id="A", state="SENT", payload={"qty": 10}
        )
        evt2 = await journal.append(
            order_id="A", state="ACK", payload={"exchange_order_id": "X1"}
        )
        # Second event's prev_hash must match first event's self_hash.
        assert evt2.prev_hash == evt1.self_hash
        assert evt2.seq == 2
    finally:
        await journal.stop()


@pytest.mark.asyncio
async def test_tampering_breaks_verify_chain(
    enable_flag: None, db_path: Path
) -> None:
    journal = ExecutionJournal(db_path=db_path)
    await journal.start()
    try:
        await journal.append(order_id="A", state="SENT", payload={"qty": 10})
        await journal.append(
            order_id="A", state="FILL", payload={"filled_qty": 10, "price": 100}
        )
        assert await journal.verify_chain() is True
    finally:
        await journal.stop()

    # Tamper directly at the sqlite layer.
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "UPDATE execution_events SET payload_json=? WHERE seq=1",
            (json.dumps({"qty": 9999}, sort_keys=True, separators=(",", ":")),),
        )
        conn.commit()
    finally:
        conn.close()

    journal2 = ExecutionJournal(db_path=db_path)
    await journal2.start()
    try:
        assert await journal2.verify_chain() is False
    finally:
        await journal2.stop()


@pytest.mark.asyncio
async def test_flag_off_append_noop(
    disable_flag: None, db_path: Path
) -> None:
    journal = ExecutionJournal(db_path=db_path)
    await journal.start()
    try:
        evt = await journal.append(
            order_id="A", state="SENT", payload={"qty": 1}
        )
        # Returns a sentinel event (flag off) and does NOT persist.
        assert evt.seq == 0
        assert evt.state == "NOOP"
        assert await journal.replay() == []
    finally:
        await journal.stop()

    # DB file must not exist when flag off.
    assert not db_path.exists(), "DB must not be created when flag is off"


@pytest.mark.asyncio
async def test_concurrent_appends_are_sequential(
    enable_flag: None, db_path: Path
) -> None:
    journal = ExecutionJournal(db_path=db_path)
    await journal.start()
    try:
        async def _append(i: int) -> ExecutionEvent:
            return await journal.append(
                order_id=f"ORD-{i:04d}",
                state="SENT",
                payload={"i": i},
            )

        results = await asyncio.gather(*(_append(i) for i in range(40)))
        seqs = sorted(r.seq for r in results)
        assert seqs == list(range(1, 41))

        replayed = await journal.replay()
        assert len(replayed) == 40
        # Verify chain holds under concurrency.
        assert await journal.verify_chain() is True
    finally:
        await journal.stop()


@pytest.mark.asyncio
async def test_replay_filter_by_order_id(
    enable_flag: None, db_path: Path
) -> None:
    journal = ExecutionJournal(db_path=db_path)
    await journal.start()
    try:
        await journal.append(order_id="A", state="SENT", payload={"i": 1})
        await journal.append(order_id="B", state="SENT", payload={"i": 2})
        await journal.append(order_id="A", state="FILL", payload={"i": 3})

        a_events = await journal.replay(order_id="A")
        assert [e.order_id for e in a_events] == ["A", "A"]
        assert [e.state for e in a_events] == ["SENT", "FILL"]

        b_events = await journal.replay(order_id="B")
        assert [e.order_id for e in b_events] == ["B"]
    finally:
        await journal.stop()


@pytest.mark.asyncio
async def test_replay_filter_by_since_ts(
    enable_flag: None, db_path: Path
) -> None:
    journal = ExecutionJournal(db_path=db_path)
    await journal.start()
    try:
        evt1 = await journal.append(
            order_id="A", state="SENT", payload={"i": 1}
        )
        await asyncio.sleep(0.01)
        evt2 = await journal.append(
            order_id="A", state="FILL", payload={"i": 2}
        )

        # since_ts before evt1 → both.
        both = await journal.replay(since_ts_ms=evt1.ts_ms - 1)
        assert len(both) == 2
        # since_ts after evt1 → only evt2.
        later = await journal.replay(since_ts_ms=evt2.ts_ms)
        assert len(later) == 1
        assert later[0].self_hash == evt2.self_hash
    finally:
        await journal.stop()


@pytest.mark.asyncio
async def test_current_hash_matches_last_event(
    enable_flag: None, db_path: Path
) -> None:
    journal = ExecutionJournal(db_path=db_path)
    await journal.start()
    try:
        assert await journal.current_hash() == GENESIS_PREV_HASH
        evt = await journal.append(
            order_id="A", state="SENT", payload={"q": 1}
        )
        assert await journal.current_hash() == evt.self_hash
    finally:
        await journal.stop()
