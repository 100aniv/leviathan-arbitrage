"""Day 6 — ExecutionJournal crash-recovery + WAL behaviour tests.

Covers:
1. Post-restart replay returns all committed events in seq order.
2. Corrupted payload row → verify_chain returns False but replay does not crash.
3. PRAGMA journal_mode=WAL + synchronous=NORMAL enabled on start.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from src.execution.journal import ExecutionJournal


@pytest.fixture
def enable_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXECUTION_JOURNAL_ENABLED", "true")


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "journal.db"


@pytest.mark.asyncio
async def test_post_restart_replay_returns_committed_events(
    enable_flag: None, db_path: Path
) -> None:
    journal = ExecutionJournal(db_path=db_path)
    await journal.start()
    try:
        await journal.append(
            order_id="A", state="SENT", payload={"qty": 1}
        )
        await journal.append(
            order_id="A", state="ACK", payload={"exchange_order_id": "X1"}
        )
        await journal.append(
            order_id="A", state="FILL", payload={"filled": 1, "price": 100}
        )
    finally:
        await journal.stop()

    # Simulate process restart by creating a fresh journal instance.
    journal2 = ExecutionJournal(db_path=db_path)
    await journal2.start()
    try:
        events = await journal2.replay()
        assert [e.state for e in events] == ["SENT", "ACK", "FILL"]
        assert [e.seq for e in events] == [1, 2, 3]
        # Chain must still verify after restart.
        assert await journal2.verify_chain() is True
    finally:
        await journal2.stop()


@pytest.mark.asyncio
async def test_corrupted_payload_detected_by_verify_chain(
    enable_flag: None, db_path: Path
) -> None:
    journal = ExecutionJournal(db_path=db_path)
    await journal.start()
    try:
        await journal.append(
            order_id="A", state="SENT", payload={"qty": 10}
        )
        await journal.append(
            order_id="A", state="FILL", payload={"filled": 10, "price": 100}
        )
    finally:
        await journal.stop()

    # Inject garbage into row 1's payload_json (bypassing append/hash recompute).
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "UPDATE execution_events SET payload_json=? WHERE seq=1",
            (json.dumps({"qty": 99999}, sort_keys=True, separators=(",", ":")),),
        )
        conn.commit()
    finally:
        conn.close()

    # Replay still returns rows (does not crash on corruption).
    journal2 = ExecutionJournal(db_path=db_path)
    await journal2.start()
    try:
        events = await journal2.replay()
        assert len(events) == 2  # Both rows still present.
        # But chain verification catches the tamper.
        assert await journal2.verify_chain() is False
    finally:
        await journal2.stop()


@pytest.mark.asyncio
async def test_wal_mode_and_synchronous_normal_enabled(
    enable_flag: None, db_path: Path
) -> None:
    journal = ExecutionJournal(db_path=db_path)
    await journal.start()
    try:
        await journal.append(
            order_id="A", state="SENT", payload={"qty": 1}
        )
        # `journal_mode=WAL` is persisted at DB level; inspect via a fresh
        # connection. `synchronous` is per-connection, so we query the
        # journal's own pragma snapshot.
        pragmas = await journal.pragma_snapshot()
        assert str(pragmas["journal_mode"]).lower() == "wal"
        # synchronous=NORMAL (1) balances durability vs throughput.
        assert int(pragmas["synchronous"]) == 1
    finally:
        await journal.stop()

    # Independent verification that WAL mode persisted on disk.
    conn = sqlite3.connect(str(db_path))
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert str(mode).lower() == "wal"
    finally:
        conn.close()
