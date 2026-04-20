# Day 6 Plan — ExecutionJournal (durable event sourcing substrate)

**Path-B v2 Day 6** — Append-only hash-chained event log for order lifecycle.

## Goal

Ship an additive, opt-in durable event-sourcing substrate (`src/execution/journal.py`) that
becomes the foundation for Day 7 `OrderStateMachine` and Day 14 executor migration.
Persists order intent → ACK → fill → cancel events to a local SQLite WAL database with
per-event SHA256 hash chaining. No behavioural change until `EXECUTION_JOURNAL_ENABLED=true`.

## Acceptance Criteria

1. `ExecutionEvent` dataclass — `(seq, ts_ms, order_id, state, payload_json, prev_hash, self_hash)` persisted.
2. Hash chain — `self_hash = SHA256(prev_hash | order_id | state | canonical_json(payload))`, genesis `prev_hash = "0"*64`.
3. `replay(since_ts_ms=None, order_id=None) -> list[ExecutionEvent]` returns events in `seq` order.
4. `append(order_id, state, payload) -> ExecutionEvent` writes durably (SQLite WAL + `synchronous=NORMAL`).
5. `verify_chain() -> bool` walks full log, recomputes each hash, returns False on tamper.
6. Concurrency — `asyncio.Lock` serialises writes; 40 concurrent appends produce deterministic sequential `seq`.
7. Flag OFF (`EXECUTION_JOURNAL_ENABLED=false`) — `append()` no-ops, `replay()` returns `[]`, no DB file created.
8. Crash recovery — mid-write termination leaves DB readable via SQLite WAL; corrupted rows do not crash replay.
9. Full unit regression: `pytest engine/tests/unit/ -x --no-cov` GREEN (baseline 4937 → 4943+, 0 new failures).

## Files Changed

1. `src/execution/journal.py` (new, ~300 LOC) — `ExecutionJournal`, `ExecutionEvent`, `get_execution_journal()`.
2. `src/execution/journal_schema.sql` (new) — standalone schema for inspectability.
3. `tests/unit/execution/test_journal_append.py` (new) — 6 tests: empty, genesis, chain, tamper, flag-off, concurrency.
4. `tests/unit/execution/test_journal_crash_recovery.py` (new) — 3 tests: corrupt-row skip, post-restart replay, fsync behaviour.
5. `CHANGELOG.md` — `[Unreleased].Added` — Day 6 entry.
6. (no changes) `.env.example` already declares `EXECUTION_JOURNAL_ENABLED=false` (line 142).

Estimated ~450 LOC net (impl + tests). `live.py` / `main.py` / `executor.py` **unchanged**.

## Design Notes

- **aiosqlite preferred, sync `sqlite3` + `asyncio.to_thread` fallback.** Module auto-detects. Keeps Day 6 install-free (no `requirements.txt` change required).
- **WAL mode** — `PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL` → survives crash up to last committed row.
- **Canonical JSON** — `json.dumps(payload, sort_keys=True, separators=(",", ":"))` for reproducible hashing.
- **DB path** — default `{worktree}/engine/.omc/state/execution_journal.db`. Parent dir created at `start()` with `mkdir(parents=True, exist_ok=True)`.
- **Singleton** — `get_execution_journal()` for DI later; first call constructs, re-entrant safe. Tests pass explicit `db_path` to avoid singleton pollution.
- **Prometheus** — two metrics (Counter + Histogram) registered lazily on first `start()` to avoid duplicate-registration errors in pytest.

## Rollback

`EXECUTION_JOURNAL_ENABLED=false` (default) → journal `append()` is a no-op, `replay()` returns `[]`.
Delete `engine/.omc/state/execution_journal.db` if created experimentally. No migration needed.

## Day 6 does NOT

- Write to `live.py`, `main.py`, `executor.py`, `atomic.py`. (Day 14 migrates.)
- Replace in-memory idempotency (`atomic.py:66`) or Redis WAL (`position_recovery.py:72`). Both remain authoritative until Day 14.
- Expose a REST / dashboard view (deferred to W3).
- Implement automatic GC / retention. Journal grows monotonically until ops manually truncates.

## Success evidence

- Baseline `pytest engine/tests/unit/ -x --no-cov -q` = 4937 passed pre-change.
- Post-Day-6 `pytest engine/tests/unit/ -x --no-cov -q` = 4937 + 9 = 4946 passed, 0 new failures.
- `python -c "from src.execution.journal import ExecutionJournal, get_execution_journal, ExecutionEvent"` imports cleanly.
- `wc -l engine/src/modes/live.py engine/src/main.py` unchanged vs `HEAD` pre-Day-6.
- Flag OFF smoke: `ls engine/.omc/state/execution_journal.db` → ENOENT (no file created).
