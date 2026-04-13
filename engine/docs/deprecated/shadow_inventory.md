# Shadow Mode Deprecation Inventory

> Generated: Phase I (US-346)
> Status: EngineMode.SHADOW deprecated as of Phase I.
> Migration: Use EngineMode.LIVE with LIVE_GATE_BYPASS=true for canary testing.

## EngineMode.SHADOW References

### src/core/config.py
- `EngineMode.SHADOW = "shadow"` — deprecated enum value, kept for backward compat
- `resolve_engine_mode()`: emits DeprecationWarning when SHADOW resolved
- `OperationalSettings`: `shadow_*` prefixed fields (20+ fields) — still used by ShadowMode orchestrator

### src/main.py
- `_start_background_tasks()`: `elif self._engine_mode == EngineMode.SHADOW:` branch
  - Routes to `_live_mode_loop()` with `name="shadow_canary"`
  - Status: merged into LIVE branch in US-346
- `_shadow_mode_loop()`: ShadowMode orchestrator (paper execution)
  - Called from PAPER mode, not SHADOW mode
- `_init_exchanges()`: `elif _engine_mode in (EngineMode.SHADOW, EngineMode.LIVE):` — keeps SHADOW
- `self._shadow_mode`: instance attribute holding ShadowMode object

### src/modes/shadow.py
- `ShadowMode` class — full paper execution orchestrator
- `PowerLawSlippage` — k=0.0 (inactive per Phase C decision)
- Still used by PAPER mode (`_shadow_mode_loop`) and progressive shadow

### src/modes/__init__.py
- Exports `ShadowMode` — kept for PAPER mode usage

### src/api/routes/shadow.py
- REST endpoints for shadow stats — used by dashboard
- `/shadow/stats`, `/shadow/snapshot` etc.

### src/core/real_signal_producer.py
- `shadow_mode` parameter — strategy.shadow_mode flag

### src/strategies/base.py, src/strategies/manager.py
- `strategy.shadow_mode = True` flag set in shadow/progressive loops

### src/infra/telegram_trade_bot.py, src/infra/telegram.py
- `shadow_mode_start` alert type
- `shadow_total_pnl`, `shadow_trades_executed` keys

## engine/config/engine.json
- `"shadow"` section: kept for PAPER mode canary config
  - `capital_pct`, `max_position_pct`, `max_daily_loss_pct`, `exchanges`
- `"live"` section: canonical config for EngineMode.LIVE
  - `max_position_pct`, `max_daily_loss_pct`, `exchanges`

## Migration Path

| Old | New | Notes |
|-----|-----|-------|
| `ENGINE_MODE=shadow` | `ENGINE_MODE=live` + `LIVE_GATE_BYPASS=true` | Canary with small capital |
| `EngineMode.SHADOW` | `EngineMode.LIVE` | In code references |
| `_engine_mode == EngineMode.SHADOW` | `_engine_mode == EngineMode.LIVE` | main.py routing |

## Files with SHADOW references (grep output)

```
engine/src/main.py
engine/src/modes/shadow.py
engine/src/modes/live.py
engine/src/modes/strategy_validation.py
engine/src/core/real_signal_producer.py
engine/src/core/config.py
engine/src/api/server.py
engine/src/modes/progressive_shadow.py
engine/src/infra/telegram_trade_bot.py
engine/src/infra/telegram.py
engine/src/modes/backtest.py
engine/src/api/routes/trading.py
engine/src/api/routes/strategies.py
engine/src/api/routes/portfolio.py
engine/src/api/routes/attribution.py
engine/src/collectors/bithumb_collector.py
engine/src/api/routes/risk.py
engine/src/api/routes/shadow.py
engine/src/strategies/base.py
engine/src/dex/mock_adapter.py
engine/src/strategies/manager.py
engine/src/modes/preflight.py
engine/src/tuning/optimizer.py
engine/src/collectors/funding_rate_collector.py
engine/src/cli/backtest_cli.py
engine/src/modes/__init__.py
```

## Phase I Action Taken

- `main.py`: `EngineMode.SHADOW` branch in `_start_background_tasks()` merged into `EngineMode.LIVE`
- `modes/base.py`: Created with `BaseMode` abstract base; `LiveMode` inherits it
- Full ShadowMode removal deferred to Phase J (PAPER mode still depends on it)
