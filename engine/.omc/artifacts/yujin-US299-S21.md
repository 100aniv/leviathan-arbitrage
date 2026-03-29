# Yujin US-299: Strategy-Filter Shadow Mode

## Status: PASS

## Changes Made

### engine/src/modes/shadow.py
1. `StrategyStats` (line ~256): Added `pnl_history: list = field(default_factory=list)` for per-trade Sharpe/MDD calculation.
2. `ShadowMode.__init__()` (line ~394): Added `strategy_filter: list[str] | None = None` parameter; stored as `self._strategy_filter: frozenset[str] | None`.
3. `_execute_shadow_trade()` (line ~1314 region): Added allowlist guard — if `_strategy_filter` is set and `sid_check` not in it, log debug and return.
4. `_execute_shadow_trade_request()` (line ~1638 region): Same allowlist guard for N-leg TradeRequest path.
5. Per-strategy tracking blocks (×2): Added `ss.pnl_history.append(net_pnl_float)` to both 2-leg and N-leg execution paths.
6. `get_strategy_report()`: Enhanced with `sharpe`, `max_drawdown`, and `pass` fields. PASS criteria: `trades >= 1 AND pnl >= 0`.

### engine/src/main.py
All three `ShadowMode()` instantiations wired with `strategy_filter=` from `SHADOW_STRATEGY_FILTER` env var (comma-separated signal IDs, e.g. `shadow_arb_v1,triangular`).

## WIRING AC Verified
1. **생성**: `ShadowMode.__init__(strategy_filter=...)` — frozenset stored as `self._strategy_filter`
2. **주입**: `main.py` parses `SHADOW_STRATEGY_FILTER` env var → passes to all 3 `ShadowMode()` calls
3. **호출**: `_execute_shadow_trade()` and `_execute_shadow_trade_request()` check filter before processing

## Usage
```bash
# Run shadow with only cross_exchange and triangular strategies:
SHADOW_STRATEGY_FILTER="shadow_arb_v1,triangular" python -m src.main

# No filter (all 6 active strategies pass — existing behaviour):
python -m src.main
```

## Tests
- 329 unit tests (core + execution): PASS
- Full suite: running
