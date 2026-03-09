# US-058 Code Review: Shadow Partial Fill (5%) + Order Rejection (2%)

**Reviewer**: code-reviewer (opus)
**Date**: 2026-03-09
**Files Reviewed**: 2
**Total Issues**: 5

---

## Stage 1: Spec Compliance

### Requirements Checklist

| Requirement | Status | Evidence |
|---|---|---|
| PaperExecutor partial_fill_rate=5% activated in ShadowMode | PASS | `shadow.py:209` — `Decimal(os.environ.get("SHADOW_PARTIAL_FILL_RATE", "0.05"))` |
| PaperExecutor rejection_rate=2% activated in ShadowMode | PASS | `shadow.py:210` — `Decimal(os.environ.get("SHADOW_REJECTION_RATE", "0.02"))` |
| ShadowStats counters for rejection/partial_fill | PASS | `shadow.py:132-133` — `trades_rejected`, `trades_partial_fill` fields added |
| StrategyStats counters for rejection/partial_fill | PASS | `shadow.py:115-116` — `rejections`, `partial_fills` fields added |
| OrderRejectedError handled before generic Exception | PASS | `shadow.py:758` and `shadow.py:980` — both `except OrderRejectedError` precede `except Exception` |
| Sell amount = buy fill amount on partial fill | PASS | `shadow.py:754` — `amount=buy_trade.amount` |
| _send_summary includes new counters | PASS | `shadow.py:1229-1230` — added to `summary_data` dict |
| Telegram daily summary includes rejection/partial stats | PASS | `shadow.py:1257-1260` — appended to strategy breakdown message |
| Env var overrides for rates | PASS | Tests at lines 109-123 confirm env var overrides work |
| 12 new tests covering all paths | PASS | All 12 tests pass (verified via pytest) |

**Stage 1 Verdict**: PASS — All spec requirements are implemented.

---

## Stage 2: Code Quality

### LSP Diagnostics

| File | Errors | Warnings |
|---|---|---|
| `engine/src/modes/shadow.py` | 0 | 0 |
| `engine/tests/unit/test_shadow_partial_fill_rejection.py` | 0 | 0 |
| `engine/src/execution/paper.py` | 0 | 0 |

### Regression Tests

- **Existing tests**: 52 tests in `test_shadow_mode.py` — all PASS (no regressions)
- **New tests**: 12 tests in `test_shadow_partial_fill_rejection.py` — all PASS

### Security Check

- No hardcoded secrets, API keys, or credentials found.
- No SQL injection vectors.
- No XSS vectors.

---

## Issues

### [MEDIUM] Missing input validation on Decimal env var parsing

**File**: `engine/src/modes/shadow.py:209-210`

```python
partial_fill_rate=Decimal(os.environ.get("SHADOW_PARTIAL_FILL_RATE", "0.05")),
rejection_rate=Decimal(os.environ.get("SHADOW_REJECTION_RATE", "0.02")),
```

**Issue**: If a user sets `SHADOW_PARTIAL_FILL_RATE=abc` or `SHADOW_REJECTION_RATE=""`, `Decimal()` raises `decimal.InvalidOperation` which will crash ShadowMode initialization. Additionally, values outside range [0.0, 1.0] (e.g., `-1` or `1.5`) are accepted without validation, which would cause nonsensical behavior (negative rejection rate, or >100% rejection).

**Fix**: Wrap in try/except with fallback and clamp to valid range:

```python
def _parse_rate(env_key: str, default: str) -> Decimal:
    try:
        val = Decimal(os.environ.get(env_key, default))
        return max(Decimal("0"), min(Decimal("1"), val))
    except Exception:
        return Decimal(default)

partial_fill_rate=_parse_rate("SHADOW_PARTIAL_FILL_RATE", "0.05"),
rejection_rate=_parse_rate("SHADOW_REJECTION_RATE", "0.02"),
```

---

### [MEDIUM] Buy-succeeds-then-sell-rejected leaves orphaned position untracked

**File**: `engine/src/modes/shadow.py:736-770`

**Issue**: In `_execute_shadow_trade`, the buy order executes at line 736, and partial fill is detected at lines 739-744. Then the sell order is submitted at line 756. If the sell order is rejected (raises `OrderRejectedError`), the catch block at line 758 increments `trades_rejected` and returns early, but the successfully executed buy trade is silently discarded — no PnL is recorded, `trades_executed` is not incremented, and there is no log entry indicating a one-legged position. In real trading this would represent a stuck open position.

For shadow simulation realism, this is acceptable behavior (the trade pair is simply abandoned), but a warning log would aid debugging.

**Fix**: Add a log entry in the `OrderRejectedError` handler distinguishing buy-rejection vs sell-rejection:

```python
except OrderRejectedError as exc:
    sid = signal.strategy_id or self.STRATEGY_ID
    self._stats.trades_rejected += 1
    if sid not in self._stats.by_strategy:
        self._stats.by_strategy[sid] = StrategyStats()
    self._stats.by_strategy[sid].rejections += 1
    # Distinguish which leg was rejected
    leg_info = "buy" if not buy_trade else "sell"  # buy_trade only defined after buy succeeds
    logger.warning(
        "shadow_mode.order_rejected",
        strategy=sid,
        symbol=signal.symbol,
        rejected_leg=leg_info,
        error=str(exc),
    )
    return
```

Note: This requires moving `buy_trade` initialization to a sentinel value before the try block (e.g., `buy_trade = None`), then checking `buy_trade is None` in the except. The current code relies on `buy_trade` being in scope from the try block, which is valid Python but fragile.

---

### [MEDIUM] `_execute_shadow_trade_request` partial fill counts per-leg, not per-trade

**File**: `engine/src/modes/shadow.py:973-978`

**Issue**: In the multi-leg `_execute_shadow_trade_request`, partial fill detection is inside the `for leg` loop (line 974). If a 3-leg trade has 2 legs partially filled, `trades_partial_fill` is incremented by 2, not 1. This inflates the partial fill counter since each trade request should count as at most 1 partial fill event.

**Fix**: Track partial fill once per trade request, not per leg:

```python
trades = []
had_partial = False
for leg in trade_request.legs:
    # ... order creation ...
    trade = await self._paper_executor.execute(order)
    if trade.amount < leg.size:
        had_partial = True
    trades.append((leg, trade))

if had_partial:
    self._stats.trades_partial_fill += 1
    if sid not in self._stats.by_strategy:
        self._stats.by_strategy[sid] = StrategyStats()
    self._stats.by_strategy[sid].partial_fills += 1
```

---

### [LOW] Per-strategy breakdown in summary_data omits rejections/partial_fills

**File**: `engine/src/modes/shadow.py:1213-1220`

**Issue**: The `strategy_breakdown` list (used in `summary_data["by_strategy"]`) does not include `rejections` or `partial_fills` per strategy. The counters exist in `StrategyStats` (lines 115-116) and are incremented correctly, but are not serialized into the summary dict. Only the aggregate counts (`stats.trades_rejected`, `stats.trades_partial_fill`) are included at the top level (lines 1229-1230). The Telegram alert does show the aggregate at the bottom (lines 1257-1260), but consumers of `summary_data` cannot get per-strategy breakdowns.

**Fix**: Add the fields to the strategy breakdown:

```python
strategy_breakdown.append({
    "strategy_id": s_id,
    "trades": ss.trades,
    "wins": ss.wins,
    "losses": ss.losses,
    "win_rate": s_wr,
    "pnl": ss.pnl,
    "rejections": ss.rejections,
    "partial_fills": ss.partial_fills,
})
```

---

### [LOW] Missing test coverage for buy-success + sell-rejection scenario

**File**: `engine/tests/unit/test_shadow_partial_fill_rejection.py`

**Issue**: The 12 tests cover: (1) default rates, (2) env var overrides, (3) buy-rejection stats, (4) partial fill detection, (5) sell amount matching, (6) trade request rejection, (7) summary data inclusion. However, no test covers the edge case where the buy leg succeeds but the sell leg raises `OrderRejectedError`. This is the most dangerous real-world scenario (one-legged execution) and should be verified.

**Fix**: Add a test where `execute` returns a Trade on first call (buy), then raises `OrderRejectedError` on second call (sell):

```python
@pytest.mark.asyncio
async def test_buy_success_sell_rejection(self) -> None:
    """Buy succeeds, sell rejected -> trades_rejected=1, trades_executed=0."""
    buy_trade = make_trade(side=OrderSide.BUY)
    mock_executor = MagicMock(spec=PaperExecutor)
    mock_executor.execute = AsyncMock(
        side_effect=[buy_trade, OrderRejectedError("sell rejected")]
    )
    shadow = make_shadow_mode(paper_executor=mock_executor)

    await shadow._execute_shadow_trade(make_signal())

    assert shadow._stats.trades_rejected == 1
    assert shadow._stats.trades_executed == 0
```

---

## By Severity

| Severity | Count | Summary |
|---|---|---|
| CRITICAL | 0 | - |
| HIGH | 0 | - |
| MEDIUM | 3 | Env var validation, orphaned position logging, per-leg partial fill inflation |
| LOW | 2 | Summary breakdown missing per-strategy counters, missing edge case test |

---

## Recommendation

**APPROVE** (with suggested improvements)

The implementation correctly fulfills all US-058 requirements. The core logic is sound:
- `OrderRejectedError` is caught before the generic `Exception` in both execution paths
- Sell amount correctly tracks buy fill amount for partial fills
- Stats are properly incremented at both global and per-strategy levels
- Summary data and Telegram alerts include the new counters
- All 12 new tests pass; all 52 existing shadow mode tests pass with no regressions
- No security issues, no type errors, no hardcoded secrets

The 3 MEDIUM issues are non-blocking but should be addressed in a follow-up:
1. **Env var validation** prevents a crash on misconfigured deployment
2. **Orphaned position logging** aids debugging when buy succeeds but sell is rejected
3. **Per-leg counting** inflates `trades_partial_fill` for multi-leg strategies (funding_rate, triangular, etc.)

The 2 LOW issues are quality improvements that would strengthen observability and test coverage.
