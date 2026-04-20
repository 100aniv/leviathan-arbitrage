# Day 7 Plan — OrderStateMachine (explicit order lifecycle)

**Path-B v2 Day 7** — Explicit order-state enum + transition map + journal emission on every state change.

## Goal

Ship an additive, opt-in `OrderStateMachine` (`src/execution/order_state.py`) that enforces a 9-state order lifecycle with a declarative legal-transition map. Every legal transition emits a hash-chained `ExecutionEvent` through the Day 6 `ExecutionJournal`. Illegal transitions raise `TransitionError` (never silently dropped). No behavioural change until `EXECUTION_STATE_MACHINE_ENABLED=true`; this flag additionally requires `EXECUTION_JOURNAL_ENABLED=true` per §22.3 Flag Interaction Matrix.

## Acceptance Criteria

1. `OrderState` str-enum with 9 members: `PENDING`, `SENT`, `ACKED`, `PARTIAL`, `FILLED`, `CANCELLED`, `REJECTED`, `ROLLED_BACK`, `STRANDED`.
2. Declarative `_LEGAL_TRANSITIONS` mapping — `FILLED`, `CANCELLED`, `REJECTED`, `ROLLED_BACK`, `STRANDED` are terminal (empty sets). `PARTIAL` → {`FILLED`, `CANCELLED`, `STRANDED`} only.
3. `TransitionError` raised when caller requests an illegal transition (e.g. `FILLED` → `SENT`).
4. Every legal transition emits exactly one `ExecutionEvent` via `journal.append(order_id, to_state.value, payload)`. Event `state` == `to_state.value`.
5. Flag dependency guard — instantiating `OrderStateMachine` with `EXECUTION_STATE_MACHINE_ENABLED=true` while `EXECUTION_JOURNAL_ENABLED=false` raises `ConfigError` (§22.3 matrix).
6. Flag OFF (`EXECUTION_STATE_MACHINE_ENABLED=false`) → `transition()` returns `None`, no journal writes, no exceptions for illegal transitions (pure no-op). `current_state()` returns `None`.
7. `current_state(order_id)` returns the `OrderState` of the latest journal event for `order_id`, or `None` if no events exist.
8. STRANDED terminal entry points reuse `StrandedPositionTracker` wiring — when caller invokes `transition(..., OrderState.STRANDED, payload)` the payload is expected to carry `{exchange, symbol, side, size, value_usd, reason}` so downstream hooks (Day 14) can forward to the tracker. No direct tracker coupling in Day 7 (avoid circular import).
9. Full unit regression: `pytest engine/tests/unit/ -x --no-cov` GREEN. Baseline 4949 → 4956+, 0 new failures.

## Files Changed

1. `src/execution/order_state.py` (new, ~180 LOC) — `OrderState` enum, `_LEGAL_TRANSITIONS` map, `TransitionError`, `ConfigError`, `OrderStateMachine`.
2. `tests/unit/execution/test_order_state.py` (new, ~5 tests) — legal path, illegal rejection, journal emission per transition, flag-off no-op, STRANDED terminal.
3. `tests/unit/execution/test_order_state_replay_convergence.py` (new, 2 tests) — Codex/Gemini consensus ask: deterministic replay across consumers, concurrent `order_id` transitions committed in journal order.
4. `CHANGELOG.md` — `[Unreleased].Added` — Day 7 entry.
5. (no change) `.env.example` already declares `EXECUTION_STATE_MACHINE_ENABLED=false` (line 154, dependency on JOURNAL documented line 150).

Estimated net ~400 LOC (impl + tests). `live.py` (3,252) / `main.py` (4,221) / `executor.py` / `atomic.py` **unchanged** (monotonic shrink invariant).

## Design Notes

- **String enum** — `class OrderState(str, Enum)` so journal persists `"PENDING"` (human-readable) not `0`. Matches Day 6 `state: TEXT` column.
- **Terminal detection** — `_LEGAL_TRANSITIONS[state] == set()` is the single source of truth for terminality. A `is_terminal(state) -> bool` helper returns `not _LEGAL_TRANSITIONS[state]`.
- **Transition API** — `async def transition(order_id, from_state, to_state, payload) -> ExecutionEvent | None`. Explicit `from_state` prevents reading-stale-state races; caller (Day 14 executor integration) reads current state once and passes it in.
- **`current_state()` cost** — O(events_for_order) because it calls `journal.replay(order_id=)`. Fine for Day 7 (executor migrates Day 14 with in-memory cache). Documented as "diagnostic / test helper only".
- **Flag dependency guard** — check at `__init__` time (fail fast). A `ConfigError` exception class lives in the same module to avoid pulling in heavy config modules.
- **STRANDED entry** — Day 7 intentionally does NOT inject `StrandedPositionTracker`. The state is emitted into the journal; Day 14 executor wiring forwards the payload to the tracker. This keeps the state machine a pure lifecycle engine.
- **Journal dependency** — constructor takes an `ExecutionJournal` instance (not the module singleton). Tests construct both with a `tmp_path` DB. Day 14 wires the singleton.
- **Flag-off ergonomics** — illegal transitions while flag-off are no-ops (not errors). This matches the Day 6 pattern (flag-off append is sentinel, not exception) and keeps rollout risk-free if flag accidentally flips off mid-call.

## Rollback

`EXECUTION_STATE_MACHINE_ENABLED=false` (default) → every `transition()` returns `None`, no journal writes. Delete `engine/.omc/state/execution_journal.db` only if Day 6 experimental DB was created. No migrations, no state, no wiring changes to legacy executor.

## Success evidence

- `pytest tests/unit/execution/test_order_state.py tests/unit/execution/test_order_state_replay_convergence.py -x --no-cov` → all green.
- Regression: `pytest tests/unit/ -x --no-cov -q` → baseline 4949 → 4949 + 7 = 4956+ passed, 0 new failures.
- Flag interaction smoke:
  ```
  EXECUTION_STATE_MACHINE_ENABLED=true EXECUTION_JOURNAL_ENABLED=false \
    python -c "from src.execution.order_state import OrderStateMachine, ConfigError; \
               from src.execution.journal import ExecutionJournal; \
               from pathlib import Path; \
               import tempfile; \
               try: OrderStateMachine(ExecutionJournal(Path(tempfile.mkstemp()[1]))); \
               except ConfigError: print('OK dependency enforced')"
  ```
- Imports smoke: `python -c "from src.execution.order_state import OrderState, OrderStateMachine, TransitionError, ConfigError"`.
- `wc -l engine/src/modes/live.py engine/src/main.py` unchanged vs pre-Day-7 (3252 + 4221 = 7473).

## Day 7 does NOT

- Modify `live.py`, `main.py`, `executor.py`, `atomic.py` (Day 14 migrates).
- Wire `StrandedPositionTracker` directly (Day 14 wiring).
- Implement replay→state-cache (deferred to Day 14 for in-executor use).
- Surface state in REST / Grafana (W3 dashboard).
