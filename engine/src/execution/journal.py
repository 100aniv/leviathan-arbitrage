"""Day 6 — ExecutionJournal: durable append-only event-sourcing substrate.

Path-B v2 Day 6 substrate for order-lifecycle events (SENT / ACK / FILL /
PARTIAL / CANCEL / REJECT / ...). Persists to a local SQLite WAL database
with per-event SHA256 hash chain. Additive and opt-in — controlled by the
`EXECUTION_JOURNAL_ENABLED` environment flag (default false). No behavioural
impact on `live.py`, `main.py`, or `executor.py` in Day 6; Day 14 migrates
the executor onto this substrate.

Hash chain
----------
Each event stores a `prev_hash` (previous event's `self_hash`) and a
`self_hash` = SHA256(prev_hash | order_id | state | canonical_json(payload)).
Genesis event uses `prev_hash = "0" * 64`. `verify_chain()` walks all rows in
`seq` order and recomputes every hash — any tampering (e.g. a direct
UPDATE on payload_json) breaks the chain.

Concurrency
-----------
A single-process `asyncio.Lock` serialises writes so that `seq` is
deterministic. Multi-process journals are out of scope for Day 6.

Fallback
--------
Prefers `aiosqlite` if installed. Falls back to the stdlib `sqlite3` module
wrapped in `asyncio.to_thread(...)` so the module is install-free.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

logger = structlog.get_logger(__name__)

try:  # Prefer aiosqlite; fall back to sync sqlite3 via to_thread.
    import aiosqlite  # type: ignore[import-not-found]
    _AIOSQLITE_AVAILABLE = True
except ImportError:  # pragma: no cover - covered by env where aiosqlite absent
    aiosqlite = None  # type: ignore[assignment]
    _AIOSQLITE_AVAILABLE = False


GENESIS_PREV_HASH: str = "0" * 64
"""SHA256 placeholder used as prev_hash of the first event."""

FLAG_ENV_VAR: str = "EXECUTION_JOURNAL_ENABLED"
"""Environment flag controlling journal activation (default false)."""

_TRUTHY = {"1", "true", "yes", "on"}


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS execution_events (
    seq           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_ms         INTEGER NOT NULL,
    order_id      TEXT    NOT NULL,
    state         TEXT    NOT NULL,
    payload_json  TEXT    NOT NULL,
    prev_hash     TEXT    NOT NULL,
    self_hash     TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_execution_events_ts_order
    ON execution_events (ts_ms, order_id);
CREATE INDEX IF NOT EXISTS idx_execution_events_order_id
    ON execution_events (order_id);
"""


@dataclass(frozen=True)
class ExecutionEvent:
    """Single row in the execution journal."""

    seq: int
    ts_ms: int
    order_id: str
    state: str
    payload: dict[str, Any]
    prev_hash: str
    self_hash: str


_NOOP_EVENT = ExecutionEvent(
    seq=0,
    ts_ms=0,
    order_id="",
    state="NOOP",
    payload={},
    prev_hash=GENESIS_PREV_HASH,
    self_hash=GENESIS_PREV_HASH,
)


def _canonical_json(payload: dict[str, Any]) -> str:
    """Deterministic JSON encoding for hashing."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _compute_hash(prev_hash: str, order_id: str, state: str, payload_json: str) -> str:
    """SHA256(prev_hash | order_id | state | canonical_json(payload))."""
    h = hashlib.sha256()
    h.update(prev_hash.encode("utf-8"))
    h.update(b"|")
    h.update(order_id.encode("utf-8"))
    h.update(b"|")
    h.update(state.encode("utf-8"))
    h.update(b"|")
    h.update(payload_json.encode("utf-8"))
    return h.hexdigest()


def _flag_enabled() -> bool:
    """Read EXECUTION_JOURNAL_ENABLED at call time (dynamic tests use monkeypatch)."""
    return os.environ.get(FLAG_ENV_VAR, "false").strip().lower() in _TRUTHY


# ---------------------------------------------------------------------------
# Prometheus metrics (lazy — avoid duplicate registration under pytest reruns).
# ---------------------------------------------------------------------------

_METRICS: dict[str, Any] = {}


def _metric_events_total() -> Any | None:
    if "events_total" not in _METRICS:
        try:
            from prometheus_client import Counter
        except Exception:  # pragma: no cover - prometheus missing
            return None
        try:
            _METRICS["events_total"] = Counter(
                "leviathan_execution_journal_events_total",
                "ExecutionJournal events appended",
                ["state"],
            )
        except ValueError:  # pragma: no cover - already registered
            return None
    return _METRICS.get("events_total")


def _metric_write_latency() -> Any | None:
    if "write_latency_ms" not in _METRICS:
        try:
            from prometheus_client import Histogram
        except Exception:  # pragma: no cover
            return None
        try:
            _METRICS["write_latency_ms"] = Histogram(
                "leviathan_execution_journal_write_latency_ms",
                "ExecutionJournal append() wall-clock latency (ms)",
                buckets=[0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 25.0, 50.0, 100.0, 250.0, 500.0],
            )
        except ValueError:  # pragma: no cover
            return None
    return _METRICS.get("write_latency_ms")


class ExecutionJournal:
    """SQLite-WAL hash-chained event log.

    Use pattern::

        journal = ExecutionJournal(db_path=Path("engine/.omc/state/journal.db"))
        await journal.start()
        evt = await journal.append(order_id="A", state="SENT", payload={"qty": 10})
        ...
        events = await journal.replay()
        await journal.stop()

    With `EXECUTION_JOURNAL_ENABLED=false` (default), `append()` returns a
    sentinel NOOP event, `replay()` returns `[]`, and no DB file is created.
    """

    def __init__(self, db_path: Path, flag_env: str = FLAG_ENV_VAR) -> None:
        self._db_path = Path(db_path)
        self._flag_env = flag_env
        self._lock = asyncio.Lock()
        self._started = False
        self._last_hash = GENESIS_PREV_HASH
        # Held only when aiosqlite path is active.
        self._aio_conn: Any | None = None
        # Pragma snapshot captured at start() — stable even after stop().
        self._pragma_snapshot: dict[str, Any] = {}

    # ------------------------------------------------------------------ setup

    def _flag_active(self) -> bool:
        return os.environ.get(self._flag_env, "false").strip().lower() in _TRUTHY

    async def start(self) -> None:
        """Open DB, create schema, set WAL pragmas, load last_hash.

        Flag OFF: no DB file is created and no connection is opened.
        """
        if self._started:
            return
        if not self._flag_active():
            self._started = True
            return

        self._db_path.parent.mkdir(parents=True, exist_ok=True)

        if _AIOSQLITE_AVAILABLE:
            self._aio_conn = await aiosqlite.connect(str(self._db_path))
            await self._aio_conn.executescript(_SCHEMA_SQL)
            await self._aio_conn.execute("PRAGMA journal_mode=WAL")
            await self._aio_conn.execute("PRAGMA synchronous=NORMAL")
            await self._aio_conn.commit()
            mode_cursor = await self._aio_conn.execute("PRAGMA journal_mode")
            mode_row = await mode_cursor.fetchone()
            await mode_cursor.close()
            sync_cursor = await self._aio_conn.execute("PRAGMA synchronous")
            sync_row = await sync_cursor.fetchone()
            await sync_cursor.close()
            self._pragma_snapshot = {
                "journal_mode": mode_row[0] if mode_row else None,
                "synchronous": sync_row[0] if sync_row else None,
            }
        else:
            self._pragma_snapshot = await asyncio.to_thread(self._sync_init_schema)

        self._last_hash = await self._load_last_hash()
        self._started = True
        logger.info(
            "execution_journal_started",
            db_path=str(self._db_path),
            backend="aiosqlite" if _AIOSQLITE_AVAILABLE else "sqlite3",
            last_hash_prefix=self._last_hash[:8],
        )

    def _sync_init_schema(self) -> dict[str, Any]:
        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.executescript(_SCHEMA_SQL)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.commit()
            mode_row = conn.execute("PRAGMA journal_mode").fetchone()
            sync_row = conn.execute("PRAGMA synchronous").fetchone()
            return {
                "journal_mode": mode_row[0] if mode_row else None,
                "synchronous": sync_row[0] if sync_row else None,
            }
        finally:
            conn.close()

    async def stop(self) -> None:
        if not self._started:
            return
        if self._aio_conn is not None:
            try:
                await self._aio_conn.close()
            except Exception:  # pragma: no cover
                logger.warning("execution_journal_close_failed", exc_info=True)
            self._aio_conn = None
        self._started = False

    # ----------------------------------------------------------------- append

    async def append(
        self,
        order_id: str,
        state: str,
        payload: dict[str, Any],
    ) -> ExecutionEvent:
        """Append a single event. Returns the persisted ExecutionEvent.

        When the feature flag is OFF, returns a sentinel NOOP event and does
        NOT touch disk.
        """
        if not self._flag_active():
            return _NOOP_EVENT
        if not self._started:
            # Defensive: permit append() before explicit start() in test code.
            await self.start()

        t0 = time.perf_counter()
        async with self._lock:
            ts_ms = int(time.time() * 1000)
            payload_json = _canonical_json(payload)
            prev_hash = self._last_hash
            self_hash = _compute_hash(prev_hash, order_id, state, payload_json)

            seq = await self._insert_row(
                ts_ms=ts_ms,
                order_id=order_id,
                state=state,
                payload_json=payload_json,
                prev_hash=prev_hash,
                self_hash=self_hash,
            )
            self._last_hash = self_hash

        latency_ms = (time.perf_counter() - t0) * 1000.0
        counter = _metric_events_total()
        if counter is not None:
            try:
                counter.labels(state=state).inc()
            except Exception:  # pragma: no cover
                pass
        hist = _metric_write_latency()
        if hist is not None:
            try:
                hist.observe(latency_ms)
            except Exception:  # pragma: no cover
                pass

        return ExecutionEvent(
            seq=seq,
            ts_ms=ts_ms,
            order_id=order_id,
            state=state,
            payload=dict(payload),
            prev_hash=prev_hash,
            self_hash=self_hash,
        )

    async def _insert_row(
        self,
        ts_ms: int,
        order_id: str,
        state: str,
        payload_json: str,
        prev_hash: str,
        self_hash: str,
    ) -> int:
        sql = (
            "INSERT INTO execution_events "
            "(ts_ms, order_id, state, payload_json, prev_hash, self_hash) "
            "VALUES (?, ?, ?, ?, ?, ?)"
        )
        params = (ts_ms, order_id, state, payload_json, prev_hash, self_hash)
        if self._aio_conn is not None:
            cursor = await self._aio_conn.execute(sql, params)
            await self._aio_conn.commit()
            seq = cursor.lastrowid
            await cursor.close()
            assert seq is not None
            return int(seq)
        return await asyncio.to_thread(self._sync_insert, sql, params)

    def _sync_insert(self, sql: str, params: tuple[Any, ...]) -> int:
        conn = sqlite3.connect(str(self._db_path))
        try:
            cursor = conn.execute(sql, params)
            conn.commit()
            seq = cursor.lastrowid
            cursor.close()
            assert seq is not None
            return int(seq)
        finally:
            conn.close()

    # ----------------------------------------------------------------- replay

    async def replay(
        self,
        since_ts_ms: int | None = None,
        order_id: str | None = None,
    ) -> list[ExecutionEvent]:
        """Return events in seq order, optionally filtered."""
        if not self._flag_active():
            return []
        if not self._started:
            await self.start()

        clauses: list[str] = []
        params: list[Any] = []
        if since_ts_ms is not None:
            clauses.append("ts_ms >= ?")
            params.append(int(since_ts_ms))
        if order_id is not None:
            clauses.append("order_id = ?")
            params.append(order_id)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = (
            "SELECT seq, ts_ms, order_id, state, payload_json, prev_hash, self_hash "
            "FROM execution_events"
            f"{where} ORDER BY seq ASC"
        )

        if self._aio_conn is not None:
            cursor = await self._aio_conn.execute(sql, params)
            rows = await cursor.fetchall()
            await cursor.close()
        else:
            rows = await asyncio.to_thread(self._sync_fetchall, sql, tuple(params))

        out: list[ExecutionEvent] = []
        for row in rows:
            seq, ts_ms, oid, state, payload_json, prev_hash, self_hash = row
            try:
                payload = json.loads(payload_json)
            except json.JSONDecodeError:
                payload = {"__raw__": payload_json}
            out.append(
                ExecutionEvent(
                    seq=int(seq),
                    ts_ms=int(ts_ms),
                    order_id=str(oid),
                    state=str(state),
                    payload=payload,
                    prev_hash=str(prev_hash),
                    self_hash=str(self_hash),
                )
            )
        return out

    def _sync_fetchall(
        self, sql: str, params: tuple[Any, ...]
    ) -> list[tuple[Any, ...]]:
        conn = sqlite3.connect(str(self._db_path))
        try:
            cursor = conn.execute(sql, params)
            rows = cursor.fetchall()
            cursor.close()
            return list(rows)
        finally:
            conn.close()

    # ---------------------------------------------------------------- verify

    async def verify_chain(self) -> bool:
        """Walk all rows in seq order, recompute hashes, return False on break."""
        if not self._flag_active():
            return True
        events = await self.replay()
        prev_hash = GENESIS_PREV_HASH
        for evt in events:
            if evt.prev_hash != prev_hash:
                logger.warning(
                    "execution_journal_chain_break",
                    seq=evt.seq,
                    expected_prev_hash=prev_hash[:8],
                    actual_prev_hash=evt.prev_hash[:8],
                )
                return False
            expected = _compute_hash(
                evt.prev_hash, evt.order_id, evt.state, _canonical_json(evt.payload)
            )
            if expected != evt.self_hash:
                logger.warning(
                    "execution_journal_hash_mismatch",
                    seq=evt.seq,
                    expected=expected[:8],
                    actual=evt.self_hash[:8],
                )
                return False
            prev_hash = evt.self_hash
        return True

    async def current_hash(self) -> str:
        """Return the self_hash of the latest event, or genesis if empty."""
        if not self._flag_active():
            return GENESIS_PREV_HASH
        if not self._started:
            await self.start()
        return self._last_hash

    async def pragma_snapshot(self) -> dict[str, Any]:
        """Return the PRAGMA settings captured at start().

        SQLite's `synchronous` pragma is per-connection; this method returns
        the values that were actually set on the journal's own connection.
        """
        if not self._flag_active():
            return {}
        if not self._started:
            await self.start()
        return dict(self._pragma_snapshot)

    async def _load_last_hash(self) -> str:
        sql = "SELECT self_hash FROM execution_events ORDER BY seq DESC LIMIT 1"
        if self._aio_conn is not None:
            cursor = await self._aio_conn.execute(sql)
            row = await cursor.fetchone()
            await cursor.close()
        else:
            row = await asyncio.to_thread(self._sync_fetchone, sql)
        if row is None:
            return GENESIS_PREV_HASH
        return str(row[0])

    def _sync_fetchone(self, sql: str) -> tuple[Any, ...] | None:
        conn = sqlite3.connect(str(self._db_path))
        try:
            cursor = conn.execute(sql)
            row = cursor.fetchone()
            cursor.close()
            return row
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Module-singleton accessor for DI (wired by Day 14 executor migration).
# Tests should instantiate ExecutionJournal directly with tmp db_path.
# ---------------------------------------------------------------------------

_SINGLETON: ExecutionJournal | None = None
_SINGLETON_LOCK = asyncio.Lock()


def _default_db_path() -> Path:
    # engine/src/execution/journal.py → engine/.omc/state/execution_journal.db
    here = Path(__file__).resolve()
    engine_root = here.parents[2]
    return engine_root / ".omc" / "state" / "execution_journal.db"


async def get_execution_journal() -> ExecutionJournal:
    """Return (constructing on demand) the module-singleton journal."""
    global _SINGLETON
    async with _SINGLETON_LOCK:
        if _SINGLETON is None:
            _SINGLETON = ExecutionJournal(db_path=_default_db_path())
            await _SINGLETON.start()
        return _SINGLETON


__all__ = [
    "ExecutionEvent",
    "ExecutionJournal",
    "FLAG_ENV_VAR",
    "GENESIS_PREV_HASH",
    "get_execution_journal",
]
