# Day 14 Plan — Migrate executor.py to OrderStateMachine + ExecutionJournal

**Path-B v2 Day 14** — Replace scattered `leg1_filled`/`leg2_filled` booleans and
ad-hoc `logger.warning` trails in `src/execution/executor.py` with explicit state
transitions via the Day 7 `OrderStateMachine`, which emits into the Day 6
`ExecutionJournal`.

## Goal

Every lifecycle event in `AtomicExecutor.execute_same_exchange`,
`AtomicExecutor.execute_multi_leg`, and `AtomicExecutor.execute_cross_exchange`
is mapped to one of the 9 canonical `OrderState` values and emitted via
`state_machine.transition(...)`. The journal becomes the source of truth for
order lifecycle; in-memory booleans (`leg1_filled`, `leg2_filled`) become
derivations of the latest journal state (or remain as fast path when the flags
are off).

## Acceptance Criteria

- Every code path in `execute_same_exchange`, `execute_multi_leg`,
  `execute_cross_exchange`, and `_do_rollback_cross` emits
  `SENT → ACKED → FILLED` / `CANCELLED` / `REJECTED` / `ROLLED_BACK` /
  `STRANDED` via the injected `OrderStateMachine` (when wired).
- `leg1_filled` / `leg2_filled` booleans continue to derive from in-memory
  `LegResult.trade` for hot-path speed, but lifecycle events are asserted via
  journal replay when flags are on.
- Flags off (`EXECUTION_JOURNAL_ENABLED=false`
  + `EXECUTION_STATE_MACHINE_ENABLED=false`) → executor behaviour is byte-identical
  to pre-Day-14 baseline. No journal DB is touched, no state-machine calls.
- Flags on → every lifecycle edge is recorded. `journal.replay(order_id=...)`
  reconstructs the full path.
- 5 new test cases in `test_executor_journal_complete.py` cover:
  1. SUCCESS (same-exchange): SENT → ACKED → FILLED on both legs.
  2. Partial fill > 80 % (cross-exchange): SENT → ACKED → PARTIAL → FILLED.
  3. Rollback (cross-exchange leg2 failure): SENT → ACKED → ROLLED_BACK.
  4. Stranded (rollback fails): SENT → STRANDED.
  5. Flags off: no journal rows written, executor behaviour unchanged.
- Regression: 5000 → 5005+ green, 0 new failures. Existing `test_executor.py`
  148+ tests all still pass.

## Design — additive DI, optional flag-gated wiring

`AtomicExecutor.__init__` accepts **optional** `state_machine: OrderStateMachine
| None = None` and `journal: ExecutionJournal | None = None`. Default `None`
preserves flag-off behaviour end-to-end. When `state_machine is not None`, the
executor emits lifecycle transitions at fixed points:

| Executor call site | Emission |
|--------------------|----------|
| Before `adapter.place_order` | `PENDING → SENT` |
| After successful `place_order` return | `SENT → ACKED`, then `ACKED → FILLED` (or `PARTIAL`) |
| On `asyncio.TimeoutError` / `Exception` | `SENT → REJECTED` (or `CANCELLED` after rollback) |
| On successful `_rollback_order` | `ACKED → ROLLED_BACK` |
| On failed `_rollback_order` + stranded register | `ACKED → STRANDED` (or `SENT → STRANDED`) |

All emissions wrapped in `_maybe_transition(order_id, from_state, to_state, payload)` —
swallows `TransitionError` as `logger.warning` (never raises) and no-ops when
`state_machine is None`.

## Rollback

Set both flags to `false` (default). Executor becomes byte-identical to
pre-Day-14.

## Risk: MED (2 days)

Executor is the 1,587 LOC hot path for all trading strategies. Every existing
test must still pass with flag-off to preserve paper/live behaviour guarantees.

## Files

- `engine/src/execution/executor.py` — surgical additive DI + ~20 helper-wrapped
  emission sites (~40-60 LOC touched).
- `engine/tests/unit/execution/test_executor_journal_complete.py` — new file,
  5 tests.
- `CHANGELOG.md` — `[Unreleased].Changed` bullet.

## §1.4 Monotonic Shrink Invariant

Day 14 executor.py must not grow net — the helper method adds LOC but replaces
scattered booleans with one-line `_maybe_transition` calls, so net delta is
expected in the +15 / −5 range. Acceptable because Day 14 is a migration step
preparing for Day 15 supervisor activation.
