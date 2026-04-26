# FF Execution Pipeline — Complete Structural Trace

**Date**: 2026-04-15 | **Issue**: Dual exit paths causing ghost-clear + 2-4s latency | **Scope**: 7 execution stages

---

## STAGE 1: Signal Generation (signal.py → on_orderbook_update)

### Flow
```
OrderBook update → PriceHub best_bid/best_ask 
  → CEXOrderbookSlippage (impact calc) 
  → CostCalculator (fees+slippage+network+rollback) 
  → Dedup check (cooldown=5s) 
  → Emit Signal to Redis "leviathan:signals"
```

### Failure Modes
| Mode | Trigger | Impact |
|------|---------|--------|
| **SG-1: Stale orderbook** | book_age > 30s | signal rejected (max_book_age_seconds) |
| **SG-2: Zero/negative spread** | buy_price >= sell_price | signal rejected (gross_spread <= 0) |
| **SG-3: Slippage underestimate** | ADV cold-start (default_adv=10000) | inflated expected_profit, margin exhaustion risk |
| **SG-4: Duplicate suppression** | Same (symbol, buy_ex, sell_ex) within 5s | legitimate opportunity missed |
| **SG-5: Market impact ceiling** | sigma computed as default (0.03) | impact overestimated in high-vol regimes |

**Latency**: 5-10ms (dominated by dedup window enforcement, not computation)

---

## STAGE 2: Strategy Evaluation (manager.py → _dispatch)

### Flow
```
StrategyManager polls "leviathan:signals" (group="strategy-manager")
  → _dispatch(signal) 
    → _should_route(strategy, signal) 
    → strategy.on_signal(signal) [**CRITICAL: FF has TWO ASYNC PATHS HERE**]
```

### FF Strategy: DUAL ASYNC EXIT PATHS (🔴 RACE CONDITION ROOT)

**Path A: Signal-Driven (on_signal)**
```python
async def on_signal(signal: Signal) -> Optional[TradeRequest]:
    if signal.symbol in self._open_positions:
        pos = self._open_positions[signal.symbol]
        age_s = time.monotonic() - pos["entry_time"]
        
        # Check spread_reversion OR holding_timeout
        if last_spread_bps <= 4.05bps or age_s > 300s:
            # Generate exit TradeRequest
            return exit_trade_request
```

**Path B: Time-Driven Monitor Loop (independent background task)**
```python
async def _open_positions_monitor(self) -> None:
    """Runs EVERY 60s, independent of signals."""
    while self._is_active:
        await asyncio.sleep(60)
        for sym, pos in list(self._open_positions.items()):
            age_s = now - pos["entry_time"]
            
            # Check spread_reversion OR holding_timeout
            if last_spread <= 4.05bps or age_s > 300s:
                self._pending_exit_requests.append(exit_req)
                self._open_positions.pop(sym)
```

### RACE CONDITION: Ghost-Clear Mechanism

```
Time    Monitor Task                          on_signal() Task
────    ────────────────────────────────────  ─────────────────────────────
T0      [polling _open_positions]
T1      Finds sym in positions               [new signal arrives]
T2      Checks age/spread (criteria met)     Reads _open_positions
T3      _exiting_symbols.add(sym)           [sym still in _open_positions]
T4      Creates exit_req_A                   Creates exit_req_B
T5      Appends to _pending_exit_requests    [queued but...]
T6      _open_positions.pop(sym) ✓          Emits both TradeRequests
T7      [monitor sleeps 60s]
T8                                          on_signal()'s TradeRequest fails
T9                                          on_execution_rollback() called
T10                                         [confused state: position already removed]
```

### Failure Modes

| Mode | Trigger | Impact |
|------|---------|--------|
| **SE-1: Ghost-clear (dual exit)** | Monitor + on_signal both see position | 2 TradeRequests exit same position; first succeeds, second times out → ROLLBACK_FAILED |
| **SE-2: Stale spread tracking** | last_spread_bps set at entry; never updated | false spread_reversion triggers at old spread threshold |
| **SE-3: Monitor interval too coarse** | 60s sleep between checks | position ages 60s without monitoring; exit delayed |
| **SE-4: Position removed before confirmation** | Monitor pops from _open_positions before exit executes | on_execution_rollback() can't find position to restore |
| **SE-5: Settlement race (FR only)** | _check_settlement_release() + _pending_exit_requests + on_signal() | Multiple exit requests queued; pop_exit_requests() called twice = drain lost |
| **SE-6: pop_exit_requests() dual-drain** | FF doesn't guard; spot_futures does | 2nd caller gets empty list; exits lost |

**Latency**: 0-100ms (signal routing) + 0-60s (until monitor wakes up)

**Key Coordination Issues:**
1. No atomic transaction: position exists in _open_positions AND _exiting_symbols must stay in sync
2. Gate `_exiting_symbols` checked AFTER reading position, not BEFORE
3. No interlocking: monitor and on_signal() run async without locks

---

## STAGE 3: Trade Request Emission (manager.py → _emit_trade_request)

### Flow
```
Publish TradeRequest to Redis "leviathan:trade_requests" stream
```

### Failure Modes
| Mode | Trigger | Impact |
|------|---------|--------|
| **TE-1: Collision check** | Same (symbol, exchange_pair) within 10s | position collision blocks new entries (correct) |
| **TE-2: Min notional filter** | any leg notional < $5 | small legs rejected (prevents imbalanced positions) |

**Latency**: 1-2ms (Redis publish)

---

## STAGE 4: Execution Routing (trade_consumer.py → _execute)

### Flow
```
TradeRequestConsumer polls "leviathan:trade_requests"
  → Risk check (RiskGuardian)
  → Route based on leg exchanges:
    - Same exchange → execute_same_exchange (parallel legs)
    - Cross exchange → execute_cross_exchange (sequential)
```

### FF Routing Decision
**FF legs are ALWAYS cross-exchange** (buy on binance, sell on binance_futures OR different spot exchanges)
→ Routes to `execute_cross_exchange()` [Sequential Amendment 4]

### Failure Modes
| Mode | Trigger | Impact |
|------|---------|--------|
| **RT-1: Risk rejection** | RiskGuardian gate fails | TradeRequest blocked (intentional) |
| **RT-2: Halt during consumption** | Kill switch fires | message stays in PEL, not ack'd → retried on resume |
| **RT-3: Deserialization failure** | malformed TradeRequest JSON | ack'd (permanent failure), not retried |

**Latency**: 1-2ms (risk check + routing decision)

---

## STAGE 5: Atomic Execution (atomic.py → execute_cross_exchange)

### Amendment 4: 14-Step Sequential Protocol

```
Step 1:  Health check (leg1 exchange) — REJECTS if health_score < 0.6
Step 2:  Acquire capital lock (leg1 exchange)
Step 3:  Submit leg1 order (IOC limit or market fallback)
         TIMEOUT: leg_timeout_ms (default 5000ms = 5s)
         
Step 4:  Validate leg1 fill (must be >= 80%)
Step 5:  Run edge check: net_profit >= min_edge
Step 6:  Call SlippageFeedback (US-283)
Step 7:  Release lock, continue to leg2

Step 8:  Health check (leg2 exchange)
Step 9:  Acquire capital lock (leg2 exchange)
Step 10: Submit leg2 order
         TIMEOUT: leg_timeout_ms (same as leg1)

Step 11: Validate leg2 fill
Step 12: Calculate final PnL from actual fills

Step 13: If leg2 failed → Rollback leg1
Step 14: Return ExecutionResult (SUCCESS | ROLLED_BACK | TIMEOUT)
```

### Failure Modes

| Mode | Trigger | Impact |
|------|---------|--------|
| **EX-1: Health threshold** | leg1 health < 0.6 | execution rejected before any order placed |
| **EX-2: Leg1 timeout** | leg1.place_order() > 5000ms | leg2 never submitted; edge evaporates during wait; return TIMEOUT |
| **EX-3: Leg1 partial fill** | leg1 filled <80% | retry market order; if still <80%, consider failed |
| **EX-4: Edge evaporated** | market moves during leg1 wait | leg2 execution at loss (edge check fails) |
| **EX-5: Leg2 timeout** | leg2.place_order() > 5000ms | leg1 filled but leg2 hung → rollback leg1 |
| **EX-6: Capital exhaustion** | acquire_lock waits indefinitely | FF positions block each other (BUG-72: max 4 concurrent) |
| **EX-7: Partial fill stop** | enabled + leg fills <95% | auto-close partial position (US-275) |
| **EX-8: Order split** | notional > $50 threshold | split into 3 chunks with 200ms delays; adds 400ms latency |

**Latency Breakdown (Worst Case)**
```
Health check:           50-200ms
Leg1 acquire lock:      0-1000ms (if other trades hold lock)
Leg1 place + timeout:   0-5000ms (configured timeout)
Leg1 validation:        50-200ms
Edge check:             1-5ms
Leg2 acquire lock:      0-1000ms
Leg2 place + timeout:   0-5000ms
Leg2 validation:        50-200ms
─────────────────────────────────
TOTAL WORST CASE:       ~6500-12000ms (6.5-12s)
TYPICAL:                300-800ms
```

**During leg1 timeout (5s window)**:
- Market spreads move 10-50bps
- FF entry spread was 15bps (min_spread_bps config)
- After 5s, spread could be negative (loss position)
- Edge check might fail, causing exit at loss

---

## STAGE 6: Rollback on Failure (atomic.py → _execute_cross_exchange)

### Rollback Protocol (Amendment 4, Step 13)

```
If leg2 fails:
  1. Unwind leg1 with market order (reverse side, same size)
  2. Wait up to 3000ms (rollback_timeout_ms) for completion
  3. If leg1 unwind succeeds: return ROLLED_BACK (position closed)
  4. If leg1 unwind fails: return ROLLBACK_FAILED (stranded position)
```

### Post-Rollback Callbacks

**on_execution_success(symbol)** called when exit order fills:
```python
def on_execution_success(self, symbol: str) -> None:
    self._exiting_symbols.discard(symbol)
    if symbol in self._pending_exits:
        self._pending_exits.pop(symbol)  # ← position snapshot deleted
```

**on_execution_rollback(symbol)** called when rollback completes:
```python
def on_execution_rollback(self, symbol: str) -> None:
    self._exiting_symbols.discard(symbol)
    if symbol in self._pending_exits:
        restored = self._pending_exits.pop(symbol)
        self._open_positions[symbol] = restored  # ← restore tracking
    elif symbol in self._open_positions:
        self._open_positions.pop(symbol, None)  # ← entry rollback
    else:
        # 🔴 BUG-116: on_fill may have cleared _pending_exits before rollback fires
        logger.warning("ff.rollback_no_state symbol=%s")
```

### Failure Modes

| Mode | Trigger | Impact |
|------|---------|--------|
| **RB-1: Leg1 unwind timeout** | leg1 unwind order > 3000ms | stranded position (ROLLBACK_FAILED) |
| **RB-2: State loss race** | on_fill fires before rollback callback | _pending_exits snapshot gone; can't restore |
| **RB-3: Stranded position** | ROLLBACK_FAILED or on_fill wins race | position stuck on exchange; operator intervention needed |
| **RB-4: Dual path confusion** | both monitor exit + on_signal exit fail | on_execution_rollback() called twice or state inconsistent |

**Latency**: 0-3000ms (rollback timeout)

---

## STAGE 7: PnL Calculation & State Update

### Paper Mode (Shadow)
```
Paper executor simulates fills:
  fill_price = mid_price * (1 +/- slippage_pct)
  slippage_pct = base * (1 + random(0, 0.5) * volatility)
  
  PnL = (leg2_price - leg1_price) * size - fees - slippage
```

### Live Mode
```
Actual exchange fills (from TradeRequestConsumer.on_result callback):
  PnL = sum(trades.amount * trades.price) - actual_fees
  
State tracked by PositionManager:
  → Engine._total_pnl += result.pnl
  → Engine._position_sizes[symbol] = 0  (if closed)
  → Strategy metrics: trades_generated, fills_received, pnl_realized
```

### Failure Modes

| Mode | Trigger | Impact |
|------|---------|--------|
| **PNL-1: Partial fill** | leg fills <80% | PnL understated (unexecuted portion ignored) |
| **PNL-2: Slippage model divergence** | shadow vs live differ | backtests overestimate (k=0.0 disables PowerLaw in paper) |
| **PNL-3: On-fill race** | on_execution_success() + on_fill both update state | double-booking or data loss |
| **PNL-4: Position mismatch** | _total_pnl vs actual exchange position differ | reconciliation required (PositionReconciler) |

**Latency**: ~10-50ms (PnL computation + state update)

---

## CONSOLIDATED RACE CONDITION: Ghost-Clear Scenario

### Timeline (Pathological Case)

```
T+0:00     Monitor wakes up (60s interval)
T+0:01     Monitor reads position "BTC/USDT" (age=120s, last_spread=10bps < 4.05bps threshold)
T+0:02     Monitor: _exiting_symbols.add("BTC/USDT")
T+0:03     on_signal() receives NEW signal for "BTC/USDT"
T+0:04     on_signal() reads _open_positions["BTC/USDT"] (still there!)
T+0:05     Monitor: exit_req_A generated, appended to _pending_exit_requests
T+0:06     on_signal(): checks if symbol in _open_positions (YES) → also generates exit_req_B
T+0:07     Monitor: _open_positions.pop("BTC/USDT")
T+0:08     pop_exit_requests() [called from main loop] retrieves [exit_req_A, exit_req_B]
T+0:09     Both TradeRequests routed to TradeRequestConsumer
           - exit_req_A: symbol="BTC/USDT", legs=[BUY short_ex, SELL long_ex]
           - exit_req_B: symbol="BTC/USDT", legs=[BUY short_ex, SELL long_ex]
T+0.50     exit_req_A executes via cross-exchange (Amendment 4)
T+1.50     exit_req_A succeeds: on_execution_success("BTC/USDT")
           → _exiting_symbols.discard("BTC/USDT")
           → _pending_exits.pop("BTC/USDT") [if it existed]
T+2.00     exit_req_B starts execution
           But position was already closed by exit_req_A
           Leg1 (BUY on short_ex) → Market order size=1.5 BTC fails (no position)
           Returns ExecutionStatus.ROLLED_BACK
T+2.50     on_execution_rollback("BTC/USDT") called
           _exiting_symbols.discard("BTC/USDT") ✓
           _pending_exits not found ✓
           _open_positions not found ✓
           → Logs "rollback_no_state" warning 🔴
           
Result: Position shown as closed, but exit marked as ROLLED_BACK (not SUCCESS)
        Metrics: rollback_no_state_count += 1
        User sees: "ghost-clear" (exit happened but execution status wrong)
```

### Why It's Called "Ghost-Clear"

1. **Position is ACTUALLY closed** (exit_req_A succeeded, position unwound on exchange)
2. **But execution reports ROLLED_BACK** (exit_req_B failed, triggering rollback logic)
3. **State tracking gets confused** (on_execution_rollback can't restore what was never pending)
4. **PnL recorded but status wrong** (successful close marked as failure)

---

## LATENCY IMPACT ON EXITS

### Spread-Reversion Exit Vulnerability

```
Entry:    spread=27bps, monitor fires when spread<4.05bps (85% reversion needed)
Latency:  Execute latency ~500-800ms in normal conditions
          Could stretch to 6.5s if leg1 times out

Price movement during execution:
  - Tight market: 1-2bps per 100ms
  - Volatile: 5-10bps per 100ms
  - During exit (leg1 hanging): could be 20-50bps in 5s window

Exit threshold: 4.05bps
Reversion needed: 27 - 4.05 = 22.95bps (85%)

If market moves 5bps during 5s leg1 timeout:
  - Entry spread was 27bps
  - Actual exit spread is 27-5=22bps (still above threshold, exit fires)
  
But if market moves 30bps (high volatility):
  - Actual exit spread is 27-30 = -3bps (NEGATIVE!)
  - Exit happens at LOSS (opposite of intended spread reversion)
```

### Holding Timeout Exit Vulnerability

```
Holding timeout: 300s (5 minutes)
Monitor check: every 60s
Worst case: position ages up to 300+60=360s before exit

During 60s checks:
  - Position could become unprofitable
  - Edge could evaporate
  - Market regime could change (CRISIS mode)
  - Funding rates could shift (FR strategy)
  
Stale spread stored at entry:
  - last_spread_bps never updated after entry
  - If market moves against position, spread_reversion triggers at old threshold
  - Not reflecting current market, only historical state
```

---

## RISK MITIGATION REQUIRED

### Critical Fixes

1. **Atomic Position Removal + Exit Request Generation**
   - Single operation: read position, verify no other exit in-flight, generate exit, mark exiting
   - Use Redis transaction or in-process lock
   - Both monitor and on_signal must coordinate via shared state

2. **Single Exit Trigger, Not Dual**
   - Remove _open_positions_monitor background task OR
   - Make on_signal() delegate to monitor via shared queue (not dual-check)
   - Remove spread_reversion/holding_timeout from on_signal(), keep only in monitor

3. **Update Stale Spread Tracking**
   - last_spread_bps should be CURRENT market spread, not entry spread
   - Update from new signals before exit threshold check
   - Or: remove last_spread_bps, always compute from current signal

4. **Faster Leg1 Timeout for Tight Spreads**
   - spread_reversion exits: use 1000ms timeout (not 5000ms)
   - If leg1 takes >1s, edge already evaporated
   - Fail fast, retry next signal

5. **Guard pop_exit_requests() Dual-Drain**
   - Document in futures_futures like spot_futures does
   - Verify only one path calls pop_exit_requests()
   - Use explicit consumer pattern (pop returns and clears list once)

---

## LATENCY TARGETS FOR VIABILITY

```
Signal generation:      5-10ms (dedup window)
Routing + risk:         50-100ms
Execution (typical):    300-500ms (leg1: 100-200ms, leg2: 100-200ms)
─────────────────────
**Total acceptable:     350-610ms**

If exceeds 1000ms:
  - 27bps entry spread loses ~5-10bps to market movement
  - Only 17-22bps cushion for slippage/fees (too tight)
  - Exit risk: negative PnL
```

---

## STRUCTURAL RECOMMENDATION

The core issue: **Monitor loop + on_signal() both independently manage exits**. 

**Solution**: Single exit trigger via StrategyManager exit loop (current 60s polling), with on_signal() **only** handling new entries.

```python
# Current (broken):
_open_positions_monitor()  # 60s loop generates exits
on_signal()                # Can ALSO generate exits (race!)

# Proposed (atomic):
on_signal()                # Only generates ENTRIES
                           # Does NOT check _open_positions for exits

_manage_exits()            # Single authoritative exit loop (60s)
                           # Reads _open_positions once per cycle
                           # Coordinates spread_reversion + holding_timeout
                           # Uses transactional remove + exit generation
```

This eliminates the dual-path race and 2-4s latency variance.
