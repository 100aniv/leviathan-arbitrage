# Day 13 Plan — Gamma Calibration Cron

**Path-B v2 Day 13** — Nightly cron fits power-law gamma against SlippageFeedbackCollector
history and writes the calibrated value to engine.json.

## Goal

Day 9 wired `Signal.predicted_slippage_bps` so `SlippageFeedbackCollector.record()` now
receives real predictions instead of constant zero. Day 13 consumes those records to fit
the power-law decay exponent:

```
Impact_decay(t) = Impact_0 * (1 + t/t_0)^(-gamma)
```

The fitted gamma is written atomically to `engine.json slippage.gamma` and
`slippage.gamma_calibrated = true` is set. `CEXOrderbookSlippage` already reads gamma
from config (line 74), so the calibrated value takes effect on the next engine restart
without code changes.

## Acceptance Criteria

- R² > 0.6 gate: fit rejected if R² is too low (insufficient signal quality).
- gamma ∈ [0.2, 1.0] gate: fit rejected if exponent is outside physically reasonable range.
- Sparse data guard: < 100 samples → returns `None` (insufficient), keeps previous gamma.
- Idempotent: safe to re-run. Does not re-write identical values.
- Atomic write: `engine.json.bak` created before any mutation. Write is atomic via
  temp-file rename so a crash mid-write cannot corrupt the config.
- `--dry-run` flag: prints result without writing.
- `--synthetic` flag: generates known-gamma=0.5 data for smoke-testing.
- JSONL fallback: loads from `engine/logs/slippage_feedback/YYYYMMDD.jsonl` when
  TimescaleDB is unavailable (paper/dev mode standard path).

## Prediction-Error Gate (post-48h)

After 48h of Day 9 data accumulation the gate criterion is:

```
mean(|actual_bps - predicted_bps|) < 5 bps   (p95 ≤ 20 bps)
```

This is a monitoring assertion logged by the calibration script; it does not block the fit.

## Files

1. `engine/scripts/calibrate_gamma.py` — new, ~120 LOC.
2. `engine/tests/unit/scripts/test_gamma_calibration.py` — new, 5 tests.
3. `CHANGELOG.md` — `[Unreleased].Added` bullet.

## Rollback

Delete `engine/scripts/calibrate_gamma.py`. Restore `engine.json` from `engine.json.bak`.
No source-code changes required — the script is a standalone cron job.

## Risk: LOW

- No changes to engine runtime path.
- Script runs out-of-process as a nightly cron.
- Failure gate keeps previous gamma unchanged.
- scipy unavailable → numpy linalg + grid search fallback (no external deps beyond numpy).
