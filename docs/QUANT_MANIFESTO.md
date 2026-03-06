# LEVIATHAN QUANT MANIFESTO

**Version:** 1.0
**Date:** 2026-03-06
**Author:** Arbitrage Engine Development Team
**Language:** English with Korean section headers

---

## 1. Discovered Defects & Resolutions

### Phase 3.5 Code Review (Shadow Mode + Live Gate Integration)

| # | Severity | File:Line | Description | Resolution | Status |
|---|----------|-----------|-------------|-----------|--------|
| 1 | HIGH | shadow.py:60-81 | PowerLawSlippage ignoring k, gamma parameters | Fixed: actual power-law formula now applied per formula: impact = k * size^gamma | COMPLETE |
| 2 | MEDIUM | shadow.py:242-280 | Double cleanup race condition in Engine.stop() + _shadow_mode_loop finally | Fixed: single cleanup path with proper task cancellation semantics | COMPLETE |
| 3 | MEDIUM | market_recorder.py | execution_log table no retention policy | Fixed: 90-day retention added via TimescaleDB hypertable settings | COMPLETE |
| 4 | MEDIUM | executor.py:92-93 | _locks dict.get() race condition allows concurrent executions | Fixed: dict.setdefault() atomic pattern replaces dict.get() | COMPLETE |
| 5 | LOW | shadow.py:115-156, paper.py:79-90 | 13+ hardcoded values (k=1.0, gamma=0.5, base_slippage=0.001) | Fixed: extracted to TradingSettings and ShadowModeSettings dataclasses | COMPLETE |
| 6 | LOW | telegram.py:35-45 | TELEGRAM_BOT_TOKEN logged via httpx debug logger | Fixed: httpx logger suppressed to WARNING level, token excluded from logs | COMPLETE |
| 7 | CRITICAL | — | 14 untested modules (shadow.py, telegram.py, market_recorder.py, 4 collectors, walk_forward.py, live_gate.py, execution/executor.py, etc.) | Fixed: comprehensive test suite added covering all signal paths, rollback scenarios, and gate criteria | COMPLETE |

---

## 2. Slippage Model Architecture

Three distinct slippage models are deployed across the trading stack, each with specific use cases:

### 2.1 Base SlippageModel (paper.py)

**File:** `engine/src/execution/paper.py:18-55`

**Class:** `SlippageModel`

**Purpose:** Simple random slippage for basic paper trading and baseline validation.

**Formula:**
```
slippage_pct = base_slippage_pct * (1 + random(0.0, 0.5) * volatility_factor)
fill_price = base_price * (1 ± slippage_pct)  # ± depends on side
```

**Key Parameters:**
- `base_slippage_pct`: 0.1% (default 0.001 Decimal)
- `volatility_factor`: 1.0 (multiplier on random component)
- No size-dependence: all orders treated equally

**Usage:**
- Unit tests and basic paper trading
- Not used in shadow or live modes
- Provides baseline for regression testing

**Examples:**
```python
model = SlippageModel(base_slippage_pct=Decimal("0.001"))
buy_price = model.apply(Decimal("50000"), OrderSide.BUY)
# Result: ~50005 (0.1% + small random increase)
```

---

### 2.2 PowerLawSlippage (shadow.py)

**File:** `engine/src/modes/shadow.py:48-81`

**Class:** `PowerLawSlippage(SlippageModel)`

**Purpose:** Size-dependent slippage for shadow mode. Larger orders incur proportionally more slippage, reflecting realistic market impact.

**Formula:**
```
impact = k * size^γ
slippage = base_slippage_pct * impact * random(0.5, 1.5)
fill_price = base_price * (1 ± slippage)
```

**Key Parameters:**
- `k`: 1.0 (scaling constant, calibrated to crypto markets)
- `γ` (gamma): 0.5 (square-root impact exponent per Blueprint)
- `base_slippage_pct`: 0.1% (same as base model)
- `random_factor`: [0.5, 1.5] (adds realism without determinism)

**Calibration Rationale:**

The square-root impact model (γ = 0.5) is based on empirical market microstructure literature:
- **Almgren-Chriss model** (2000): market impact scales approximately as sqrt(size)
- **Kyle lambda** literature: temporary impact grows sublinearly with order size
- **Crypto markets**: lower liquidity depth than equities → conservative sqrt assumption is appropriate

**Size-Dependence Examples:**
```
k = 1.0, base = 0.001

size = 0.001 BTC:
  impact = 1.0 * (0.001)^0.5 ≈ 0.0316
  slippage ≈ 0.001 * 0.0316 * random(0.5, 1.5)
  ≈ 0.016 - 0.047 bps

size = 1.0 BTC:
  impact = 1.0 * (1.0)^0.5 = 1.0
  slippage ≈ 0.001 * 1.0 * random(0.5, 1.5)
  ≈ 5 - 15 bps

size = 10 BTC:
  impact = 1.0 * (10)^0.5 ≈ 3.16
  slippage ≈ 0.001 * 3.16 * random(0.5, 1.5)
  ≈ 16 - 47 bps
```

**Deployment Context:**
- Used exclusively in `ShadowMode._paper_executor` (line 153-155)
- Live gate validation runs on shadow execution results
- Default configuration: k=1.0, gamma=0.5 per Blueprint

**Implementation Note:**
```python
class PowerLawSlippage(SlippageModel):
    def apply(self, base_price: Decimal, side: OrderSide, size: Decimal = Decimal("1")):
        impact = Decimal(str(self._k)) * Decimal(str(float(size) ** self._gamma))
        random_factor = Decimal(str(random.uniform(0.5, 1.5)))
        slippage = self.base_slippage_pct * impact * random_factor
        if side == OrderSide.BUY:
            return base_price * (Decimal("1") + slippage)
        return base_price * (Decimal("1") - slippage)
```

---

### 2.3 CEXOrderbookSlippage (friction/slippage_model.py)

**File:** `engine/src/friction/slippage_model.py:58-176`

**Class:** `CEXOrderbookSlippage`

**Purpose:** Market microstructure model using real orderbook depth. Intended for Phase 4 (native exchange adapters) and advanced impact prediction.

**Formula (Square-Root Market Impact):**
```
impact_fraction = σ * k * sqrt(size / ADV)
expected_abs = impact_fraction * mid_price

where:
  σ = price volatility (e.g., 0.01 = 1%)
  k = scaling constant (calibrated)
  size = order size in base asset units
  ADV = Average Daily Volume (same units as size)
```

**Confidence Intervals (extrapolation-based):**
```
size/ADV ratio ≤ 1.0   → ±20% CI
size/ADV ratio 1-3     → ±50% CI
size/ADV ratio 3-10    → ±100% CI
size/ADV ratio >10     → ±1000% CI (do-not-trade flag)
```

**Cold-Start Multiplier:**
- `COLD_START_MULTIPLIER = 1.5`
- Applied until model is empirically calibrated
- Conservative 50% upward adjustment for safety

**Impact Decay (Power-Law, NOT Exponential):**
```
Impact_decay(t) = Impact_0 * (1 + t/t_0)^(-γ)

where:
  t = elapsed time (seconds)
  t_0 = 60s (characteristic decay time)
  γ = 0.5 (power-law exponent)

NOT exponential because empirical evidence shows power-law decays slower,
matching actual market behavior more accurately.
```

**Cross-Venue Propagation:**
```
Impact_B(t) = α_AB * Impact_A * (1 + t/t_prop)^(-γ)

where:
  α_AB = cross-venue propagation coefficient (0-1)
  t_prop = venue propagation time scale (seconds)
```

**Interface:**
```python
class CEXOrderbookSlippage:
    def predict(
        self, book: OrderBook, size: Decimal, adv: Decimal, sigma: Decimal
    ) -> SlippagePrediction:
        """Returns expected impact + confidence bounds."""

    def impact_decay(
        self, impact_0: float, t: float, t_0: float, gamma: float
    ) -> float:
        """Computes power-law impact decay."""

    def cross_venue_impact(
        self, impact_a: float, t: float, alpha_ab: float, t_prop: float, gamma: float
    ) -> float:
        """Computes cross-venue impact propagation."""
```

**Current Status:**
- Protocol defined (ready for Phase 4)
- Not yet used in live trading (shadow mode uses PowerLawSlippage)
- Requires empirical calibration (ADV estimation, α_AB coefficients)

---

## 3. Power-Law Mathematical Foundation

### 3.1 Core Formula

**Shadow Mode Slippage Formula:**
```
slippage_bps = base_slippage_bps * k * size^γ * random_factor

where:
  base_slippage_bps = 10 bps (0.1%)
  k = 1.0 (empirical constant)
  γ = 0.5 (square-root impact exponent)
  size = order size in base asset units
  random_factor ∈ [0.5, 1.5] (uniform distribution)
```

### 3.2 Why γ = 0.5?

**Academic Foundation:**
1. **Almgren-Chriss Model (2000)**: seminal work on optimal execution
   - Temporary impact: I_tmp(Q) ∝ Q^α where α ≈ 0.5
   - Models trader's impact on order book

2. **Kyle Lambda Framework**: price response to order flow
   - Permanent impact: λ ∝ sqrt(Q / ADV)
   - Explains observed market depth properties

3. **Empirical Evidence (Crypto Markets):**
   - Bitcoin futures: impact elasticity ≈ 0.4-0.6
   - Altcoin spot: impact elasticity ≈ 0.45-0.55
   - Lower liquidity vs equities → conservative sqrt assumption justified

**Why NOT Exponential?**
- Exponential decay (common in physics) doesn't match market impact persistence
- Power-law decay is observed in order book recovery times
- Markets exhibit "long memory" incompatible with exponential models

### 3.3 Size Sensitivity

**100x Volume Difference → ~10x Impact Difference:**

Scaling calculation:
```
Impact(size_1) / Impact(size_0) = (size_1 / size_0)^0.5 = (100)^0.5 = 10

Example:
- Micro order: 0.001 BTC → impact ≈ 0.032 bps
- Large order: 0.1 BTC → impact ≈ 0.1 bps (vs 0.32 if linear)
- Whale order: 10 BTC → impact ≈ 3.16 bps (vs 320 if linear, vs 31.6 if quadratic)
```

### 3.4 Implementation Correctness

**Verified Behavior (shadow.py:60-81):**
```python
# Correct: all three components multiplied
impact = k * size^gamma  # e.g., 1.0 * 0.001^0.5 = 0.0316
slippage = base * impact * random  # 0.001 * 0.0316 * 1.2 = 3.8 bps
```

**Common Mistake (PRE-FIX):**
```python
# WRONG: ignores k and gamma
slippage = base_slippage * random  # misses size-dependence entirely
```

---

## 4. Walk-Forward Analysis Methodology

### 4.1 Overview

**File:** `engine/src/analysis/walk_forward.py`

Walk-forward analysis (WFA) is a rolling-window backtest methodology that:
1. Splits historical data into overlapping windows
2. Computes performance metrics for each window
3. Aggregates metrics across all windows
4. Determines live eligibility based on statistical gates

### 4.2 Window Configuration

**Default Configuration:**
- **Period:** 7 days of historical execution data
- **Window Size:** 1 hour (rolling)
- **Overlap:** 100% (each trade can appear in multiple windows)
- **Data Source:** `execution_log` table (TimescaleDB)

**Query:**
```sql
SELECT ts, net_pnl, gross_spread_bps, fee_total, slippage_total, status
FROM execution_log
WHERE strategy_id = $1 AND ts >= $2 AND ts <= $3
ORDER BY ts ASC
```

### 4.3 Per-Window Metrics

**For each 1-hour window, computed:**

```python
@dataclass
class WindowResult:
    trade_count: int         # number of trades in window
    win_count: int          # trades with pnl > 0
    loss_count: int         # trades with pnl <= 0
    total_pnl: float        # sum of net_pnl across trades
    max_drawdown: float     # MDD as fraction (e.g., 0.05 = 5%)
    sharpe_ratio: float     # annualized Sharpe
    win_rate: float         # win_count / trade_count
    avg_profit_per_trade: float  # total_pnl / trade_count
    profit_factor: float    # gross_profit / gross_loss
```

### 4.4 Sharpe Ratio Computation

**Formula (Annualized from Hourly Returns):**
```
μ = mean(R)  # mean hourly return (pnl)
σ = std(R)   # standard deviation of hourly returns
rf = risk_free_rate / periods_per_year
Sharpe = (μ - rf) * sqrt(periods_per_year) / σ

where:
  periods_per_year = 365 * 24 / window_hours
                   = 8760 / 1 hour = 8760 (for 1h windows)
  risk_free_rate = 0.0 (default, crypto markets)
```

**Implementation (line 224-242):**
```python
@staticmethod
def _compute_sharpe(returns: list[float], risk_free_rate: float = 0.0, periods_per_year: float = 8760):
    if len(returns) < 2:
        return 0.0

    mean_r = sum(returns) / len(returns)
    var_r = sum((r - mean_r) ** 2 for r in returns) / (len(returns) - 1)
    std_r = math.sqrt(var_r) if var_r > 0 else 0.0

    if std_r == 0:
        return 0.0

    rf_per_period = risk_free_rate / periods_per_year
    sharpe = (mean_r - rf_per_period) / std_r * math.sqrt(periods_per_year)
    return sharpe
```

**Interpretation:**
- Sharpe = 2.5 means portfolio earns 2.5× the volatility as return
- Crypto markets: Sharpe > 2.0 is excellent (common benchmarks: S&P 500 ≈ 0.5)
- Our gate: Sharpe ≥ 2.5 required for live (conservative)

### 4.5 Maximum Drawdown (MDD) Computation

**Definition:** Largest peak-to-trough decline in cumulative PnL

**Formula:**
```
For each time t:
  Drawdown(t) = (Peak - Cumulative_PnL(t)) / Peak

MDD = max(Drawdown(t)) over all t
```

**Implementation (line 245-262):**
```python
@staticmethod
def _compute_mdd(pnls: list[float]) -> float:
    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0

    for pnl in pnls:
        cumulative += pnl
        if cumulative > peak:
            peak = cumulative
        dd = (peak - cumulative) / peak if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd

    return max_dd
```

**Example:**
```
Trades:   +100, -30, +50, -80, +20
Cumul:    100, 70, 120, 40, 60
Peak:     100, 100, 120, 120, 120
DD:       0%, 30%, 0%, 67%, 50%
MDD:      67%
```

### 4.6 Overall Aggregated Metrics

**Computed from all windows:**
```python
overall_sharpe = compute_sharpe(
    [w.total_pnl for w in windows],
    risk_free_rate=0.0,
    periods_per_year=365 * 24 / window_hours  # 8760 for 1h
)
overall_mdd = compute_mdd(all_pnls)  # across all trades
overall_win_rate = len([t for t in all_trades if t.pnl > 0]) / len(all_trades)
avg_signals_per_day = total_trades / (days_in_period)
```

### 4.7 Data Requirements

**Minimum to run WFA:**
- At least 1 complete window of data
- At least 2 trades for Sharpe computation
- Valid timestamps for bucketing

**Failure Modes:**
- No execution data: `block_reason = "No execution data found"`
- Insufficient trades: `overall_sharpe = 0.0`
- Zero variance: `sharpe = 0.0` (no signal-noise ratio)

---

## 5. Live Gate Criteria

### 5.1 The 6-Check Gate

**File:** `engine/src/modes/live_gate.py:112-267`

The live gate is a boolean AND of 6 independent checks. ALL must pass for live eligibility.

| # | Check | Threshold | Rationale | Severity |
|---|-------|-----------|-----------|----------|
| 1 | Sharpe Ratio (7d rolling) | ≥ 2.5 | Return/volatility ratio validates strategy quality | CRITICAL |
| 2 | Max Drawdown | < 5% | Capital preservation gate | CRITICAL |
| 3 | Daily Signals | ≥ 100/day | Sufficient trade flow for edge expression | MEDIUM |
| 4 | Kill Switch Status | Not halted | Prevents live trading if emergency halt triggered | CRITICAL |
| 5 | Circuit Breaker | CLOSED | Exchange health gate prevents catastrophic loss | CRITICAL |
| 6 | Exchange Health Scores | ≥ 95% | Per-exchange availability and responsiveness | MEDIUM |

### 5.2 Check 1: Sharpe Ratio

**Implementation (line 138-150):**
```python
sharpe_ok = wf_result.overall_sharpe >= self.SHARPE_THRESHOLD  # 2.5
checks.append(LiveGateCheck(
    name="Sharpe Ratio",
    passed=sharpe_ok,
    value=f"{wf_result.overall_sharpe:.2f}",
    threshold=f">= {self.SHARPE_THRESHOLD}",
))
```

**Interpretation:**
- **Sharpe 2.5:** Excellent risk-adjusted returns (top-decile strategies)
- **Sharpe < 2.5:** Too much volatility relative to returns; unsuitable for live
- **Sharpe 0.0:** Zero return or infinite volatility; blocks live

**Example Gate Failure:**
```
Scenario: 7-day Sharpe = 1.8
Message: "Sharpe 1.8 < 2.5"
Action: Blocks live, auto-reeval in 24h
```

### 5.3 Check 2: Maximum Drawdown

**Implementation (line 155-167):**
```python
mdd_ok = wf_result.overall_mdd < self.MDD_THRESHOLD  # 0.05 (5%)
checks.append(LiveGateCheck(
    name="Max Drawdown",
    passed=mdd_ok,
    value=f"{wf_result.overall_mdd * 100:.1f}%",
    threshold=f"< {self.MDD_THRESHOLD * 100:.0f}%",
))
```

**Rationale:**
- A 5% drawdown on 1 BTC = 0.05 BTC loss
- Crypto volatility is high; 5% is conservative upper bound
- Prevents leverage death spirals (margin call chains)

**Example Gate Failure:**
```
Scenario: 7-day MDD = 7.2%
Message: "MDD 7.2% >= 5.0%"
Action: Blocks live, requires strategy adjustment
```

### 5.4 Check 3: Signals Per Day

**Implementation (line 172-184):**
```python
signals_ok = wf_result.avg_signals_per_day >= self.MIN_SIGNALS_PER_DAY  # 100
checks.append(LiveGateCheck(
    name="Signals/Day",
    passed=signals_ok,
    value=f"{wf_result.avg_signals_per_day:.0f}",
    threshold=f">= {self.MIN_SIGNALS_PER_DAY}",
))
```

**Calculation:**
```python
avg_signals_per_day = total_trades / days_in_period
# Over 7 days: 100/day = 700 minimum trades for gate pass
```

**Rationale:**
- Arbitrage edge is weak per-trade (often 5-20 bps)
- Requires 100+ daily trades to compound to meaningful monthly returns
- Validates sufficient market opportunities in test period

**Example Gate Failure:**
```
Scenario: 7-day average = 42 signals/day (294 total over 7d)
Message: "Signals/day 42 < 100"
Action: Blocks live, need broader symbol/exchange coverage
```

### 5.5 Check 4: Kill Switch Status

**Implementation (line 189-201):**
```python
ks_halted = self._check_kill_switch()
ks_ok = not ks_halted
checks.append(LiveGateCheck(
    name="Kill Switch",
    passed=ks_ok,
    value="HALTED" if ks_halted else "Clear",
    threshold="Not halted",
))
```

**Safety Context:**
- Kill switch is 3-tier emergency halt (see Section 7)
- Triggered by: extreme loss (Tier 1), technical failure (Tier 2), human override (Tier 3)
- If halted during evaluation → live gate blocks immediately
- Prevents trading with known failures

### 5.6 Check 5: Circuit Breaker State

**Implementation (line 205-217):**
```python
cb_state_value = self._get_circuit_breaker_state()
cb_ok = cb_state_value == 0  # 0 = CLOSED
cb_label = {0: "CLOSED", 1: "OPEN", 2: "HALF_OPEN"}.get(...)
checks.append(LiveGateCheck(
    name="Circuit Breaker",
    passed=cb_ok,
    value=cb_label,
    threshold="CLOSED",
))
```

**States:**
- **CLOSED (0):** Normal operation, trading enabled
- **OPEN (1):** Exchange unavailable or health degraded, trading paused
- **HALF_OPEN (2):** Recovery in progress, limited trading allowed

**Example Gate Failure:**
```
Scenario: Bitget API response time > 5s, CB_OPEN triggered
Message: "Circuit breaker is OPEN (expected CLOSED)"
Action: Blocks live until exchange recovers
```

### 5.7 Check 6: Exchange Health Scores

**Implementation (line 222-233):**
```python
exchange_health_ok, health_detail = self._check_exchange_health()
checks.append(LiveGateCheck(
    name="Exchange Health",
    passed=exchange_health_ok,
    value="OK" if exchange_health_ok else "DEGRADED",
    threshold=f">= {self.MIN_EXCHANGE_HEALTH}",
    detail=health_detail,
))
```

**Computation (line 374-405):**
```python
def _check_exchange_health(self) -> tuple[bool, str]:
    scores: dict[str, float] = self._exchange_health_fn()
    # scores = {"bitget": 0.98, "bybit": 0.97, ...}

    failing = {
        exch: score for exch, score in scores.items()
        if score < self.MIN_EXCHANGE_HEALTH  # 0.95
    }

    if failing:
        detail = ", ".join(f"{exch}={score:.3f}" for exch, score in sorted(failing.items()))
        return False, f"Below threshold: {detail}"

    return True, "All exchanges healthy"
```

**Health Score Formula:**
```
health_score = (1.0 - avg_latency_ms / 5000) * uptime_fraction
- Bitget 200ms latency, 99.9% uptime → 0.96 × 0.999 ≈ 0.96 (PASS)
- ByBit 300ms latency, 99.5% uptime → 0.94 × 0.995 ≈ 0.935 (FAIL)
```

---

## 6. Atomic Execution Protocol

### 6.1 Overview

**File:** `engine/src/execution/executor.py`

The atomic execution engine handles two scenarios:
1. **Same-Exchange:** Both buy+sell on single exchange (parallel execution)
2. **Cross-Exchange:** Buy on A, sell on B (sequential per Amendment 4)

### 6.2 Same-Exchange Execution (Parallel)

**Amendment 5: RC-SAME-1 through RC-SAME-11 Race Conditions**

**Flow (line 146-255):**

```
STEP 1: Halt check
  ↓
STEP 2: Health check (exchange > 0.9)
  ↓
STEP 3: Acquire exchange lock (atomic)
  ↓
STEP 4: Submit leg1 + leg2 in parallel (asyncio.gather)
  ├─ leg1: place_with_timeout(leg1_order, timeout=500ms)
  ├─ leg2: place_with_timeout(leg2_order, timeout=500ms)
  ↓
STEP 5: Collect results (success/timeout/exception)
  ↓
STEP 6: Evaluate fills
  ├─ Check: leg1_fill >= 80% of requested
  ├─ Check: leg2_fill >= 80% of requested
  ↓
STEP 7: If ANY check fails OR exception
  ├─ Cancel leg1 (if submitted)
  ├─ Cancel leg2 (if submitted)
  ├─ If cancel fails: halt_local() + log CRITICAL
  └─ Return: ROLLED_BACK
  ↓
STEP 8: If ALL checks pass
  └─ Return: SUCCESS
  ↓
FINALLY: Release lock
```

**Race Conditions Handled:**
- **RC-SAME-1:** Halt flag set → REJECTED pre-submission
- **RC-SAME-2:** Exchange health < 0.9 → REJECTED pre-submission
- **RC-SAME-3:** Timeout on leg1 OR leg2 → cancel + ROLLED_BACK
- **RC-SAME-4:** Partial fill ≤ 80% → cancel + ROLLED_BACK
- **RC-SAME-5:** Partial fill > 80% → accept (quantity adjusted downstream)
- **RC-SAME-6-11:** Exception paths (network, validation, etc.) → cancel + ROLLED_BACK

### 6.3 Cross-Exchange Execution (Sequential)

**Amendment 4: 14-Step Protocol**

**Phase 1: PRE-VALIDATION (Steps 0-7)**

```
Step 0: Halt check
Step 1: Health check (both exchanges > 0.9)
Step 2: Balance/margin checks (delegated to RiskGuardian)
Step 3: Snapshot orderbooks (best effort)
Step 4: Re-verify spread is positive (sanity check)
Step 5: Rollback cost check (delegated to RiskGuardian)
Step 6: Acquire lock on exchange A
Step 7: Acquire lock on exchange B
```

**Phase 2: SEQUENTIAL SUBMISSION (Steps 8-11)**

```
Step 8: Submit Leg 1 on Exchange A
        • timeout 500ms
        • if timeout: cancel leg1, return ROLLED_BACK
        • if exception: log error, return ROLLED_BACK

Step 9: Evaluate Leg 1 fill ratio
        • if fill < 80%: cancel leg1, return ROLLED_BACK
        • if fill >= 80%: proceed (adjust leg2 size if partial)

Step 10: Submit Leg 2 on Exchange B
         • timeout 500ms
         • use leg1's actual filled amount
         • if timeout/exception: cancel leg1, return ROLLED_BACK

Step 11: Evaluate Leg 2 fill ratio
         • if fill < 80%: cancel leg1, return ROLLED_BACK
         • if fill >= 80%: SUCCESS
```

**Phase 3: ROLLBACK (Step 12)**

```
If leg1 fills but leg2 fails:
  • Cancel leg1 using its order_id
  • If cancel succeeds: return ROLLED_BACK
  • If cancel fails: halt_local(), return ROLLBACK_FAILED
      (stranded position, human intervention required)
```

**Phase 4: RECONCILIATION (Steps 13-14, Async)**

```
Step 13 (async, non-blocking):
  • After delay (default 5s), verify fills via REST API
  • Log any discrepancies
  • Never blocks trade result

Step 14 (optional):
  • Archive execution record to TimescaleDB
```

**Implementation Excerpt (line 261-437):**
```python
async def execute_cross_exchange(
    self,
    leg1_order: Order,
    leg2_order: Order,
    strategy_id: str,
    min_edge: Decimal,
) -> ExecutionResult:
    # PRE-VALIDATION
    if self._check_halt():
        return ExecutionResult(status=ExecutionStatus.REJECTED, ...)

    # Step 8: Submit leg1
    try:
        leg1_trade = await self._place_with_timeout(adapter_a, leg1_order)
    except asyncio.TimeoutError:
        await self._rollback_order(ex_a_id, leg1_order)
        return ExecutionResult(status=ExecutionStatus.ROLLED_BACK, ...)

    # Step 9: Evaluate leg1 fill
    leg1_ratio = leg1_result.fill_ratio(leg1_order.amount)
    if leg1_ratio <= 0.80:
        await self._rollback_order(ex_a_id, leg1_order)
        return ExecutionResult(status=ExecutionStatus.ROLLED_BACK, ...)

    # Adjust leg2 for partial fill
    adjusted_leg2 = leg2_order if leg1_ratio >= 1.0 else \
                    leg2_order.model_copy(update={"amount": leg1_result.filled_amount})

    # Step 10: Submit leg2
    try:
        leg2_trade = await self._place_with_timeout(adapter_b, adjusted_leg2)
    except Exception:
        # Step 12: Rollback leg1
        await self._do_rollback_cross(...)

    # Step 13: Async reconciliation
    asyncio.ensure_future(self._post_execution_reconcile(...))

    return ExecutionResult(status=ExecutionStatus.SUCCESS, ...)
```

---

## 7. Risk Management Chain

### 7.1 Layered Defense Model

The LEVIATHAN engine implements a 4-layer risk control hierarchy:

```
Layer 1: KillSwitch (3-tier emergency halt)
         ↓
Layer 2: CircuitBreaker (exchange health gate)
         ↓
Layer 3: RiskGuardian (9-check pre-execution validation)
         ↓
Layer 4: AtomicExecutor (transaction-level safety)
```

### 7.2 KillSwitch (3 Tiers)

**File:** `engine/src/risk/kill_switch.py`

**Tier 1: Loss-Based Halt**
- Trigger: Cumulative daily loss > threshold (default: -0.5 BTC)
- Action: Stop all trading, send Telegram alert
- Recovery: Manual reset via `clear_halt()`

**Tier 2: Technical Failure Halt**
- Trigger: Circuit breaker OPEN > 30 minutes
- Trigger: Latency spikes (avg > 5s for 10 consecutive requests)
- Trigger: Exchange connection loss > 10 minutes
- Action: Pause trading, alert operations
- Recovery: Automatic on health recovery

**Tier 3: Manual Override**
- Trigger: `halt_local()` called by operator
- Action: Immediate trading halt
- Recovery: Manual `clear_halt()` call

**State Machine:**
```
CLEAR → [loss/technical/manual trigger] → HALTED
HALTED → [recovery + manual reset] → CLEAR
```

### 7.3 CircuitBreaker (3 States)

**File:** `engine/src/risk/circuit_breaker.py`

**State CLOSED (Normal):**
- All checks passing
- Trading enabled
- No order restrictions

**State OPEN (Paused):**
- Transition: failed health check or timeout
- Duration: exponential backoff (1s → 2s → 4s → cap 60s)
- Recovery: automatic retry after backoff

**State HALF_OPEN (Limited):**
- Transition: end of backoff period
- Action: test single order on each exchange
- If success: CLOSED
- If failure: back to OPEN with doubled backoff

### 7.4 RiskGuardian (9 Checks)

Pre-execution validation layer (delegated to core execution flow):

1. **Capital Check:** Available balance ≥ leg1_cost + fees
2. **Margin Check:** Collateral ratio > 1.5x (if margin trading)
3. **Spread Check:** Buy ask < sell bid (positive edge)
4. **Max Position Check:** Total exposure < position limit
5. **Order Size Check:** Size > min, Size < max per exchange
6. **Daily Loss Check:** Cumulative loss < daily_loss_limit
7. **Consecutive Loss Check:** N consecutive losses < threshold
8. **Slippage Check:** Expected slippage < max_allowed_slippage
9. **Rollback Cost Check:** Potential cancellation cost < max_rollback_cost

### 7.5 AtomicExecutor (Transaction-Level)

Per Section 6: implements 14-step cross-exchange protocol + race condition handling.

---

## 8. Phase 4/5 Recommendations

### 8.1 Phase 4: Native Exchange Adapters

**Objective:** Replace CCXT with native REST/WebSocket implementations.

**Slippage Interface Contract:**
All adapters (native and ccxt-based) MUST implement identical slippage interface:

```python
class ExchangeAdapter(Protocol):
    async def estimate_slippage(
        self,
        side: OrderSide,
        size: Decimal,
        symbol: str,
    ) -> Decimal:
        """Return expected slippage in fraction (e.g., 0.001 = 0.1%)."""
        ...

    async def get_slippage_model(self) -> SlippageModel:
        """Return applicable slippage model for this exchange."""
        ...
```

**Implementation Path:**
1. Define adapter-specific slippage calibration (k, γ per exchange)
2. Implement `estimate_slippage()` using PowerLawSlippage or CEXOrderbookSlippage
3. Validate against historical execution data
4. Deploy behind feature flag (e.g., `USE_NATIVE_BITGET=true`)

### 8.2 Phase 5: Live Readiness

**Pre-Flight Checklist (72 Hours Before Go-Live):**

```
[ ] 72-hour shadow mode simulation
    - 100+ signals/day
    - Sharpe ≥ 2.5
    - MDD < 5%

[ ] Exchange API validation
    - Response times: all < 1s (p95)
    - Uptime: all > 99.5%
    - No authentication failures

[ ] Database continuity
    - Backups executing successfully
    - Query latency < 100ms (p99)
    - Replication lag < 10s

[ ] Risk limits review
    - Position limits set conservatively (2x initial)
    - Daily loss limits configured
    - Kill switch manual trigger tested

[ ] Telegram notifications
    - Test messages received in < 5s
    - Signal alerts, daily summaries working
    - Emergency alerts tested

[ ] Atomic execution dry-run
    - 10 simulated same-exchange trades
    - 10 simulated cross-exchange trades
    - All reconciliation checks passing

[ ] Operator training
    - Manual halt/resume procedures practiced
    - Stranded position recovery procedures known
    - Incident response playbook reviewed
```

**Go-Live Safety Gates:**
1. Sharpe ≥ 2.5 (minimum 7-day rolling)
2. MDD < 5% (maximum peak-to-trough)
3. Signals/day ≥ 100 (consistent opportunity flow)
4. All kill switches clear
5. Circuit breaker in CLOSED state
6. All exchanges health ≥ 95%

**Monitoring (First 30 Days):**
- Daily PnL report via Telegram
- Hourly slippage analysis (actual vs. model)
- Weekly walk-forward re-evaluation
- Kill switch trigger alert (any tier)
- Manual override audit (full details logged)

---

## 9. Formula Reference

### 9.1 Slippage (Shadow Mode)

```
slippage_bps = base_slippage_bps * k * size^γ * random_factor

where:
  base_slippage_bps = 10 (0.1% in basis points)
  k = 1.0
  γ = 0.5
  size = order size in base asset
  random_factor ~ Uniform[0.5, 1.5]
```

### 9.2 Sharpe Ratio (Annualized from Hourly)

```
Sharpe = (μ - rf) / σ * sqrt(periods_per_year)

where:
  μ = mean(hourly_returns)
  σ = std(hourly_returns)
  rf = risk_free_rate / periods_per_year
  periods_per_year = 8760 (for 1-hour windows)
```

### 9.3 Maximum Drawdown

```
MDD = max_t { (Peak_t - Cumulative_PnL_t) / Peak_t }

where:
  Peak_t = max(Cumulative_PnL_s) for s <= t
  Cumulative_PnL_t = sum of all PnL up to time t
```

### 9.4 Impact Decay (Power-Law)

```
Impact(t) = Impact_0 * (1 + t/t_0)^(-γ)

where:
  t = elapsed time (seconds)
  t_0 = 60 (characteristic time)
  γ = 0.5
```

---

## 10. Changelog

| Version | Date | Changes |
|---------|------|---------|
| 1.0 | 2026-03-06 | Initial manifesto: 3 slippage models, 6-check live gate, 14-step execution, formula reference |

---

## References

- Almgren, R., & Chriss, N. (2000). "Optimal Execution of Portfolio Transactions." *Journal of Risk*, 3(2).
- Kyle, A. S. (1985). "Continuous auctions and insider trading." *Econometrica*, 53(6).
- LEVIATHAN Blueprint Amendment 4-6 (internal design documents)
- Walk-Forward Analysis: "Tradestation Tutorial" (rolling-window backtest methodology)
- Sharpe Ratio: Sharpe, W. F. (1994). "The Sharpe Ratio." *Journal of Portfolio Management*, 21(1).
