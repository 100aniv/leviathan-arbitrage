# Day 10 Plan — MarketStats real 24h ADV aggregator

**Path-B v2 Day 10** — Replace `signal.py:150-158` top-5 depth proxy with
rolling 24h volume from WS trade stream.

## Goal

The current `_compute_dynamic_adv` uses the sum of top-5 bid and top-5 ask
volumes as a stand-in for ADV. For illiquid altcoins this underestimates
true daily volume by 10-100x, corrupting the `size/ADV` input of
`CEXOrderbookSlippage.predict` and therefore Day 13 gamma calibration.

Day 10 introduces `src/core/market_stats.py::MarketStats` — a thread-safe,
per-(exchange, symbol) rolling-window aggregator that consumes WS trade
events and returns a real 24h USD volume. Behind feature flag
`CORE_REAL_ADV_ENABLED` (default `false`), `signal.py` prefers
`market_stats.get_adv_usd(ex, sym)` whenever the aggregator is warm
(≥15min of data); otherwise it falls back to the existing top-5 proxy so
behaviour is unchanged when the flag is off or the stream is cold.

## Acceptance Criteria

- `MarketStats.get_adv_usd(exchange, symbol)` returns a `Decimal` USD
  value. For a test fixture replaying trades equivalent to Binance REST
  `/api/v3/ticker/24hr`, the output must land within ±15% of the REST
  reference (verified by unit fixture, not a live call).
- Feature flag `CORE_REAL_ADV_ENABLED` gates the new code path. Flag
  absent or `false` → `signal.py` behaviour is byte-identical to Day 9.
- `is_warm(ex, sym)` returns `False` until ≥15min of trade data exists
  for that pair; signal.py falls back to the proxy during warmup.
- Thread-safe: 40 concurrent `on_trade` calls produce the same final
  aggregate as 40 serial calls (asyncio.Lock).
- Full unit regression: `pytest tests/unit/ -x --no-cov` green,
  baseline+6 = 4936 passing (adjust in Stage E if actual baseline differs).
- LOC invariant: `src/modes/live.py` and `src/main.py` unchanged.
- `signal.py` delta ≤ +10 LOC (new module + one feature-flag branch).

## Files Changed

1. `src/core/market_stats.py` — new module (~150 LOC).
2. `src/core/signal.py` — feature-flag branch in `_compute_dynamic_adv`
   (~5 LOC delta, strictly additive).
3. `tests/unit/core/test_market_stats.py` — new test file, 6 tests.
4. `CHANGELOG.md` — `[Unreleased].Added` bullet.

## Rollback

Revert the 3 source-file diffs and the CHANGELOG bullet. Feature flag is
default `false`, so even a partial revert (keeping `market_stats.py`) is
safe — nothing consumes it until the flag is on.

## Scope Constraints (from operator)

- Do **not** wire WS trade handlers into native adapters in this commit.
  Day 10 ships the module + `signal.py` flag branch only; adapter wiring
  is deferred to a follow-up (Day 10b or Day 14).
- No monolith edits beyond the `signal.py` 5-line branch.
- Day 13 gamma calibration depends on real ADV values flowing through;
  until adapter wiring lands, the flag can be flipped on in tests but
  production will continue to use the proxy (graceful `is_warm=False`).

## Notes

- Module exposes a `stats_summary()` dict for debug/ops endpoints
  (per-pair trade count + warm status + aggregate). Not wired into any
  route in Day 10 — reserved for follow-up observability work.
- `TradeEvent.qty` is base asset units; USD conversion is `price * qty`
  at trade time. No FX conversion needed for USDT/USDC/USD pairs.
- Rolling window is implemented with `collections.deque` and a forward
  eviction sweep on every `get_adv_usd` / `on_trade` call.
