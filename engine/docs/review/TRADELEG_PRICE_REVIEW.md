# Code Review: TradeLeg price fix + PaperExecutor slippage fix

**Reviewer**: code-reviewer (opus)
**Date**: 2026-03-09
**Verdict**: APPROVE

---

## Code Review Summary

**Files Reviewed:** 9
**Total Issues:** 2

### By Severity
- CRITICAL: 0 (must fix)
- HIGH: 0 (should fix)
- MEDIUM: 1 (consider fixing)
- LOW: 1 (optional)

---

## Stage 1: Spec Compliance

### Problem Statement

1. **TradeLeg.price was None**: All strategy `on_signal()` methods created `TradeLeg` without setting `price`. In `shadow.py:932`, the fallback `leg.price or Decimal("0")` produced `price=0` Orders. `PaperExecutor.execute()` at line 122 checks `if base_price > 0` -- when price=0, `fill_price=Decimal("0")`, causing PnL=0 for all trades.

2. **Double slippage**: `PaperExecutor` was initialized with `PowerLawSlippage(k=1.0, gamma=0.5)`, adding ~10bps/side slippage on top of the `CEXOrderbookSlippage` already applied in `SignalGenerator`. This double-counted friction costs.

### Verification

| Requirement | Status | Evidence |
|---|---|---|
| All 7 strategies + cex_dex set TradeLeg.price | PASS | Every `TradeLeg(` constructor in diff now includes `price=` |
| Triangular (not in diff) already had price | PASS | `triangular.py:122` already had `price=Decimal(str(price_str))` |
| PaperExecutor slippage k=1.0 -> k=0.0 | PASS | `shadow.py:201` now `PowerLawSlippage(k=0.0, gamma=0.5)` |
| Shadow 10min validation | PASS | 132 trades, 100% WR, +$34.97, 0 crashes |
| Config params lowered for more signal generation | PASS | cross_exchange 29.9->5 bps, futures_futures 49.9->5 bps |
| No regressions in tests | PASS | 83/83 strategy unit tests passing |

**Stage 1 Result: PASS** -- All stated requirements implemented correctly.

---

## Stage 2: Code Quality

### LSP Diagnostics

| File | Errors | Warnings |
|---|---|---|
| `src/modes/shadow.py` | 0 | 0 |
| `src/strategies/cross_exchange.py` | 0 | 0 |
| `src/strategies/futures_futures.py` | 0 | 0 |
| `src/strategies/funding_rate.py` | 0 | 0 |
| `src/strategies/spot_futures.py` | 0 | 0 |
| `src/strategies/latency_arb.py` | 0 | 0 |
| `src/strategies/statistical_arb.py` | 0 | 0 |
| `src/strategies/cex_dex.py` | 0 | 0 |
| `config/strategy_params.json` | 0 | 0 |

### Security Review

- No hardcoded secrets, API keys, or credentials introduced.
- No new external inputs or injection surfaces.
- Config parameter changes are safe (min_spread_bps threshold reduction).

### Statistical Arb Exit Price Mapping -- Deep Verification

The statistical_arb strategy has 3 distinct TradeLeg creation sites. The price mapping must be verified against the Signal model semantics:

- `signal.buy_exchange`: exchange where asset is cheaper
- `signal.sell_exchange`: exchange where asset is more expensive
- `signal.buy_price`: current price at `buy_exchange`
- `signal.sell_price`: current price at `sell_exchange`

**Entry (line 430-448)**: zscore > 0 => SHORT spread, zscore < 0 => LONG spread.
Local variables `buy_exchange`, `sell_exchange`, `buy_price`, `sell_price` are swapped for LONG. The TradeLeg uses these locals correctly:
- BUY leg at `buy_exchange` with `price=buy_price` -- CORRECT
- SELL leg at `sell_exchange` with `price=sell_price` -- CORRECT

**Exit SHORT (line 288-306)**: "Close by reversing: sell on buy_exchange, buy on sell_exchange"
- SELL leg at `signal.buy_exchange` with `price=signal.buy_price` -- CORRECT (selling at that exchange's current price)
- BUY leg at `signal.sell_exchange` with `price=signal.sell_price` -- CORRECT (buying at that exchange's current price)

**Exit LONG (line 327-345)**: "Close by reversing: buy on buy_exchange, sell on sell_exchange"
- SELL leg at `signal.sell_exchange` with `price=signal.sell_price` -- CORRECT
- BUY leg at `signal.buy_exchange` with `price=signal.buy_price` -- CORRECT

All 3 sites correctly map: **each leg's price = the current market price at that leg's exchange**. The price field represents "what price to execute at on this exchange", not "which side of the arb am I on".

### PowerLawSlippage k=0.0 Behavior Verification

With `k=0.0`:
```
impact = Decimal("0.0") * Decimal(str(float(size) ** 0.5)) = 0
slippage = base_slippage_pct * 0 * random_factor = 0
fill_price = base_price * (1 + 0) = base_price
```

Result: PaperExecutor returns exact price with zero slippage. This is the correct behavior since `CEXOrderbookSlippage` in `SignalGenerator` is the sole source of slippage modeling.

Note: `base_slippage_pct` in the parent `SlippageModel.__init__` is still set to `Decimal("0.001")` but it gets multiplied by zero impact, so it has no effect. This is clean.

### Config Parameter Changes

- `cross_exchange.min_spread_bps`: 29.9 -> 5
- `futures_futures.min_spread_bps`: 49.9 -> 5

These are thresholds for signal filtering, not risk parameters. Lowering them allows more signals to pass through, which is consistent with:
1. The slippage fix making previously-filtered signals now profitable
2. The `MIN_EDGE_BPS=5` setting already validated in Phase 7.3k (3110 trades, 100% WR, +$21.10)

Both strategies remain in `"status": "MONITOR"` mode, so no live execution risk.

---

## Issues

### [MEDIUM] Defensive: shadow.py still has `leg.price or Decimal("0")` fallback
**File**: `src/modes/shadow.py:932`
**Issue**: The fallback `leg.price or Decimal("0")` silently produces zero-price orders if any future strategy forgets to set `price`. With `k=0.0`, `PaperExecutor` will return `fill_price=0`, producing silent PnL=0 trades that are hard to debug.
**Fix**: Add a warning log when `leg.price` is None or zero:
```python
price = leg.price or Decimal("0")
if price <= 0:
    logger.warning("shadow_mode.leg_missing_price",
                   exchange=leg.exchange_id, symbol=leg.symbol, side=leg.side)
order = Order(..., price=price, ...)
```
Alternatively, raise a `ValueError` to fail fast and prevent silent data corruption.

### [LOW] Comment in shadow.py still references old k=1.0 behavior
**File**: `src/modes/shadow.py:198`
**Issue**: The comment block at line 198 starts with "k=1.0 matches CEXOrderbookSlippage's default (~10bps/side = 20bps round-trip)" but the code now uses `k=0.0`. The first line of the comment block describes the old behavior and could confuse future readers.
**Fix**: Remove or rewrite line 198 to reflect the current k=0.0 decision. Lines 199-201 already explain the rationale correctly.

---

## Recommendation

**APPROVE**

The changes are correct, well-targeted, and solve both root causes (missing price + double slippage). The statistical_arb exit price mapping has been verified across all 3 TradeLeg creation sites and is semantically correct. The config threshold changes are consistent with the validated MIN_EDGE_BPS=5 setting.

The two issues found are both non-blocking:
- MEDIUM: defensive logging for future-proofing (no current impact since all strategies now set price)
- LOW: stale comment (cosmetic only)

No CRITICAL or HIGH severity issues found. LSP diagnostics clean across all 9 files. 83/83 unit tests passing.
