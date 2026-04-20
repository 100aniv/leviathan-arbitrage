# Day 9 Plan — pred_bps wiring fix

**Path-B v2 Day 9** — Enable predicted-vs-actual slippage variance measurement.

## Goal

Fix the `_pred_bps = 0.0` hardcoded bug at `src/modes/live.py:1863,1870` so that
`SlippageFeedbackCollector.record()` receives the actual pre-trade slippage
prediction instead of a constant zero. This unblocks Day 13 gamma calibration.

## Acceptance Criteria

- `Signal.predicted_slippage_bps: Decimal | None = None` field added
  (backward-compatible default).
- `TradeRequest.signal: Signal | None = None` field added so live execution
  can read the originating signal's prediction.
- `src/modes/live.py:1863,1870` replaces `_pred_bps = 0.0` with a safe read
  from `trade_request.signal.predicted_slippage_bps` (fallback `0.0`).
- New test `tests/unit/modes/test_slippage_feedback_wired.py` passes with 3
  cases (prediction present, prediction None, collector kwargs verification).
- Full unit regression: `pytest tests/unit/ -x --tb=line --no-cov` green with
  0 new failures vs current baseline (expect baseline+3).
- `live.py` LOC delta ≤ ±5 (plan §1.4 monotonic shrink invariant).

## Files Changed

1. `src/core/models.py` — add `predicted_slippage_bps` to `Signal`.
2. `src/strategies/base.py` — add `signal: Signal | None` to `TradeRequest`.
3. `src/modes/live.py` — replace hardcoded `_pred_bps` at lines 1863, 1870.
4. `tests/unit/modes/test_slippage_feedback_wired.py` — new test file.

Estimated ~30 LOC net.

## Rollback

Revert the 3 source-file diffs; feedback collector reverts to silent
`(0.0, actual_bps)` recording. No schema/migration changes required.

## Notes

- Strategies do NOT yet populate `TradeRequest.signal` — that wiring arrives
  incrementally as each strategy opts in. Day 9 lands the plumbing so the
  `_pred_bps` read returns the real value when the plumbing is enabled
  upstream, and returns `0.0` (current behaviour) otherwise.
- Day 13 will calibrate `gamma` by analysing the recorded
  `(predicted, actual)` distribution.
- Prometheus export of `SLIPPAGE_PREDICTION_ERROR_BPS` is not in scope here;
  flagged for Day 10 follow-up.
