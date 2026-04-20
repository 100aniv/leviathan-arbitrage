# Day 11 Plan — IOC-TTL parallel cross-exchange legs (HIGH risk, 5d rescoped)

**Path-B v2 Day 11** — Convert cross-exchange execution from sequential
(`executor.py:1050 → 1276`, 200-480 ms naked-exposure window) to concurrent
`asyncio.gather` submission with IOC TTL on both legs and a new
both-legs-stranded rollback topology.

## Goal

Shrink the post-leg1-fill / pre-leg2-ACK naked-exposure window to p95 < 50 ms
by submitting both legs concurrently as IOC limits (TTL ≤ 5 s, no market
fallback) and handling the four resulting outcomes explicitly:

| Outcome           | Action                                                   |
| ----------------- | -------------------------------------------------------- |
| Both legs fill    | Return `ExecutionStatus.SUCCESS`                         |
| Only leg1 fills   | Register via `StrandedPositionTracker`, status `STRANDED_LEG1` |
| Only leg2 fills   | Mirror path, status `STRANDED_LEG2`                      |
| Neither fills     | IOC TTL cancels cleanly on-exchange; no rollback needed  |
| Both fill but edge gone | `_do_rollback_cross_parallel(list[LegState], reason)` unwinds both with `asyncio.gather` |

## Approach

- **New module** `src/execution/cross_exchange_v2.py` (~260 LOC) holds
  `CrossExchangeV2Executor` and `_do_rollback_cross_parallel`. Does **not**
  modify `src/execution/executor.py:1050-1276` — Day 14 migrates the legacy
  executor onto this substrate.
- **Extract** `AtomicOrderExecutor.try_ioc()` from the first half of
  `atomic.py::AtomicOrderExecutor.execute()` (lines 134-151). Pure IOC
  attempt, no market fallback. `execute()` keeps its existing external API
  and delegates the IOC half to the new primitive, so every caller
  (`atomic.py` tests, `executor.py`, etc.) stays byte-compatible.
- **Reuse** existing Day 7/8 infrastructure: `OrderRouter.submit()` for the
  adapter boundary + dedup cache, `OrderStateMachine` transitions for the
  hash-chained journal trail. `OrderStateMachine` is **optional**
  (falls back to no-op with a WARN) so paper environments without a journal
  flag still run.
- **Reuse** `StrandedPositionTracker` for the two single-leg-stranded
  outcomes, same call shape already used in the sequential path.
- **Feature flag** `EXECUTION_PARALLEL_LEGS_ENABLED` (default `false`).
  §22.3 Flag-Interaction Matrix: requires
  `EXECUTION_JOURNAL_ENABLED`,
  `EXECUTION_STATE_MACHINE_ENABLED`,
  `EXECUTION_ROUTER_ENABLED` all `true`. Enforced at construction via
  `ConfigError`.

## Acceptance Criteria

- `CrossExchangeV2Executor.execute(trade_request, adapter_a, adapter_b)`
  dispatches both legs via `asyncio.gather` and returns an
  `ExecResultV2` with one of five statuses
  (`SUCCESS`, `STRANDED_LEG1`, `STRANDED_LEG2`, `NEITHER`,
  `EDGE_EVAPORATED`, `ROLLED_BACK`, `DISABLED`).
- Flag off → returns `DISABLED`, adapter calls never issued, no side effects.
- Flag on + required-dependency flag missing → `ConfigError` at construction.
- Each leg uses `AtomicOrderExecutor.try_ioc()` with
  `ttl_ms = self._ttl_ms` (default 5 000). No market fallback path.
- Pre-gather edge re-check rejects stale signals (~50 ms fresh-book
  latency): returns `EDGE_EVAPORATED` without any submit.
- Four outcome branches covered by dedicated integration tests + a
  `CancelledError` propagation test + an edge-evaporated test (6 tests).
- `_do_rollback_cross_parallel(leg_states: list[LegState], reason: str)`
  unwinds both legs via `asyncio.gather` when a post-hoc invariant check
  fails (both legs filled but size/price mismatch → both-stranded fallback
  rollback).
- `atomic.py::AtomicOrderExecutor.execute()` external API unchanged:
  every existing atomic.py test passes without modification.
- `executor.py:1050-1276` untouched — monotonic shrink invariant preserved.
  `live.py` and `main.py` untouched.
- Full unit regression green (`pytest tests/unit/ -x --no-cov`):
  baseline + 6 new tests, 0 new failures.

## Files Changed

1. `src/execution/atomic.py` — surgical extraction of `try_ioc()` (~30 LOC
   touched, no behaviour change). `execute()` delegates its IOC half.
2. `src/execution/cross_exchange_v2.py` — new (~260 LOC). Holds
   `CrossExchangeV2Executor`, `ExecResultV2`, `LegState`,
   `ExecutionStatusV2`, `_do_rollback_cross_parallel`, `try_ioc` adapter
   bridge.
3. `tests/unit/execution/test_parallel_legs_both_fill.py` — new.
4. `tests/unit/execution/test_parallel_legs_leg1_only.py` — new.
5. `tests/unit/execution/test_parallel_legs_leg2_only.py` — new.
6. `tests/unit/execution/test_parallel_legs_both_stranded.py` — new.
7. `tests/unit/execution/test_parallel_legs_edge_evaporated.py` — new.
8. `tests/unit/execution/test_parallel_legs_async_cancellation.py` — new.
9. `CHANGELOG.md` — `[Unreleased].Added` bullet for Day 11.

## Rollback

- Feature flag default `false` → production is untouched on merge.
- Revert the two `src/execution/*.py` source diffs + the six test files +
  the CHANGELOG bullet. No schema/migration changes required.
- Sequential path (`executor.py:1050-1276`) stays live as rollback
  insurance for 2 weeks post-Day-11 per plan §3 Day 11 rollback criteria.

## Notes / Operator

- `EXECUTION_PARALLEL_LEGS_ENABLED` is **not** set in any rollout env yet.
  Day 11 lands the plumbing; Day 14 (executor migration) will actually
  route cross-exchange traffic through it, at which point the flag will be
  flipped in a paper canary first.
- `p95 naked-exposure < 50 ms` is the stated target once activated; Day 11
  code does not measure it yet — Day 15 (`TradingSupervisor`) adds the
  observability.
- `try_ioc` extraction is purely refactor-level: the partial-close + depth
  rejection + idempotency check branches stay inside `AtomicOrderExecutor.execute()`
  and are untouched.
- If `OrderStateMachine` is not wired (flag off), parallel executor logs a
  WARN and proceeds (no-op transitions). This matches the §22.3 matrix
  dependency gating — Day 11 enforces the matrix at executor construction
  so mis-configuration fails fast.
