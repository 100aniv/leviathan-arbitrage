# Day 15 Plan — Activate TradingSupervisor as main.py runloop owner

**Path-B v2 Day 15** — Wire the Day-4 `src/core/supervisor.py:TradingSupervisor`
(498 LOC, 12 tests already green) as the lifecycle owner of `Engine.run()`.
Behind new feature flag `SUPERVISOR_ACTIVE` (default `false`). The legacy
runloop is preserved unchanged when the flag is off; when on, `Engine.run()`
delegates the wait-for-shutdown + graceful-stop phase to the supervisor and
forwards `StrandedPositionTracker` halt events as `supervisor.halt_request()`.

## Goal

1. Flag ON (`SUPERVISOR_ACTIVE=true`)
   - `Engine.run()` routes its post-init "block on shutdown" phase through
     `self._supervisor.start() → await _shutdown_event.wait() → self._supervisor.stop()`.
   - When `StrandedPositionTracker` returns `should_halt=True` inside the
     executor, the `halt_local()` call is mirrored by an additional
     `supervisor.halt_request()` fire-and-forget task that sets the supervisor
     shutdown event so the `main.py` runloop unwinds cleanly.
2. Flag OFF (default)
   - `Engine.run()` body is byte-identical to the pre-Day-15 baseline.
   - No supervisor instance is constructed. `StrandedPositionTracker.register()`
     keeps returning the same `bool` — the executor's existing
     `if should_halt: halt_local()` path is unchanged.
3. Rollback: flip `SUPERVISOR_ACTIVE=false`. Zero behavioural delta.

## Acceptance Criteria

- Flag ON — `Engine.run()`:
  - Constructs a `TradingSupervisor` against `self._settings`.
  - Calls `supervisor.start()` after the existing `_init_*` boot sequence.
  - Awaits `self._shutdown_event.wait()` as before.
  - Calls `supervisor.stop()` in `finally` before the existing `self.stop()`.
- Flag ON — journal STRANDED event path:
  - A new `Engine._on_stranded_halt()` helper creates an `asyncio.Task` that
    invokes `self._supervisor.halt_request()` so the supervisor's shutdown
    event is set and its `stop()` sequence kicks in.
  - `StrandedPositionTracker` is plumbed to call this helper whenever
    `register()` would return `True`, **additive** to the existing
    `halt_local()` path (never replacing it).
- Flag OFF — byte-identical `Engine.run()` code path. No `TradingSupervisor`
  import, no instance, no additional tasks.
- Regression: `pytest --co -q` collects 5798 → 5802+ tests. No new failures.
- `src/main.py` line count growth ≤ +10 LOC net (guard-rail).

## Tests (`tests/unit/modes/test_supervisor_halt_on_stranded.py`)

1. **Flag ON + StrandedPositionTracker emits halt** → `Engine._on_stranded_halt`
   schedules `supervisor.halt_request()` and the supervisor's shutdown event
   is set.
2. **Flag OFF + STRANDED event** → legacy path only — no supervisor, no
   `halt_request()` call, `halt_local()` still fires.
3. **supervisor.start() registers registered background tasks** — verify
   `register_background_task("stranded_watch", …)` wiring.
4. **supervisor.stop() cancels tasks within 30 s** — reuse stub from Day 4
   tests to verify `SHUTDOWN_TIMEOUT` path still holds end-to-end.

## Files touched

- `engine/src/main.py` — +~8 LOC runloop branch + `_on_stranded_halt()` helper.
- `engine/src/core/supervisor.py` — +`halt_request()` public handler (~6 LOC)
  that sets the shutdown event and schedules `stop()`.
- `engine/tests/unit/modes/test_supervisor_halt_on_stranded.py` — new, 4 tests.
- `engine/docs/plans/day-15-plan.md` — this file.
- `CHANGELOG.md` — `[Unreleased].Changed` bullet.

## §1.4 Monotonic Shrink Invariant

`main.py` may grow by +10 LOC net in this Day. All growth is feature-flag
gated and will be reclaimed (+ more) during the Day 16+ executor migration
that replaces the legacy `_init_*` sequence with supervisor boot steps.

## Risk: LOW (1d)

Pure additive wiring. All changes behind `SUPERVISOR_ACTIVE=false` default.
