# Day 8 Plan — OrderRouter (thin adapter boundary with idempotency)

**Path-B v2 Day 8** — Thin adapter-facing boundary that wraps `adapter.place_order(order)` behind a stable `OrderRouter.submit(order, adapter, trace_id, leg_index) → RouteResult` contract with 10-min dedup cache and optional journal/state-machine hooks.

## Goal

Ship an additive, opt-in `OrderRouter` (`src/execution/router.py`) that:
1. Formats a stable `client_order_id = f"{trace_id}.{leg_index}"` per §3.4 of the plan.
2. Deduplicates retries within a 10-minute TTL window (matches `atomic.py:113-128` idempotency behaviour).
3. Emits a `SENT` state transition via the optional Day 7 `OrderStateMachine` before the adapter call.
4. Remains fully transparent when `EXECUTION_ROUTER_ENABLED=false` (default) — bypass directly to `adapter.place_order(order)` with zero behaviour change.

No changes to `live.py`, `main.py`, `executor.py`, or `atomic.py`. Day 14 performs executor migration.

## Acceptance Criteria

1. `OrderRouter.submit(order, adapter, trace_id, leg_index) → RouteResult` — single public entrypoint.
2. `client_order_id` format is exactly `f"{trace_id}.{leg_index}"` (plan §3.4).
3. In-memory dedup cache keyed by `client_order_id`, TTL = 600 s (10 min). Cache entry carries the originally returned `RouteResult`.
4. Duplicate submit within TTL returns the cached `RouteResult` without a second `adapter.place_order` call (idempotency).
5. On TTL expiry the entry is evicted; a subsequent submit with the same `client_order_id` performs a new adapter call.
6. With `EXECUTION_ROUTER_ENABLED=true` AND a `state_machine` instance supplied, a `SENT` event is emitted via `state_machine.transition(...)` **before** the adapter call. Event sequence number (`seq`) is recorded on the returned `RouteResult.journal_event_seq`.
7. With `EXECUTION_ROUTER_ENABLED=true` AND no `state_machine` supplied, no journal interaction occurs and `journal_event_seq` is `None`.
8. With `EXECUTION_ROUTER_ENABLED=false` (default), `submit()` bypasses: direct `adapter.place_order(order)` call, no dedup, no journal hook — byte-identical behaviour.
9. If `adapter.place_order` raises, the exception propagates and **no dedup entry is recorded** (retry must re-attempt).
10. Concurrency-safe: `asyncio.Lock` guards dedup cache read-modify-write.
11. Full unit regression: `pytest tests/unit/ -x --no-cov -q` GREEN. Baseline ≥4949 (Day 6 + Day 9 + Day 10 shipped) → baseline + 7 Day 8 tests, 0 new failures.

## Files Changed

1. `src/execution/router.py` (new, ~180 LOC) — `OrderRouter`, `RouteResult` dataclass.
2. `tests/unit/execution/test_order_router.py` (new, 7 tests) — flag-off bypass, basic submit, dedup cache hit, TTL expiry, client_order_id format, state-machine SENT emission, adapter-raise path.
3. `engine/docs/plans/day-8-plan.md` (this file).
4. `CHANGELOG.md` — `[Unreleased].Added` — Day 8 entry.

## Design Notes

- **`.env.example` already declares `EXECUTION_ROUTER_ENABLED=false`** (line 144). No `.env.example` edit required.
- **State machine is optional** — Day 7 may ship later; `OrderRouter(state_machine=None)` works. Tests inject a stub state machine with the minimum surface area (`.transition(order_id, from_state, to_state, payload)` returning an object with a `.seq` attribute).
- **No coupling to Day 7 `OrderState` enum** — Day 8 passes raw strings (`"PENDING"`, `"SENT"`) so tests can use mocks without importing `OrderState`.
- **TTL = 600 s** matches `atomic.py:80` idempotency cleanup window (5-min) loosened to 10-min to survive longer retry loops as §3.4 of the plan specifies.
- **Concurrency** — `asyncio.Lock` on dedup read-modify-write. The adapter call happens outside the lock so concurrent distinct `client_order_id`s do not serialise.
- **Bypass semantics (flag-off)** — direct `await adapter.place_order(order)`; the returned value must still be wrapped in a `RouteResult` so callers get a stable type.
- **Idempotency cache is in-memory only** — persistence is Day 14's responsibility (executor integration).

## Rollback

`EXECUTION_ROUTER_ENABLED=false` (default) → `OrderRouter.submit()` bypasses all logic and calls the adapter directly. Zero state, zero persistence, zero migration.

## Day 8 does NOT

- Modify `live.py`, `main.py`, `executor.py`, or `atomic.py` (Day 14 migrates).
- Wire `StrandedPositionTracker` / partial-fill flows (Day 14).
- Persist dedup cache across restarts (Day 14 adds journal-replay recovery).
- Handle retry back-off or circuit-breaker logic.
- Replace `atomic.py` idempotency dict — both coexist until Day 14.

## Success evidence

- `pytest tests/unit/execution/test_order_router.py -x --no-cov` → 7 passed.
- `pytest tests/unit/ -x --no-cov -q` → baseline + 7, 0 new failures.
- `python -c "from src.execution.router import OrderRouter, RouteResult"` → clean import.
- `wc -l engine/src/modes/live.py engine/src/main.py engine/src/execution/executor.py engine/src/execution/atomic.py` unchanged vs HEAD pre-Day-8.
- Flag-OFF smoke: `OrderRouter` construction + `submit` on a stub adapter returns without touching the dedup dict or any state machine.
