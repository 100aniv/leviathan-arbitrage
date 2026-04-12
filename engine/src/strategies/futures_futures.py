"""Futures-Futures Cross strategy (CEX-CEX).

Price discrepancy between the same futures contract on two exchanges.
Similar to cross-exchange spot but operates on leveraged futures positions.

US-233: Tighter parameters — min_spread_bps=15, min_book_depth_usd=500, max_notional_usd=200.
"""
from __future__ import annotations

import logging
import os
import time
from decimal import Decimal
from typing import Any, Optional

logger = logging.getLogger(__name__)

from pydantic import BaseModel, Field

from src.core.exchanges import KRW_EXCHANGES
from src.core.models import OrderSide, OrderType, Signal, Trade
from src.strategies.base import BaseStrategy, CostCalculator, TradeLeg, TradeRequest


class FuturesFuturesConfig(BaseModel):
    """Configuration for FuturesFuturesStrategy."""

    min_spread_bps: Decimal = Field(default=Decimal("15"), ge=Decimal("0"))
    max_position_size: Decimal = Field(default=Decimal("50000"), gt=Decimal("0"))  # USD notional cap
    max_leverage: int = Field(default=5, ge=1, le=20)
    margin_safety_pct: Decimal = Field(default=Decimal("0.20"), ge=Decimal("0"))
    max_notional_usd: Decimal | None = Field(default=None)  # deprecated: use max_position_size (%-based)
    min_book_depth_usd: Decimal = Field(default=Decimal("500"), ge=Decimal("0"))  # US-233
    # US-272: Funding convergence combined signal
    funding_convergence_weight: Decimal = Field(default=Decimal("0.3"), ge=Decimal("0"), le=Decimal("1"))
    enable_funding_convergence: bool = Field(default=True)
    # US-273: Stale guard
    max_book_age_seconds: float = Field(default=5.0, gt=0)
    enable_stale_guard: bool = Field(default=False)  # book_age_ms 신호 미지원 — 활성화 전 signal producer 수정 필요
    # CB: KRW spot-only exchanges don't support futures contracts — exclude by default
    excluded_exchanges: list[str] = Field(
        default_factory=lambda: sorted(KRW_EXCHANGES)
    )
    # Bug 26: Separate baseline for AdaptiveThreshold outlier filter (independent of min_spread_bps)
    # min_spread_bps=150 (latency-adjusted trade floor) vs adaptive_static_entry_bps=50 (realistic spread baseline)
    adaptive_static_entry_bps: Decimal | None = Field(default=None)
    # PHOENIX: Per-run symbol exclusion (e.g. stranded position symbols)
    excluded_symbols: list[str] = Field(default_factory=list)
    # Exit: close positions older than max_hold_seconds (0 = disabled)
    max_hold_seconds: float = Field(default=1800.0, ge=0)  # 30min default (was 4H)
    # BUG-72: Max simultaneous open positions to prevent Binance -2019 margin exhaustion.
    # With $120 capital at 5x leverage, each ~$12 trade uses ~$2.5 margin.
    # 4 positions = $10 margin = 8.3% of capital — safe headroom.
    max_concurrent_positions: int = Field(default=4, ge=1, le=50)


class FuturesFuturesStrategy(BaseStrategy):
    """
    Futures-Futures Cross-Exchange Arbitrage.

    Buys futures cheap on one exchange, sells expensive on another.
    Applies leverage cap and margin safety buffer checks.

    signal.metadata may contain:
      - 'margin_available': float  (USDT available as margin)
    """

    STRATEGY_TYPE = "futures_futures"

    def __init__(
        self,
        strategy_id: str,
        cost_calculator: CostCalculator,
        config: FuturesFuturesConfig | None = None,
        regime_detector: Any = None,
    ) -> None:
        super().__init__(strategy_id, cost_calculator)
        self._regime_detector = regime_detector
        if config is None:
            from src.core.config_loader import get_config
            _at_bps_raw = get_config("strategy_filters.futures_adaptive_static_entry_bps", default=None)
            config = FuturesFuturesConfig(
                min_spread_bps=Decimal(str(get_config("strategy_filters.futures_min_spread_bps", default=15))),
                min_book_depth_usd=Decimal(str(get_config("strategy_filters.futures_min_book_depth_usd", default=500))),
                funding_convergence_weight=Decimal(str(get_config("strategy_filters.funding_convergence_weight", default=0.3))),
                enable_funding_convergence=get_config("strategy_filters.enable_funding_convergence", default=True),
                max_book_age_seconds=float(get_config("strategy_filters.futures_max_book_age_s", default=5.0)),
                enable_stale_guard=get_config("strategy_filters.enable_stale_guard", default=False),
                adaptive_static_entry_bps=Decimal(str(_at_bps_raw)) if _at_bps_raw is not None else None,
                excluded_symbols=list(get_config("strategy_filters.futures_excluded_symbols", default=[])),
                max_hold_seconds=float(get_config("strategy_filters.futures_max_hold_seconds", default=1800)),
                max_concurrent_positions=int(get_config("strategy_filters.futures_max_concurrent_positions", default=4)),
            )
        self.config = config
        self._margin_tracker: Any | None = None  # injected by live.py
        # Position tracking for time-based exit: symbol → {buy_ex, sell_ex, size, entry_time}
        self._open_positions: dict[str, dict] = {}
        # MAJOR-1 race guard: symbols with in-flight on_signal() calls (between check and write).
        # Prevents concurrent coroutines from both passing _open_positions check before either writes.
        self._pending_entry_symbols: set[str] = set()
        # PHOENIX v18: Pending exit TradeRequests from _open_positions_monitor
        self._pending_exit_requests: list = []
        self._monitor_task: "asyncio.Task | None" = None  # stored ref prevents GC / allows cancel
        # BUG-62: snapshot of positions currently being exited → restore on rollback
        self._pending_exits: dict[str, dict] = {}
        # BUG-CRITICAL: exit race guard — prevents duplicate exit from both monitor + on_signal()
        # Both paths independently check _open_positions; this set ensures only one wins.
        self._exiting_symbols: set[str] = set()

        # US-260: Adaptive threshold — rolling percentile + volatility weight
        # Bug 26: Use adaptive_static_entry_bps (if set) as the outlier-filter baseline,
        # independent of min_spread_bps (latency-adjusted trade floor).
        try:
            from src.core.adaptive_threshold import AdaptiveThreshold
            _at_static = (
                float(config.adaptive_static_entry_bps)
                if config.adaptive_static_entry_bps is not None
                else float(config.min_spread_bps)
            )
            self._adaptive_threshold = AdaptiveThreshold(
                window=1440,
                entry_percentile=95.0,
                exit_percentile=50.0,
                static_entry=_at_static,
                static_exit=_at_static * 0.5,
            )
        except ImportError:
            self._adaptive_threshold = None

    def set_margin_tracker(self, tracker: Any) -> None:
        """Inject MarginTracker (called by live.py after strategy init)."""
        self._margin_tracker = tracker

    async def start(self) -> None:
        """전략 시작 + 포지션 시간 모니터 태스크."""
        await super().start()
        if self.config.max_hold_seconds > 0:
            import asyncio as _asyncio
            self._monitor_task = _asyncio.create_task(
                self._open_positions_monitor(),
                name="ff_position_monitor",
            )

    async def stop(self) -> None:
        """전략 중지 + 모니터 태스크 명시적 취소."""
        await super().stop()  # sets _is_active = False
        if self._monitor_task is not None and not self._monitor_task.done():
            import asyncio as _asyncio
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except _asyncio.CancelledError:
                pass
            logger.info("ff.monitor_task_cancelled")

    def on_execution_rollback(self, symbol: str) -> None:
        """실행 롤백 완료 시 처리 (BUG-J + BUG-62).

        ENTRY rollback (ROLLED_BACK): leg2 실패 후 leg1 언와인드 성공 → 실제 포지션 없음 → _open_positions 제거.
        EXIT rollback (ROLLED_BACK): exit 실패 후 partial fill 언와인드 성공 → 포지션 복원됨 → _pending_exits에서 복원.
        ROLLBACK_FAILED: 호출하지 않음 (stranded position 존재).
        """
        self._exiting_symbols.discard(symbol)
        if symbol in self._pending_exits:
            # Exit order rolled back → original position restored on exchange → re-track
            restored = self._pending_exits.pop(symbol)
            self._open_positions[symbol] = restored
            logger.info("ff.position_restored_on_exit_rollback symbol=%s", symbol)
        elif symbol in self._open_positions:
            # Entry order rolled back → position was never actually opened → clear tracking
            logger.info("ff.position_cleared_on_rollback symbol=%s", symbol)
            self._open_positions.pop(symbol, None)

    def on_execution_success(self, symbol: str) -> None:
        """성공적 실행 완료 시 _pending_exits 정리 (BUG-80).

        EXIT success: 실제 포지션이 거래소에서 청산됨 → _pending_exits 스냅샷 삭제.
        ENTRY success: _pending_exits에 해당 심볼 없음 → no-op.
        """
        self._exiting_symbols.discard(symbol)
        if symbol in self._pending_exits:
            self._pending_exits.pop(symbol)
            logger.debug("ff.pending_exits_cleared_on_success symbol=%s", symbol)

    async def _open_positions_monitor(self) -> None:
        """60초마다 _open_positions 점검 — max_hold_seconds 초과 또는 spread 수렴 시 exit TradeRequest 생성."""
        import asyncio as _asyncio
        while self._is_active:
            try:
                await _asyncio.sleep(60)
            except _asyncio.CancelledError:
                return
            now = time.time()
            for sym, pos in list(self._open_positions.items()):
                try:
                    age_s = now - pos["entry_time"]

                    # Spread-reversion exit from monitor (uses last stored spread from on_signal)
                    _exit_threshold_bps: float = float(self.config.min_spread_bps) * 0.5
                    last_spread = pos.get("last_spread_bps")
                    if last_spread is not None and last_spread <= _exit_threshold_bps:
                        if sym in self._exiting_symbols:
                            continue  # BUG-CRITICAL: on_signal() already claimed this exit
                        self._exiting_symbols.add(sym)
                        logger.info(
                            "ff.spread_exit_monitor symbol=%s last_spread_bps=%.2f exit_threshold_bps=%.2f age_s=%.0f — 스프레드 수렴 청산",
                            sym, last_spread, _exit_threshold_bps, age_s,
                        )
                        exit_req = TradeRequest(
                            strategy_id=self.strategy_id,
                            legs=[
                                TradeLeg(
                                    exchange_id=pos["buy_ex"],
                                    symbol=sym,
                                    side=OrderSide.SELL,
                                    size=pos["size"],
                                    order_type=OrderType.MARKET,
                                    price=None,
                                    metadata={"leg_type": "spread_exit_close_long", "reduceOnly": True},
                                ),
                                TradeLeg(
                                    exchange_id=pos["sell_ex"],
                                    symbol=sym,
                                    side=OrderSide.BUY,
                                    size=pos["size"],
                                    order_type=OrderType.MARKET,
                                    price=None,
                                    metadata={"leg_type": "spread_exit_close_short", "reduceOnly": True},
                                ),
                            ],
                            expected_profit_usdt=Decimal("0"),
                            confidence=1.0,
                            metadata={"reason": "spread_reversion_monitor", "spread_bps": str(round(last_spread, 2)), "age_s": str(int(age_s))},
                        )
                        self._pending_exit_requests.append(exit_req)
                        # BUG-62: save snapshot before removing — restored if exit rolls back
                        self._pending_exits[sym] = dict(pos)
                        self._open_positions.pop(sym, None)
                        continue

                    if age_s > self.config.max_hold_seconds:
                        if sym in self._exiting_symbols:
                            continue  # BUG-CRITICAL: on_signal() already claimed this exit
                        self._exiting_symbols.add(sym)
                        logger.warning(
                            "ff.stale_position symbol=%s age_s=%.0f max_hold_s=%.0f "
                            "— exit TradeRequest 생성",
                            sym, age_s, self.config.max_hold_seconds,
                        )
                        exit_req = TradeRequest(
                            strategy_id=self.strategy_id,
                            legs=[
                                TradeLeg(
                                    exchange_id=pos["buy_ex"],
                                    symbol=sym,
                                    side=OrderSide.SELL,
                                    size=pos["size"],
                                    order_type=OrderType.MARKET,
                                    price=None,
                                    metadata={"leg_type": "time_exit_close_long", "reduceOnly": True},
                                ),
                                TradeLeg(
                                    exchange_id=pos["sell_ex"],
                                    symbol=sym,
                                    side=OrderSide.BUY,
                                    size=pos["size"],
                                    order_type=OrderType.MARKET,
                                    price=None,
                                    metadata={"leg_type": "time_exit_close_short", "reduceOnly": True},
                                ),
                            ],
                            expected_profit_usdt=Decimal("0"),
                            confidence=0.0,
                            metadata={"reason": "holding_timeout", "age_s": str(int(age_s))},
                        )
                        self._pending_exit_requests.append(exit_req)
                        # BUG-62: save snapshot before removing — restored if exit rolls back
                        self._pending_exits[sym] = dict(pos)
                        # 중복 emit 방지: 모니터에서 즉시 제거
                        self._open_positions.pop(sym, None)
                except Exception:
                    logger.exception("ff.monitor_symbol_error symbol=%s — skipping this symbol", sym)

    def pop_exit_requests(self) -> list:
        """pending exit TradeRequests를 반환하고 목록 초기화."""
        reqs = list(self._pending_exit_requests)
        self._pending_exit_requests.clear()
        return reqs

    async def on_signal(self, signal: Signal) -> Optional[TradeRequest]:
        self._metrics.signals_received += 1

        if not self._is_active:
            self._metrics.signals_filtered += 1
            return None

        # US-273: Stale Guard — fail closed if book_age_ms missing or stale
        if self.config.enable_stale_guard:
            raw_book_age = signal.metadata.get("book_age_ms")
            if raw_book_age is None:
                logger.warning("missing book_age_ms, filtering signal")
                self._metrics.signals_filtered += 1
                return None
            book_age_ms = float(raw_book_age)
            if book_age_ms / 1000 > self.config.max_book_age_seconds:
                self._metrics.signals_filtered += 1
                return None

        # PHOENIX: Exclude specific symbols (e.g. stranded position symbols)
        if self.config.excluded_symbols and signal.symbol:
            _base = signal.symbol.split("/")[0].upper()
            if _base in {s.upper() for s in self.config.excluded_symbols}:
                self._metrics.signals_filtered += 1
                logger.debug(
                    "strategy.rejected strategy=futures_futures reason=excluded_symbol symbol=%s", signal.symbol
                )
                return None

        # CB: Exclude KRW spot-only exchanges (no futures market)
        if self.config.excluded_exchanges:
            for _ex in (signal.buy_exchange, signal.sell_exchange):
                if _ex and any(_ex == exc or _ex.startswith(exc) for exc in self.config.excluded_exchanges):
                    self._metrics.signals_filtered += 1
                    logger.debug(
                        "strategy.rejected strategy=futures_futures reason=excluded_exchange exchange=%s", _ex
                    )
                    return None

        # Bug 25a / Bug 31 prevention: same futures exchange on both legs.
        # Must compare *resolved* futures exchange IDs — "bitget" and "bitget_futures"
        # both resolve to "bitget_futures", so a raw-string comparison misses this case.
        def _to_futures_id(eid: str) -> str:
            return eid if eid.endswith("_futures") else f"{eid}_futures"

        _buy_futures_id = _to_futures_id(signal.buy_exchange) if signal.buy_exchange else None
        _sell_futures_id = _to_futures_id(signal.sell_exchange) if signal.sell_exchange else None
        if _buy_futures_id and _sell_futures_id and _buy_futures_id == _sell_futures_id:
            self._metrics.signals_filtered += 1
            logger.debug(
                "strategy.rejected strategy=futures_futures reason=same_exchange exchange=%s",
                _buy_futures_id,
            )
            return None

        # BUG-72: Enforce max concurrent positions limit to prevent Binance -2019 margin exhaustion.
        # With ~$12 per trade at 5x leverage, each position uses ~$2.5 margin.
        # Reject new entries once the limit is reached (existing positions stay open until exit).
        _cur_positions = len(self._open_positions) + len(self._pending_entry_symbols)
        if _cur_positions >= self.config.max_concurrent_positions:
            self._metrics.signals_filtered += 1
            logger.debug(
                "strategy.rejected strategy=futures_futures reason=max_concurrent_positions "
                "current=%d limit=%d symbol=%s",
                _cur_positions, self.config.max_concurrent_positions, signal.symbol,
            )
            return None

        # Time-based exit or entry block: if open position for this symbol exists, handle it
        _sym = signal.symbol or ""
        # MAJOR-1 race guard: reject if another on_signal() coroutine is already processing this
        # symbol (between its _open_positions check and its _open_positions write, across an await).
        if _sym and _sym in self._pending_entry_symbols:
            self._metrics.signals_filtered += 1
            logger.debug("strategy.rejected strategy=futures_futures reason=pending_entry symbol=%s", _sym)
            return None
        if _sym and _sym in self._open_positions:
            pos = self._open_positions[_sym]
            age_s = time.time() - pos["entry_time"]
            _current_spread_bps = float(signal.spread_pct) * 10000 if signal.spread_pct else None

            # --- Spread-reversion exit (PRIMARY exit) ---
            # Exit when spread closes back below exit threshold (profit locked in)
            _exit_threshold_bps: float = float(self.config.min_spread_bps) * 0.5
            if self._adaptive_threshold is not None and self._adaptive_threshold.is_ready:
                _, _at_exit = self._adaptive_threshold.thresholds  # (p95_entry, p50_exit)
                if _at_exit and _at_exit > 0:
                    _exit_threshold_bps = float(_at_exit)
            if _current_spread_bps is not None and _current_spread_bps <= _exit_threshold_bps:
                # BUG-CRITICAL: guard against duplicate exit if monitor already queued this symbol
                if _sym in self._exiting_symbols:
                    return None
                self._exiting_symbols.add(_sym)
                logger.info(
                    "ff.spread_exit symbol=%s spread_bps=%.2f exit_threshold_bps=%.2f age_s=%.0f — 스프레드 수렴 청산",
                    _sym, _current_spread_bps, _exit_threshold_bps, age_s,
                )
                # BUG-62: save snapshot before removing — restored if exit rolls back
                self._pending_exits[_sym] = dict(pos)
                del self._open_positions[_sym]
                self._metrics.trade_requests_generated += 1
                return TradeRequest(
                    strategy_id=self.strategy_id,
                    legs=[
                        TradeLeg(
                            exchange_id=pos["buy_ex"],
                            symbol=_sym,
                            side=OrderSide.SELL,
                            size=pos["size"],
                            order_type=OrderType.MARKET,
                            price=signal.buy_price,
                            metadata={"leg_type": "futures_close", "reduceOnly": True},
                        ),
                        TradeLeg(
                            exchange_id=pos["sell_ex"],
                            symbol=_sym,
                            side=OrderSide.BUY,
                            size=pos["size"],
                            order_type=OrderType.MARKET,
                            price=signal.sell_price,
                            metadata={"leg_type": "futures_close", "reduceOnly": True},
                        ),
                    ],
                    expected_profit_usdt=Decimal("0"),
                    confidence=1.0,
                    metadata={"close_reason": "spread_reversion", "spread_bps": str(round(_current_spread_bps, 2)), "age_s": str(int(age_s))},
                )

            # --- Time-based exit (SAFETY fallback) ---
            if self.config.max_hold_seconds > 0 and age_s > self.config.max_hold_seconds:
                # BUG-CRITICAL: guard against duplicate exit if monitor already queued this symbol
                if _sym in self._exiting_symbols:
                    return None
                self._exiting_symbols.add(_sym)
                logger.info(
                    "ff.time_exit symbol=%s age_s=%.0f max_hold_s=%.0f — closing position",
                    _sym, age_s, self.config.max_hold_seconds,
                )
                # BUG-62: save snapshot before removing — restored if exit rolls back
                self._pending_exits[_sym] = dict(pos)
                del self._open_positions[_sym]
                self._metrics.trade_requests_generated += 1
                return TradeRequest(
                    strategy_id=self.strategy_id,
                    legs=[
                        TradeLeg(
                            exchange_id=pos["buy_ex"],
                            symbol=_sym,
                            side=OrderSide.SELL,
                            size=pos["size"],
                            order_type=OrderType.MARKET,
                            price=signal.buy_price,
                            metadata={"leg_type": "futures_close", "reduceOnly": True},
                        ),
                        TradeLeg(
                            exchange_id=pos["sell_ex"],
                            symbol=_sym,
                            side=OrderSide.BUY,
                            size=pos["size"],
                            order_type=OrderType.MARKET,
                            price=signal.sell_price,
                            metadata={"leg_type": "futures_close", "reduceOnly": True},
                        ),
                    ],
                    expected_profit_usdt=Decimal("0"),
                    confidence=1.0,
                    metadata={"close_reason": "max_hold_exceeded", "age_s": str(int(age_s))},
                )
            else:
                # Position still active, spread not yet converged → block new entry (BUG-I)
                # Store last seen spread for monitor-based exit check
                if _current_spread_bps is not None:
                    self._open_positions[_sym]["last_spread_bps"] = _current_spread_bps
                self._metrics.signals_filtered += 1
                logger.debug(
                    "ff.rejected reason=position_open symbol=%s age_s=%.0f spread_bps=%s exit_thr=%.2f",
                    _sym, age_s,
                    f"{_current_spread_bps:.2f}" if _current_spread_bps is not None else "N/A",
                    _exit_threshold_bps,
                )
                return None

        # US-254: Regime check — block new entries in CRISIS mode
        if self._regime_detector is not None:
            try:
                if self._regime_detector.current_regime == "CRISIS":
                    self._metrics.signals_filtered += 1
                    return None
            except Exception:
                pass  # graceful fallback

        # US-260: static min_spread is the ENTRY FLOOR; adaptive p95 is OUTLIER CAP
        # (p95 as entry threshold blocks 95% of normal signals — use as upper bound instead)
        if float(signal.spread_pct) <= 0:
            self._metrics.signals_filtered += 1
            return None
        _spread_bps = float(signal.spread_pct) * 10000
        min_spread_bps_effective = self.config.min_spread_bps
        if self._adaptive_threshold is not None and self._adaptive_threshold.is_ready:
            _outlier_cap, _ = self._adaptive_threshold.thresholds  # p95
            if _spread_bps > _outlier_cap:
                self._metrics.signals_filtered += 1
                logger.info(
                    "strategy.outlier_rejected strategy=futures_futures reason=outlier_cap "
                    "symbol=%s score_bps=%.2f cap_bps=%.2f",
                    signal.symbol, _spread_bps, _outlier_cap,
                )
                return None

        # US-272: Funding convergence combined score
        try:
            funding_diff_bps = Decimal(str(signal.metadata.get("funding_diff_bps", 0)))
        except Exception:
            funding_diff_bps = Decimal("0")
        # Clamp to ±500 bps — anything beyond is anomalous
        funding_diff_bps = max(Decimal("-500"), min(funding_diff_bps, Decimal("500")))
        if self.config.enable_funding_convergence:
            combined_score = Decimal(str(_spread_bps)) + self.config.funding_convergence_weight * funding_diff_bps
        else:
            combined_score = Decimal(str(_spread_bps))

        if combined_score < min_spread_bps_effective:
            self._metrics.signals_filtered += 1
            logger.info(
                "strategy.rejected strategy=futures_futures reason=min_spread symbol=%s "
                "score_bps=%.2f threshold_bps=%.2f",
                signal.symbol, float(combined_score), float(min_spread_bps_effective),
            )
            return None

        # US-260: Feed spread to adaptive threshold AFTER min_spread filter.
        # Only profitable spreads build the window → p95 represents realistic high-spread distribution.
        # During initial calibration (is_ready=False, need 60 samples), outlier_cap is skipped.
        if self._adaptive_threshold is not None:
            self._adaptive_threshold.update(_spread_bps)

        # US-233: minimum book depth filter
        if self.config.min_book_depth_usd > Decimal("0"):
            book_depth_usd = signal.volume * signal.buy_price
            if book_depth_usd < self.config.min_book_depth_usd:
                self._metrics.signals_filtered += 1
                logger.info(
                    "strategy.rejected strategy=futures_futures reason=depth_insufficient symbol=%s "
                    "depth_usd=%.2f min_depth_usd=%.2f",
                    signal.symbol, float(book_depth_usd), float(self.config.min_book_depth_usd),
                )
                return None

        # PHOENIX: max_position_size is USD notional cap — divide by price to get base units
        _ff_price = signal.buy_price if signal.buy_price > 0 else Decimal("1")
        size = min(signal.volume, (self.config.max_position_size / _ff_price) if _ff_price > 0 else signal.volume)

        # S10: Optional per-trade notional cap
        if self.config.max_notional_usd is not None:
            notional = signal.buy_price * size
            if notional > self.config.max_notional_usd:
                size = self.config.max_notional_usd / signal.buy_price
                if size <= Decimal("0"):
                    self._metrics.signals_filtered += 1
                    return None

        # Check margin safety: required margin must not exceed available * (1 - safety_pct)
        margin_available = Decimal(str(signal.metadata.get("margin_available", "0")))
        required_margin = Decimal("0")  # initialized here; set inside if-block below
        if margin_available > Decimal("0"):
            required_margin = (signal.buy_price * size) / Decimal(str(self.config.max_leverage))
            max_allowed_margin = margin_available * (Decimal("1") - self.config.margin_safety_pct)
            if required_margin > max_allowed_margin:
                self._metrics.signals_filtered += 1
                logger.info(
                    "strategy.rejected strategy=futures_futures reason=margin_insufficient symbol=%s "
                    "required=%.2f max_allowed=%.2f",
                    signal.symbol, float(required_margin), float(max_allowed_margin),
                )
                return None  # SAFE: _pending_entry_symbols.add() not yet called

        # Race guard: claim symbol before first await.
        # try/finally guarantees discard on ALL exit paths including unhandled exceptions
        # from check_and_reserve or cost calculators (MAJOR-1 fix — complete exception coverage).
        if _sym:
            self._pending_entry_symbols.add(_sym)
        try:
            # Bug 29: MarginTracker — check in-flight reservations to prevent margin exhaustion
            if margin_available > Decimal("0") and self._margin_tracker is not None:
                ok = await self._margin_tracker.check_and_reserve(
                    exchange_id=signal.buy_exchange,
                    required_usd=required_margin,
                    available_usd=margin_available,
                )
                if not ok:
                    self._metrics.signals_filtered += 1
                    logger.info(
                        "strategy.rejected strategy=futures_futures reason=margin_tracker_blocked symbol=%s",
                        signal.symbol,
                    )
                    return None  # finally: discard

            buy_notional = signal.buy_price * size
            sell_notional = signal.sell_price * size
            # BUG-14 fix: use estimate_futures_cost — single rollback, no network cost
            # (futures P&L settled in USDT; prior 2×estimate_cost doubled rollback $0.25×2=$0.50)
            if hasattr(self._cost_calculator, "estimate_futures_cost"):
                total_cost = self._cost_calculator.estimate_futures_cost(
                    buy_exchange=signal.buy_exchange,
                    sell_exchange=signal.sell_exchange,
                    buy_notional=buy_notional,
                    sell_notional=sell_notional,
                )
            else:
                # Fallback for stub: fees only via estimate_cost (no network, no rollback)
                buy_cost = self._cost_calculator.estimate_cost(
                    exchange_id=signal.buy_exchange,
                    symbol=signal.symbol,
                    side=OrderSide.BUY,
                    size=size,
                    price=signal.buy_price,
                )
                sell_cost = self._cost_calculator.estimate_cost(
                    exchange_id=signal.sell_exchange,
                    symbol=signal.symbol,
                    side=OrderSide.SELL,
                    size=size,
                    price=signal.sell_price,
                )
                total_cost = buy_cost + sell_cost
            gross_profit = (signal.sell_price - signal.buy_price) * size
            net_profit = gross_profit - total_cost

            if net_profit <= Decimal("0"):
                self._metrics.signals_filtered += 1
                logger.info(
                    "strategy.rejected strategy=futures_futures reason=net_profit_negative symbol=%s "
                    "net_profit=%.6f gross=%.6f cost=%.6f",
                    signal.symbol, float(net_profit), float(gross_profit), float(total_cost),
                )
                return None  # finally: discard

            def _to_futures_exchange(eid: str) -> str:
                """Ensure exchange ID refers to the futures adapter (e.g. 'binance' → 'binance_futures')."""
                if not eid.endswith("_futures"):
                    return f"{eid}_futures"
                return eid

            # Track entry for time-based exit.
            if self.config.max_hold_seconds > 0 and signal.symbol:
                self._open_positions[signal.symbol] = {
                    "buy_ex": _to_futures_exchange(signal.buy_exchange),
                    "sell_ex": _to_futures_exchange(signal.sell_exchange),
                    "size": size,
                    "entry_time": time.time(),
                }

            self._metrics.trade_requests_generated += 1
            return TradeRequest(
                strategy_id=self.strategy_id,
                legs=[
                    TradeLeg(
                        exchange_id=_to_futures_exchange(signal.buy_exchange),
                        symbol=signal.symbol,
                        side=OrderSide.BUY,
                        size=size,
                        order_type=OrderType.MARKET,
                        price=signal.buy_price,
                        metadata={"leverage": str(self.config.max_leverage), "leg_type": "futures"},
                    ),
                    TradeLeg(
                        exchange_id=_to_futures_exchange(signal.sell_exchange),
                        symbol=signal.symbol,
                        side=OrderSide.SELL,
                        size=size,
                        order_type=OrderType.MARKET,
                        price=signal.sell_price,
                        metadata={"leverage": str(self.config.max_leverage), "leg_type": "futures"},
                    ),
                ],
                expected_profit_usdt=net_profit,
                confidence=signal.confidence,
                metadata={
                    "gross_profit": str(gross_profit),
                    "total_cost": str(total_cost),
                    "leverage": str(self.config.max_leverage),
                },
            )
        finally:
            # Unconditional cleanup: discard race guard on ALL exit paths (return, exception, cancel)
            if _sym:
                self._pending_entry_symbols.discard(_sym)

    async def on_fill(self, trade: Trade) -> None:
        await super().on_fill(trade)
        # BUG-HIGH: clear _pending_exits/_exiting_symbols on exit fill to prevent
        # indefinite leak when on_execution_success is not called (partial fills).
        _EXIT_LEG_TYPES = frozenset((
            "futures_close", "spread_exit_close_long", "spread_exit_close_short",
            "time_exit_close_long", "time_exit_close_short",
        ))
        leg_type = (trade.metadata or {}).get("leg_type", "")
        if leg_type in _EXIT_LEG_TYPES:
            sym = trade.symbol
            self._exiting_symbols.discard(sym)
            if sym in self._pending_exits:
                self._pending_exits.pop(sym)
                logger.debug("ff.on_fill_exit_cleanup symbol=%s leg_type=%s", sym, leg_type)
