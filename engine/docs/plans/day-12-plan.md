# Day 12 Plan — Activate PreTradeValidator + BookWalk via feature flag

**Path-B v2 Day 12** — Gate `PreTradeValidator.validate()` and the inline
BookWalk market-impact check in `live.py` behind feature flag
`EXECUTION_PRETRADE_VALIDATOR_ENABLED` (default `false`).

## Goal

Day 2 wired `PreTradeValidator` unconditionally into `_execute_trade_request`.
Day 12 makes the validator (and the BookWalk VWAP gate) **opt-in** so operators
can enable/disable the gate without a redeploy:

- **Flag off** (default): `_execute_trade_request` skips the validator entirely
  and skips the BookWalk impact check. Behaviour is identical to the pre-Day-2
  baseline — no gate, no rejection logging.
- **Flag on**: every TradeRequest flows through `PreTradeValidator.validate()`
  before reaching the order router; the BookWalk impact check is also active.

This split enables A/B comparison and reduces blast radius if a gate bug causes
phantom rejections in paper trading.

## Acceptance criteria

1. When `EXECUTION_PRETRADE_VALIDATOR_ENABLED=false` (default):
   - `_execute_trade_request` does NOT call `self._pre_trade_validator.validate()`.
   - BookWalk market-impact block at lines ~1414-1443 is skipped.
   - Existing unit tests pass unchanged.

2. When `EXECUTION_PRETRADE_VALIDATOR_ENABLED=true`:
   - Every TradeRequest calls `self._pre_trade_validator.validate(signal, ...)`.
   - A thin book (walk cost > edge) causes rejection via `ReasonCode`.
   - A sufficient book passes through to the executor.
   - Rejection increments `leviathan_signal_rejected_total` (existing validator logic).

3. Live.py LOC growth ≤ +10 lines (plan §1.4 monotonic shrink; Day 14 removes them).

## Deliverables

- `src/modes/live.py`: flag check wrapping the validator call and BookWalk block
  (~8 LOC added, ≤10 total).
- `tests/unit/modes/test_pretrade_validator_live.py`: 4 integration tests
  covering flag-off bypass, thin-book rejection, sufficient-book pass-through,
  and flag-off leaves BookWalk inactive.

## Dependencies

- Day 6 `EXECUTION_JOURNAL_ENABLED` (required per §22.3 interaction matrix).
- Day 8 `EXECUTION_ROUTER_ENABLED` (required per §22.3 interaction matrix).
- The flag interaction check is NOT enforced in Day 12 live.py — that
  responsibility belongs to `ConfigService` (Day 4). Live.py simply reads the
  env var directly.

## Risk: LOW

The validator is already instantiated; this change only adds an `if` guard.
Flag off is the default, so paper canary is unaffected.
