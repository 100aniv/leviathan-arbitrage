# codex advisor artifact

- Provider: codex
- Exit code: 0
- Created at: 2026-04-18T10:21:34.420Z

## Original task

Review this change: added src/infra/fx/krw_rate_provider.py (Upbit KRW-USDT REST poll every 30s, stale guard 60s). Wired self._fx_provider in RealDataSignalProducer.__init__. All 4 self._krw_usdt_rate sites replaced with _fx_rate = self._fx_provider.get_rate() once per call. XE-KRW now gated by engine.json strategy_filters.xe_krw_enabled (default false) so PHOENIX Step 2-3 (no KRW) is preserved. Check: race/logic/safety issues. 100 words max.

## Final prompt

Review this change: added src/infra/fx/krw_rate_provider.py (Upbit KRW-USDT REST poll every 30s, stale guard 60s). Wired self._fx_provider in RealDataSignalProducer.__init__. All 4 self._krw_usdt_rate sites replaced with _fx_rate = self._fx_provider.get_rate() once per call. XE-KRW now gated by engine.json strategy_filters.xe_krw_enabled (default false) so PHOENIX Step 2-3 (no KRW) is preserved. Check: race/logic/safety issues. 100 words max.

## Raw output

```text
Findings:

1. `LiveMode` starts the FX poller only opportunistically in `__init__` if an event loop is already running ([live.py](/Users/100aniv/Development/arbitrage_OMC/engine/src/modes/live.py:342)). If `LiveMode` is constructed off-loop, `KRWRateProvider.start()` is never called, and `get_rate()` stays on fallback forever ([krw_rate_provider.py](/Users/100aniv/Development/arbitrage_OMC/engine/src/infra/fx/krw_rate_provider.py:85)). Move startup to `async start()`.

2. No shutdown wiring: `LiveMode.stop()` cancels `_krw_rate_task` but never awaits `_real_signal_producer._fx_provider.stop()` ([live.py](/Users/100aniv/Development/arbitrage_OMC/engine/src/modes/live.py:845)). If started, that background poll task can leak across mode restarts/tests.


OpenAI Codex v0.115.0 (research preview)
--------
workdir: /Users/100aniv/Development/arbitrage_OMC/engine
model: gpt-5.4
provider: openai
approval: never
sandbox: danger-full-access
reasoning effort: none
reasoning summaries: none
session id: 019da01b-0fb8-7c10-b774-72efbae39803
--------
user
Review this change: added src/infra/fx/krw_rate_provider.py (Upbit KRW-USDT REST poll every 30s, stale guard 60s). Wired self._fx_provider in RealDataSignalProducer.__init__. All 4 self._krw_usdt_rate sites replaced with _fx_rate = self._fx_provider.get_rate() once per call. XE-KRW now gated by engine.json strategy_filters.xe_krw_enabled (default false) so PHOENIX Step 2-3 (no KRW) is preserved. Check: race/logic/safety issues. 100 words max.
mcp: chrome-devtools starting
mcp: chrome-devtools ready
mcp startup: ready: chrome-devtools
codex
Reviewing the new FX provider wiring and the XE-KRW gate now. I’ll inspect the added provider, the constructor changes, and the replaced call sites for race, stale-data handling, and behavior regressions.
exec
/bin/zsh -lc 'git status --short' in /Users/100aniv/Development/arbitrage_OMC/engine succeeded in 0ms:
 M .omc/project-memory.json
 M .omc/state/agent-replay-d1dfbee9-40b6-41a8-aa82-06f508cfb187.jsonl
 M .omc/state/idle-notif-cooldown.json
 M .omc/state/last-tool-error.json
 M .omc/state/mission-state.json
 D .omc/state/sessions/2ff306f3-0d61-49a4-9a77-2a29b6a69000/ralph-state.json
 D .omc/state/sessions/2ff306f3-0d61-49a4-9a77-2a29b6a69000/ultrawork-state.json
 D .omc/state/sessions/8ed3cf76-3602-46d0-b911-ea902da88fc4/ralph-state.json
 D .omc/state/sessions/8ed3cf76-3602-46d0-b911-ea902da88fc4/ultrawork-state.json
 D .omc/state/sessions/cde1fa47-e2a9-4dd5-9863-a30ba177ecec/ralph-state.json
 D .omc/state/sessions/cde1fa47-e2a9-4dd5-9863-a30ba177ecec/ultrawork-state.json
 D .omc/state/sessions/d1dfbee9-40b6-41a8-aa82-06f508cfb187/ralph-state.json
 D .omc/state/sessions/d1dfbee9-40b6-41a8-aa82-06f508cfb187/ultrawork-state.json
 M .omc/state/subagent-tracking.json
 M config/engine.json
 M config/strategy_activation.json
 M src/core/real_signal_producer.py
 M src/modes/live.py
?? ../.claude/position-state-mapping.md
?? ../.claude/scheduled_tasks.lock
exec
/bin/zsh -lc "rg -n \"krw_rate_provider|_fx_provider|_krw_usdt_rate|xe_krw_enabled|get_rate\\(\" src engine.json" in /Users/100aniv/Development/arbitrage_OMC/engine exited 2 in 0ms:
rg: engine.json: No such file or directory (os error 2)
src/core/real_signal_producer.py:139:        self._fx_provider: KRWRateProvider = KRWRateProvider(fallback_rate=_fallback)
src/core/real_signal_producer.py:246:        # Enable via engine.json strategy_filters.xe_krw_enabled=true once FX oracle
src/core/real_signal_producer.py:248:        _xe_krw_enabled = bool(get_config("strategy_filters.xe_krw_enabled", default=False))
src/core/real_signal_producer.py:249:        if (self._backtest_mode or _xe_krw_enabled) and exchange_id in KRW_EXCHANGES and symbol.endswith("/KRW"):
src/core/real_signal_producer.py:998:        _fx_rate = self._fx_provider.get_rate()
src/modes/live.py:349:                        _aio.ensure_future(self._real_signal_producer._fx_provider.start())
src/collectors/funding_rate_collector.py:95:    def get_rate(self, exchange: str, symbol: str) -> FundingRateEntry | None:
src/collectors/funding_rate_collector.py:108:        entry_a = self.get_rate(exchange_a, symbol)
src/collectors/funding_rate_collector.py:109:        entry_b = self.get_rate(exchange_b, symbol)
src/collectors/funding_rate_collector.py:134:        entry = collector.store.get_rate("binance_futures", "BTC/USDT")
src/infra/fx/krw_rate_provider.py:83:    def get_rate(self) -> Decimal:
src/infra/fx/__init__.py:1:from src.infra.fx.krw_rate_provider import KRWRateProvider
codex
I’ve confirmed the touch points. Next I’m reading the provider and the affected producer/live paths to check whether startup ordering, stale fallback, and exception behavior can break signal generation.
exec
/bin/zsh -lc "sed -n '1,220p' src/infra/fx/krw_rate_provider.py" in /Users/100aniv/Development/arbitrage_OMC/engine succeeded in 1ms:
"""KRW/USDT live FX rate provider — polls Upbit ticker every 30s.

Replaces hardcoded `_DEFAULT_KRW_TO_USDT_RATE` in real_signal_producer.
Fallback: engine.json `strategy_filters.krw_usdt_rate` if Upbit unavailable.

Source: Upbit KRW-USDT trade_price (most liquid KRW<->USDT pair in KR market).
Staleness: 60s max (2 poll intervals). Beyond that, fallback to config.
"""
from __future__ import annotations

import asyncio
import logging
import time
from decimal import Decimal
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

exec
/bin/zsh -lc "sed -n '120,320p' src/core/real_signal_producer.py" in /Users/100aniv/Development/arbitrage_OMC/engine succeeded in 0ms:
        self._spread_history: dict[tuple[str, str, str], deque[float]] = defaultdict(
            lambda: deque(maxlen=200)
        )
        # Per-pair Kalman filters for dynamic hedge ratio estimation
        self._stat_arb_kalman: dict[tuple[str, str, str], _KalmanHedgeRatio] = {}
        self._stat_arb_cooldown: dict[tuple[str, str, str], float] = {}
        from src.core.config_loader import get_config
        # In backtest mode: use lower z-threshold (OHLCV hourly data is smoother than tick data,
        # so z-scores are systematically lower; 1.5 is appropriate for hourly backtest data)
        self._backtest_mode = backtest_mode
        self._stat_arb_z_threshold = float(get_config("strategy_filters.stat_arb_z_threshold", default=2.5))
        if backtest_mode:
            # Cap at 1.5 regardless of config — OHLCV hourly prices have less intraday noise
            # than live tick data, so live-tuned thresholds (2.0+) rarely trigger in backtest
            self._stat_arb_z_threshold = min(self._stat_arb_z_threshold, 1.5)
        self._stat_arb_cooldown_s = float(get_config("strategy_filters.stat_arb_cooldown_s", default=300))
        # BUG-108 → BUG-112: FX rate from live Upbit oracle (fallback to config)
        from src.infra.fx import KRWRateProvider
        _fallback = Decimal(str(get_config("strategy_filters.krw_usdt_rate", default=0.000676)))
        self._fx_provider: KRWRateProvider = KRWRateProvider(fallback_rate=_fallback)
exec
/bin/zsh -lc "sed -n '320,390p' src/modes/live.py" in /Users/100aniv/Development/arbitrage_OMC/engineexec
/bin/zsh -lc "sed -n '960,1045p' src/core/real_signal_producer.py" in /Users/100aniv/Development/arbitrage_OMC/engine succeeded in 15ms:
                latency_diff_ms=latency_diff_ms,
            )
            if sig is not None:
                logger.info(
                    "real_signal_producer.latency_arb_signal",
                    extra={"symbol": symbol, "fast_ex": fast_ex, "slow_ex": slow_ex,
                           "latency_diff_ms": f"{latency_diff_ms:.1f}"},
                )
                signals.append(sig)
        return signals

    async def _evaluate_cross_exchange_krw(
        self,
        krw_exchange: str,
        krw_symbol: str,
        krw_book: "OrderBook",
        all_books: "_Books",
        simulated_ts: float | None = None,
    ) -> list[Signal]:
        """KRW↔USDT cross-exchange 차익 평가 (backtest 전용, K-BT-10~12).
 succeeded in 20ms:
        if self._multi_signal_producer is not None:
            try:
                from src.core.real_signal_producer import RealDataSignalProducer
                from src.core.triangular_scanner import TriangularScanner
                from src.core.latency_tracker import LatencyTracker

                self._latency_tracker = LatencyTracker()
                self._stale_detector = None
                try:
                    from src.core.stale_detector import StaleOrderbookDetector
                    self._stale_detector = StaleOrderbookDetector()
                except Exception:
                    pass

                self._real_signal_producer = RealDataSignalProducer(
                    multi_signal_producer=self._multi_signal_producer,
                    triangular_scanner=TriangularScanner(),
                    futures_exchanges=self._futures_exchanges,
                    latency_tracker=self._latency_tracker,
                    stale_detector=self._stale_detector,
exec
/bin/zsh -lc "sed -n '1045,1125p' src/core/real_signal_producer.py" in /Users/100aniv/Development/arbitrage_OMC/engine succeeded in 0ms:
                        "krw_symbol": krw_symbol,
                    },
                )
                logger.info(
                    "real_signal_producer.cross_krw_signal",
                    extra={
                        "usdt_ex": usdt_exchange, "krw_ex": krw_exchange,
                        "symbol": usdt_symbol, "spread_bps": f"{spread_bps:.1f}",
                        "direction": "sell_krw",
                    },
                )
                signals.append(sig)

            # 방향 2: USDT 거래소가 더 비쌈 → USDT 거래소에서 팔고 KRW 거래소에서 삼
            if float(usdt_bid) > float(krw_ask_usdt):
                spread_bps = (float(usdt_bid) - float(krw_ask_usdt)) / float(krw_ask_usdt) * 10000
                if spread_bps <= 0 or spread_bps > 500:
                    continue
                _ce_key2 = (usdt_symbol, krw_exchange, usdt_exchange)
                _ce_hist2 = self._rolling_spread[_ce_key2]
codex
The provider itself is straightforward. I’m checking the remaining call sites and config path to see whether any live path still assumes the old scalar or can call `get_rate()` before the poller ever starts.
exec
/bin/zsh -lc "sed -n '1,120p' src/infra/fx/__init__.py" in /Users/100aniv/Development/arbitrage_OMC/engine succeeded in 0ms:
from src.infra.fx.krw_rate_provider import KRWRateProvider

__all__ = ["KRWRateProvider"]
exec
/bin/zsh -lc "rg -n \"_fx_provider\\.get_rate\\(|_krw_usdt_rate|krw_usdt_rate|xe_krw_enabled\" src config" in /Users/100aniv/Development/arbitrage_OMC/engine succeeded in 0ms:
config/strategy_activation.json:36:  "_step_2_3_note": "cross_exchange_v1 active but XE-KRW (Upbit/Bithumb/Coinone) gated by engine.json strategy_filters.xe_krw_enabled=false. Current Step 2-3 supports only global USDT XE (Binance<->Bitget spot). KRW activation sequence: 2-4 Coinone -> 2-5 Upbit -> 2-6 Bithumb.",
config/engine.json:54:    "krw_usdt_rate": 0.000676,
config/engine.json:55:    "xe_krw_enabled": false,
src/modes/shadow.py:520:        _raw_krw_rate = get_settings().operational.krw_usdt_rate
src/modes/live.py:394:        _raw_krw_rate = get_settings().operational.krw_usdt_rate
src/core/real_signal_producer.py:138:        _fallback = Decimal(str(get_config("strategy_filters.krw_usdt_rate", default=0.000676)))
src/core/real_signal_producer.py:246:        # Enable via engine.json strategy_filters.xe_krw_enabled=true once FX oracle
src/core/real_signal_producer.py:249:        _xe_krw_enabled = bool(_gc("strategy_filters.xe_krw_enabled", default=False))
src/core/real_signal_producer.py:250:        if (self._backtest_mode or _xe_krw_enabled) and exchange_id in KRW_EXCHANGES and symbol.endswith("/KRW"):
src/core/real_signal_producer.py:999:        _fx_rate = self._fx_provider.get_rate()
src/core/config.py:463:    krw_usdt_rate: float = Field(default=1380.0, alias="KRW_USDT_RATE")
src/infra/fx/krw_rate_provider.py:4:Fallback: engine.json `strategy_filters.krw_usdt_rate` if Upbit unavailable.
exec
/bin/zsh -lc 'git diff -- src/core/real_signal_producer.py src/modes/live.py src/infra/fx/krw_rate_provider.py config/engine.json' in /Users/100aniv/Development/arbitrage_OMC/engine succeeded in 0ms:
diff --git a/engine/config/engine.json b/engine/config/engine.json
index 9c5f28e..83ee950 100644
--- a/engine/config/engine.json
+++ b/engine/config/engine.json
@@ -52,6 +52,7 @@
   },
   "strategy_filters": {
     "krw_usdt_rate": 0.000676,
+    "xe_krw_enabled": false,
     "enable_holding_timeout": true,
     "spot_futures_max_hold_seconds": 1800,
     "futures_max_hold_seconds": 1800,
diff --git a/engine/src/core/real_signal_producer.py b/engine/src/core/real_signal_producer.py
index ddbaea0..8708d2d 100644
--- a/engine/src/core/real_signal_producer.py
+++ b/engine/src/core/real_signal_producer.py
@@ -133,9 +133,10 @@ class RealDataSignalProducer:
             # than live tick data, so live-tuned thresholds (2.0+) rarely trigger in backtest
             self._stat_arb_z_threshold = min(self._stat_arb_z_threshold, 1.5)
         self._stat_arb_cooldown_s = float(get_config("strategy_filters.stat_arb_cooldown_s", default=300))
codex
The main thing left is startup semantics. I’m checking whether `LiveMode.__init__` runs before the event loop is active, because that would silently leave the provider on fallback forever.
exec
/bin/zsh -lc "nl -ba src/modes/live.py | sed -n '300,370p'" in /Users/100aniv/Development/arbitrage_OMC/engine succeeded in 0ms:
   300	                    partial_fill_rate=pfr,
   301	                    rejection_rate=rr,
   302	                )
   303	                logger.info(
   304	                    "live_mode.book_walk_slippage_wired execution_mode=paper "
   305	                    "executor=PaperExecutor(BookWalkSlippage)"
   306	                )
   307	            except Exception as exc:
   308	                logger.warning(
   309	                    "live_mode.book_walk_slippage_wire_failed (non-fatal): %s — "
   310	                    "keeping original executor",
   311	                    exc,
   312	                )
   313	
   314	        # Futures exchanges for identification — dynamic from FUTURES_TO_SPOT SSOT
   315	        from src.core.exchanges import FUTURES_TO_SPOT
   316	        self._futures_exchanges: set[str] = set(FUTURES_TO_SPOT.keys())
   317	
   318	        # RealDataSignalProducer (spot_futures, funding_rate, stat_arb, etc.)
   319	        self._real_signal_producer: Any | None = None
exec
/bin/zsh -lc "rg -n \"class LiveMode|async def start|def start\\(|run_forever|asyncio.run|_real_signal_producer\" src/modes/live.py src -g '"'!src/core/real_signal_producer.py'"'" in /Users/100aniv/Development/arbitrage_OMC/engine succeeded in 0ms:
src/main.py:4099:        asyncio.run(main())
src/bot_gateway.py:127:    asyncio.run(main())
src/core/live_gate_continuous.py:62:    async def start(self) -> None:
src/modes/base.py:51:    async def start(self) -> None:  # pragma: no cover
src/core/multi_signal.py:525:    async def start(self) -> None:
src/modes/backtest.py:120:        self._real_signal_producer: Any | None = None
src/modes/backtest.py:123:            self._real_signal_producer = RealDataSignalProducer(
src/modes/backtest.py:431:        if self._real_signal_producer is not None:
src/modes/backtest.py:441:                signals = await self._real_signal_producer.on_orderbook_update(
src/modes/shadow.py:564:        self._real_signal_producer: RealDataSignalProducer | None = None
src/modes/shadow.py:566:            self._real_signal_producer = RealDataSignalProducer(
src/modes/shadow.py:648:    async def start(self) -> None:
src/modes/shadow.py:1101:        if self._real_signal_producer is None:
src/modes/shadow.py:1108:        signals = await self._real_signal_producer.on_orderbook_update(
src/modes/shadow.py:1227:                if self._real_signal_producer is not None:
src/modes/shadow.py:1229:                        signals = await self._real_signal_producer.on_funding_rates_updated(
src/modes/live.py:104:class LiveModeStats:
src/modes/live.py:170:class LiveMode(BaseMode):
src/modes/live.py:319:        self._real_signal_producer: Any | None = None
src/modes/live.py:334:                self._real_signal_producer = RealDataSignalProducer(
exec
/bin/zsh -lc "nl -ba src/core/real_signal_producer.py | sed -n '236,255p; 992,1005p'" in /Users/100aniv/Development/arbitrage_OMC/engineexec
/bin/zsh -lc "nl -ba src/infra/fx/krw_rate_provider.py | sed -n '1,140p'" in /Users/100aniv/Development/arbitrage_OMC/engine succeeded in 0ms:
   236	            )
   237	
   238	        # Futures-futures spread
   239	        signals.extend(
   240	            await self._evaluate_futures_futures(symbol, futures_books)
   241	        )
   242	
   243	        # Cross-exchange KRW↔USDT arb (Kimchi premium)
   244	        # PHOENIX Step 2-4~2-6: staged activation per-exchange. Default DISABLED in live
   245	        # to prevent bulk activation of Upbit+Bithumb+Coinone at once (troubleshooting).
   246	        # Enable via engine.json strategy_filters.xe_krw_enabled=true once FX oracle
   247	        # + per-exchange KYC/TOS checks complete.
   248	        from src.core.config_loader import get_config as _gc
   249	        _xe_krw_enabled = bool(_gc("strategy_filters.xe_krw_enabled", default=False))
   250	        if (self._backtest_mode or _xe_krw_enabled) and exchange_id in KRW_EXCHANGES and symbol.endswith("/KRW"):
   251	            signals.extend(
   252	                await self._evaluate_cross_exchange_krw(
   253	                    exchange_id, symbol, book, all_books, simulated_ts=simulated_ts
   254	                )
   255	            )
 succeeded in 0ms:
     1	"""KRW/USDT live FX rate provider — polls Upbit ticker every 30s.
     2	
     3	Replaces hardcoded `_DEFAULT_KRW_TO_USDT_RATE` in real_signal_producer.
     4	Fallback: engine.json `strategy_filters.krw_usdt_rate` if Upbit unavailable.
     5	
     6	Source: Upbit KRW-USDT trade_price (most liquid KRW<->USDT pair in KR market).
     7	Staleness: 60s max (2 poll intervals). Beyond that, fallback to config.
     8	"""
     9	from __future__ import annotations
    10	
    11	import asyncio
    12	import logging
    13	import time
    14	from decimal import Decimal
    15	from typing import Optional
    16	
    17	import httpx
    18	
    19	logger = logging.getLogger(__name__)
    20	
exec
/bin/zsh -lc "nl -ba src/modes/live.py | sed -n '446,540p'" in /Users/100aniv/Development/arbitrage_OMC/engine succeeded in 0ms:
   446	    async def start(self) -> None:
   447	        """Start live mode: LiveGate check → strategies → collectors → background tasks."""
   448	        if self._running:
   449	            logger.warning("live_mode.already_running")
   450	            return
   451	
   452	        # Step 1: LiveGate enforcement (if available)
   453	        if self._live_gate is not None:
   454	            try:
   455	                from src.modes.live_gate import LiveGate
   456	                if isinstance(self._live_gate, LiveGate):
   457	                    eligible = await self._live_gate.enforce_or_fallback()
   458	                    if not eligible:
   459	                        logger.warning("live_mode.live_gate_failed — raising LiveGateFailed")
   460	                        raise LiveGateFailed("LiveGate enforcement failed")
   461	            except LiveGateFailed:
   462	                raise
   463	            except Exception as exc:
   464	                logger.warning("live_mode.live_gate_error: %s — raising LiveGateFailed", exc)
   465	                raise LiveGateFailed(f"LiveGate error: {exc}") from exc
exec
/bin/zsh -lc "rg -n \"_fx_provider\\.stop|stop\\(self\\).*real_signal_producer|async def stop|def stop\\(\" src/modes/live.py src/core/real_signal_producer.py src/infra/fx/krw_rate_provider.py" in /Users/100aniv/Development/arbitrage_OMC/engine succeeded in 0ms:
src/infra/fx/krw_rate_provider.py:44:    async def stop(self) -> None:
src/modes/live.py:845:    async def stop(self) -> None:
exec
/bin/zsh -lc "nl -ba src/modes/live.py | sed -n '845,930p'" in /Users/100aniv/Development/arbitrage_OMC/engine succeeded in 0ms:
   845	    async def stop(self) -> None:
   846	        """Stop live mode gracefully."""
   847	        if not self._running:
   848	            return
   849	
   850	        self._running = False
   851	        logger.info("live_mode.stopping")
   852	
   853	        # Cancel background tasks (BUG-87: include dedup/reconciler/margin tasks)
   854	        for task in [
   855	            self._daily_task, self._funding_rate_task, self._krw_rate_task,
   856	            self._dedup_cleanup_task, self._trade_reconciler_task, self._margin_refresh_task,
   857	        ]:
   858	            if task is not None and not task.done():
   859	                task.cancel()
   860	                try:
   861	                    await task
   862	                except asyncio.CancelledError:
   863	                    pass
   864	
exec
/bin/zsh -lc "nl -ba src/modes/live.py | sed -n '388,445p'" in /Users/100aniv/Development/arbitrage_OMC/engine succeeded in 0ms:
   388	        # Symbol window: recently-seen symbols with expiry timestamps (20-minute TTL).
   389	        # Ensures recently-closed positions are still reconciled even after _open_positions
   390	        # is cleared — prevents blind spot where reconciler reports "all clear" on empty symbols.
   391	        self._recon_symbol_window: dict[str, float] = {}  # symbol → last_seen_epoch
   392	
   393	        # KRW/USDT normalization (ported from ShadowMode)
   394	        _raw_krw_rate = get_settings().operational.krw_usdt_rate
   395	        if _raw_krw_rate <= 0:
   396	            _raw_krw_rate = 1380.0
   397	        self._krw_rate: float = _raw_krw_rate
   398	        self._krw_rate_task: asyncio.Task | None = None
   399	        self._krw_stale: bool = False
   400	        self._krw_fail_count: int = 0  # BUG-88: require 5 consecutive failures before marking stale
   401	        self._krw_exchanges: set[str] = set(KRW_EXCHANGES)
   402	
   403	        # Bithumb delta orderbook handling
   404	        self._delta_exchanges: set[str] = {"bithumb"}
   405	
   406	        # Event-loop yield counter (every N updates, yield to prevent starvation)
   407	        self._ob_counter: int = 0
exec
/bin/zsh -lc "rg -n \"_krw_rate_task|_krw_rate|_krw_stale|KRW\" src/modes/live.py | sed -n '1,120p'" in /Users/100aniv/Development/arbitrage_OMC/engine succeeded in 0ms:
33:from src.core.exchanges import KRW_EXCHANGES
342:                # BUG-112: start KRWRateProvider live polling (Upbit 30s).
393:        # KRW/USDT normalization (ported from ShadowMode)
394:        _raw_krw_rate = get_settings().operational.krw_usdt_rate
395:        if _raw_krw_rate <= 0:
396:            _raw_krw_rate = 1380.0
397:        self._krw_rate: float = _raw_krw_rate
398:        self._krw_rate_task: asyncio.Task | None = None
399:        self._krw_stale: bool = False
401:        self._krw_exchanges: set[str] = set(KRW_EXCHANGES)
663:        # Step 4: Start KRW rate updater
664:        self._krw_rate_task = asyncio.create_task(
665:            self._krw_rate_loop(), name="live_krw_rate"
855:            self._daily_task, self._funding_rate_task, self._krw_rate_task,
923:        # KRW normalization: convert KRW prices to USDT
925:        if ex_base in self._krw_exchanges and "/KRW" in symbol:
926:            if self._krw_stale or self._krw_rate <= 0:
929:            rate = self._krw_rate
932:            symbol = symbol.replace("/KRW", "/USDT")
2339:    # KRW rate loop (ported from ShadowMode)
exec
/bin/zsh -lc "nl -ba src/modes/live.py | sed -n '2339,2378p'" in /Users/100aniv/Development/arbitrage_OMC/engine succeeded in 0ms:
  2339	    # KRW rate loop (ported from ShadowMode)
  2340	    # -----------------------------------------------------------------------
  2341	
  2342	    async def _krw_rate_loop(self) -> None:
  2343	        """Fetch KRW/USDT rate from Upbit every 60s."""
  2344	        import httpx
  2345	
  2346	        try:
  2347	            async with httpx.AsyncClient(timeout=5.0) as client:
  2348	                while self._running:
  2349	                    try:
  2350	                        resp = await client.get(
  2351	                            "https://api.upbit.com/v1/ticker?markets=USDT-KRW"
  2352	                        )
  2353	                        if resp.status_code == 200:
  2354	                            data = resp.json()
  2355	                            if data and isinstance(data, list) and "trade_price" in data[0]:
  2356	                                new_rate = float(data[0]["trade_price"])
  2357	                                if new_rate > 0:
  2358	                                    self._krw_rate = new_rate
exec
/bin/zsh -lc "nl -ba src/modes/live.py | sed -n '996,1035p'" in /Users/100aniv/Development/arbitrage_OMC/engine succeeded in 0ms:
   996	                )
   997	                if signal is not None:
   998	                    if self._strategy_manager is not None:
   999	                        await self._route_signal_to_strategies(signal)
  1000	                    else:
  1001	                        await self._execute_direct_signal(signal)
  1002	            except Exception as exc:
  1003	                logger.warning("live_mode.signal_generator_error: %s", exc)
  1004	
  1005	        # --- RealDataSignalProducer (spot_futures, funding_rate, stat_arb, etc.) ---
  1006	        if self._real_signal_producer is not None:
  1007	            try:
  1008	                # BUG-100: all_books must be full {symbol: {exchange: book}} dict
  1009	                # (real_signal_producer internally does all_books.get(symbol, {}) again,
  1010	                # so passing pre-indexed {exchange: book} yielded empty spot_books → SF 0 signals).
  1011	                # BUG-103: shallow copy to prevent "dict changed size during iteration"
  1012	                # (collectors concurrently mutate self._books).
  1013	                _books_snapshot = dict(self._books)
  1014	                signals = await self._real_signal_producer.on_orderbook_update(
  1015	                    exchange_id=exchange_id,
codex
Findings:

1. `LiveMode` starts the FX poller only opportunistically in `__init__` if an event loop is already running ([live.py](/Users/100aniv/Development/arbitrage_OMC/engine/src/modes/live.py:342)). If `LiveMode` is constructed off-loop, `KRWRateProvider.start()` is never called, and `get_rate()` stays on fallback forever ([krw_rate_provider.py](/Users/100aniv/Development/arbitrage_OMC/engine/src/infra/fx/krw_rate_provider.py:85)). Move startup to `async start()`.

2. No shutdown wiring: `LiveMode.stop()` cancels `_krw_rate_task` but never awaits `_real_signal_producer._fx_provider.stop()` ([live.py](/Users/100aniv/Development/arbitrage_OMC/engine/src/modes/live.py:845)). If started, that background poll task can leak across mode restarts/tests.
tokens used
34,727

```

## Concise summary

Provider completed successfully. Review the raw output for details.

## Action items

- Review the response and extract decisions you want to apply.
- Capture follow-up implementation tasks if needed.
