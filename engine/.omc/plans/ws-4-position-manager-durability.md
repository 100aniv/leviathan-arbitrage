# WS-4: PositionManager Durability + Single Source of Truth

## Context

4 racy/fragile patterns in `engine/src/main.py` leave Position state non-deterministic and non-durable. Scope: tighten ordering, wire persistence, unify source of truth. Must preserve BUG-78 soft-block semantics and in-flight canary v137 behavior.

## Work Objectives

- Eliminate `asyncio.ensure_future` fire-and-forget on PositionManager mutations.
- Make reconciler see authoritative state synchronously.
- Enable persistence via `dual_writer`.
- Collapse `_position_sizes` duplicate source into PositionManager (gated rollout).

## Guardrails

**Must Have**
- Synchronous in-memory update BEFORE async queue dispatch.
- Queue drain task lifecycle tied to engine start/stop.
- Exception surfacing: drain task logs + increments metric on failure.
- Backward-compat: `_position_sizes` remains populated during Step 4 transition.

**Must NOT Have**
- No schema changes to PositionManager public API.
- No change to BUG-78 FF soft-block logic in `risk_check`.
- No deletion of `_position_sizes` rollback path (line 2114-2126) in this WS.

## Task Flow (Ordered — DO NOT REORDER)

### Step 1: Async Queue + Drain Task (30 LOC, 30 min)
**Depends on:** nothing. **Unblocks:** Steps 2, 3.

- `main.py:~1545` — after PositionManager init, create `self._pm_queue: asyncio.Queue[tuple[str, dict]] = asyncio.Queue(maxsize=1024)` and `self._pm_drain_task: asyncio.Task | None = None`.
- Add `async def _pm_drain_loop(self)`: `while True: op, kwargs = await self._pm_queue.get(); try: await getattr(self._position_manager, op)(**kwargs) except Exception as exc: logger.error("pm_drain_error op=%s: %s", op, exc); metrics.pm_drain_errors.inc()`
- Start in engine `start()` (near existing task spawns): `self._pm_drain_task = asyncio.create_task(self._pm_drain_loop())`.
- Cancel in `stop()`: cancel + await with `except CancelledError`.

**Acceptance:** Grep confirms no `ensure_future(self._position_manager.*)`. Drain task appears in `asyncio.all_tasks()` during runtime.

### Step 2: Synchronous In-Memory Index + Queue Dispatch (25 LOC, 20 min)
**Depends on:** Step 1. **Unblocks:** Step 3.

- `main.py:1843-1861` — replace `ensure_future` calls with:
  - Direct sync call to NEW `self._position_manager.update_index_sync(op, strategy_id, exchange_id, symbol, ...)` that mutates the internal dict only (no I/O).
  - Then `self._pm_queue.put_nowait(("open_position"|"close_position", kwargs))`.
- Add `update_index_sync` method to `PositionManager` (~15 LOC in `position_manager.py`): mutates `self._positions` dict atomically under existing lock.
- On `QueueFull`: log `pm_queue_full` warning, fall back to current `ensure_future` behavior (safety net).

**Acceptance:** After fill event, `position_manager.get_all_positions()` returns the new position within same synchronous tick. Unit test proves this.

### Step 3: Wire dual_writer Persistence (15 LOC, 15 min)
**Depends on:** Step 1 (drain task surfaces DB errors). **Unblocks:** Step 4.

- `main.py:1542-1549` — locate existing dual_writer initialization (search `DualWriter` / `dual_writer` in file). If none exists in Engine, defer this step to a separate US and document in open-questions.
- If exists: replace `dual_writer=None` with `dual_writer=getattr(self, "_dual_writer", None)`.
- Add fallback log: if `None`, log `pm_persistence_disabled reason=no_dual_writer` once at init.
- Gate persistence writes behind `engine.mode in ("paper", "live")` check inside PositionManager (avoid backtest DB churn).

**Acceptance:** In paper mode with Docker up, `SELECT * FROM positions` returns rows after trades. In backtest, no rows written.

### Step 4: Unify used_capital Source (20 LOC, 20 min) — FEATURE-FLAGGED
**Depends on:** Steps 1-3 (PM must be authoritative). **Do NOT** delete `_position_sizes` yet.

- `main.py:1724` — gate via config flag `engine.use_pm_for_capital` (default `False` in v137 canary):
  ```
  if self._config.get("use_pm_for_capital", False) and self._position_manager:
      used_capital = sum(abs(p.quantity * p.entry_price) for p in self._position_manager.get_all_positions())
  else:
      used_capital = sum(self._position_sizes.values()) if self._position_sizes else Decimal("0")
  ```
- `main.py:3905` — no change (already reads from PM).
- Leave `_position_sizes` mutations (1823-1832, 2114-2126) intact for rollback path and v137 compat.

**Acceptance:** With flag off → identical behavior to pre-WS-4. With flag on → shadow test shows used_capital matches PM sum within Decimal("0.01").

## Risk Assessment

| Risk | Mitigation |
|---|---|
| Queue backpressure blocks fill pipeline | `put_nowait` + QueueFull fallback to ensure_future |
| Drain task dies silently | Wrap in outer try/except restart loop; metric `pm_drain_restarts` |
| dual_writer attribute missing | Step 3 gates on `getattr` — falls through safely |
| BUG-78 FF regression | Step 4 flag-gated; v137 canary unaffected |
| `update_index_sync` lock contention | Reuse existing PositionManager lock; no new lock |

## Test Strategy

- **New unit tests** (`tests/risk/test_position_manager_sync.py`, ~60 LOC):
  - `test_update_index_sync_visible_immediately` — mutate, read in same tick.
  - `test_drain_task_processes_queued_ops` — enqueue 10 ops, assert all persisted.
  - `test_drain_task_surfaces_exceptions` — inject failing dual_writer, assert metric++.
  - `test_queue_full_falls_back` — fill queue, assert fallback path taken.
- **Regression**: run existing `tests/` with `pytest -x --tb=short`. Zero failures.
- **Shadow**: 10-min paper run, assert `pm_drain_errors == 0` and reconciler zero-discrepancy.

## Rollback Plan

- Step 4: set `use_pm_for_capital=False` (instant revert, no code deploy).
- Steps 1-3: single commit with clear boundary `refactor(phoenix): WS-4 ...` → `git revert <sha>` restores ensure_future path.
- No DB migrations. `dual_writer=None` fallback preserves no-persistence baseline.

## Size Estimate

- **LOC:** ~90 (Step 1: 30, Step 2: 25 + 15 PM, Step 3: 15, Step 4: 20)
- **Time:** 85 min implementation + 30 min tests + 10 min Shadow verify = **~2 hr**
- **Files touched:** `engine/src/main.py`, `engine/src/risk/position_manager.py`, `engine/tests/risk/test_position_manager_sync.py`

## Success Criteria

- All 4 race conditions closed (grep verifies no `ensure_future` on PM).
- Reconciler reads authoritative state synchronously.
- Paper mode writes positions to TimescaleDB (if dual_writer wired).
- `_position_sizes` duplicate source neutralized behind flag (zero behavior change at `use_pm_for_capital=False`).
- Tests: +4 unit, existing suite green, 10-min Shadow clean.

## Open Questions

- [ ] Does `self._dual_writer` exist on Engine today? If not, Step 3 becomes a separate US (persistence layer wiring). — Blocks Step 3 only.
- [ ] v137 canary currently running? Confirm Step 4 flag default `False` is acceptable. — Blocks Step 4 merge timing.
