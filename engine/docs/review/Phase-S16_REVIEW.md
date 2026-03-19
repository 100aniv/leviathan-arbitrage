# Phase S16 Code Review

**Reviewer**: Direct (Lead)
**Date**: 2026-03-20
**Scope**: 12 US (US-248, 250, 253, 256, 258-b, 259-a, 260, 261, 262, 263, 264, 265)
**Files**: 11 modified/created

## Summary

CRITICAL: 0 | HIGH: 0 | MEDIUM: 2 | LOW: 3

All S16 changes are backward-compatible, well-integrated, and math-correct. No regressions (4962 tests pass). Shadow 11min: +$379 PnL, 0 crashes.

## Assembly Verification: PASS (4/4)

- Init Chain: AdaptiveThreshold in 3 strategies, PositionRecovery/Reconciler in main.py, peak_equity in shadow.py
- Signal Flow: on_signal → update() → threshold check in cross/futures/spot strategies, z-score in funding_rate
- Dead Wiring: 0 dead classes (all imported, instantiated, consumed)
- Config Audit: 3 env vars (CROSS_EXCHANGE_MIN_BOOK_DEPTH_USD, FUTURES_MIN_BOOK_DEPTH_USD, FUNDING_ZSCORE_THRESHOLD) properly wired

## Findings

### MEDIUM-1: AdaptiveThreshold deque double initialization
**File**: src/core/adaptive_threshold.py:45,49
**Issue**: `_observations` created by dataclass default_factory (maxlen=1440) then overwritten in `__post_init__` (maxlen=self.window). Wasteful but functionally correct since __post_init__ always runs.
**Impact**: Negligible (one extra deque allocation per strategy).
**Recommendation**: Remove default_factory, use `field(default=None, repr=False)` and create only in __post_init__.

### MEDIUM-2: HMMRegimeDetector.detect() missing (PRE-EXISTING)
**File**: shadow.py regime_check_error log
**Issue**: shadow.py calls regime_detector.detect() but HMMRegimeDetector only has predict(). Logs warning every ~60s.
**Impact**: Regime detection falls back to default (MEDIUM). Non-blocking.
**Note**: Pre-existing issue, not introduced by S16. Fix planned for S17+.

### LOW-1: Funding z-score cold start
**File**: src/strategies/funding_rate.py:137
**Issue**: Z-score filter only activates after 30 samples (~40min at 80s intervals). Early signals bypass z-score check.
**Impact**: Acceptable — early signals still pass static min_funding_diff_bps check.

### LOW-2: Volatility baseline set once
**File**: src/core/adaptive_threshold.py:81-83
**Issue**: vol_baseline set on first calculation and never updated. In very long runs, baseline could become stale.
**Impact**: Minimal for shadow (11min runs). For 24H+ runs, baseline recalibration could improve accuracy.

### LOW-3: guardian.py check #9 behavioral change
**File**: src/risk/guardian.py:330
**Issue**: Changed from log-only to actual size scaling. This is intentional (US-264) but is a behavioral change.
**Impact**: Correct — correlation-scaled positions reduce correlated risk. Prometheus counter tracks occurrences.

## Shadow Results Cross-Reference

| Metric | Value | Check |
|--------|-------|-------|
| Runtime | 11.0min | PASS (>= 10min) |
| Crashes | 0 | PASS |
| PnL | +$379.44 | PASS |
| Trades | 14 (stat_arb) | PARTIAL (1/6 strategies, market conditions) |
| Signals | 12,171 | PASS |
| dynamic_sigma | 114K+ logs | US-248 active |
| peak_equity DB | Loaded successfully | US-256 fixed |

## Test Coverage

- 22 new tests (14 adaptive_threshold + 8 regime_param_matrix)
- Full regression: 4962 passed, 0 failed, 12 skipped
- Coverage: 82%

## Verdict

**PASS** — No CRITICAL/HIGH issues. 2 MEDIUM issues are non-blocking (deque double-init is cosmetic, HMM detect() is pre-existing). All S16 code changes are correctly integrated and functional.
