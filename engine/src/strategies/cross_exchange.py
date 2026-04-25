"""Cross-Exchange Spot Arbitrage strategy (CEX-CEX).

Detects price discrepancy for the same asset across two exchanges.
Pre-funded accounts on both sides — no transfer needed.
Entry: buy cheap on exchange A, sell expensive on exchange B simultaneously.

latency_boost mode (US-194): when enabled, additionally requires a measurable
latency advantage (slow_exchange_ema > fast_exchange_ema) before trading.

US-235 fine-tuning:
  - max_spread_bps: rejects anomalously wide spreads (likely stale data)
  - min_book_depth_usd: rejects signals where available liquidity is too thin
"""
from __future__ import annotations

import logging
import os
from decimal import Decimal
from typing import Any, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

from src.core.exchanges import KRW_EXCHANGES
from src.core.models import OrderSide, OrderType, Signal, Trade
from src.infra.metrics import SIGNALS_REJECTED_SYMBOL_UNSUPPORTED
from src.strategies.base import BaseStrategy, CostCalculator, TradeLeg, TradeRequest


class CrossExchangeConfig(BaseModel):
    """Configuration for CrossExchangeStrategy."""

    min_spread_bps: Decimal = Field(default=Decimal("10"), ge=Decimal("0"))
    max_position_size: Decimal = Field(default=Decimal("50000"), gt=Decimal("0"))  # USD notional cap
    rebalance_threshold: Decimal = Field(default=Decimal("0.05"), ge=Decimal("0"))
    latency_boost: bool = Field(default=False)
    min_latency_advantage_ms: float = Field(default=5.0, ge=0.0)
    # US-235: anomaly guard — reject spreads wider than this (likely stale orderbook)
    max_spread_bps: Decimal = Field(default=Decimal("100"), ge=Decimal("0"))
    # US-235: minimum available liquidity in USD (volume * price proxy)
    min_book_depth_usd: Decimal = Field(default=Decimal("500"), ge=Decimal("0"))


class CrossExchangeStrategy(BaseStrategy):
    """
    Cross-Exchange Spot Arbitrage.

    Buys on the cheaper exchange and sells on the more expensive one
    simultaneously. Net profit must exceed all friction costs (fees + slippage).

    When latency_boost=True, an additional latency-advantage gate is applied:
    signals are only accepted when the slow exchange's EMA latency exceeds the
    fast exchange's EMA latency by at least min_latency_advantage_ms.
    """

    STRATEGY_TYPE = "cross_exchange_spot"

    def __init__(
        self,
        strategy_id: str,
        cost_calculator: CostCalculator,
        config: CrossExchangeConfig | None = None,
        latency_tracker=None,
        regime_detector: Any = None,
        exchange_registry: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(strategy_id, cost_calculator)
        self._regime_detector = regime_detector
        if config is None:
            from src.core.config_loader import get_config
            config = CrossExchangeConfig(
                max_spread_bps=Decimal(str(get_config("strategy_filters.cross_exchange_max_spread_bps", default=100))),
                min_book_depth_usd=Decimal(str(get_config("strategy_filters.cross_exchange_min_book_depth_usd", default=500))),
            )
        self.config = config
        self._latency_tracker = latency_tracker
        # BUG-225: per-exchange symbol availability gate
        self._exchange_registry: dict[str, Any] = exchange_registry or {}
        # Log-once guard: set of (symbol, exchange) already warned to avoid log flood
        self._symbol_unsupported_logged: set[tuple[str, str]] = set()

        # US-260: Adaptive threshold — rolling percentile + volatility weight
        try:
            from src.core.adaptive_threshold import AdaptiveThreshold
            self._adaptive_threshold = AdaptiveThreshold(
                window=1440,
                entry_percentile=95.0,
                exit_percentile=50.0,
                static_entry=float(config.min_spread_bps),
                static_exit=float(config.min_spread_bps) * 0.5,
            )
        except ImportError:
            self._adaptive_threshold = None

    def _latency_advantage_ms(self, fast_exchange: str, slow_exchange: str) -> float:
        """Return EMA latency difference (slow - fast) in ms, or 0 if data missing."""
        if self._latency_tracker is None:
            return 0.0
        fast_info = self._latency_tracker.get_latency_info(fast_exchange)
        slow_info = self._latency_tracker.get_latency_info(slow_exchange)
        if fast_info is None or slow_info is None:
            return 0.0
        return max(0.0, slow_info.ema_ms - fast_info.ema_ms)

    async def on_signal(self, signal: Signal) -> Optional[TradeRequest]:
        self._metrics.signals_received += 1

        if not self._is_active:
            self._metrics.signals_filtered += 1
            return None


        # US-254: Regime check — block new entries in CRISIS mode
        if self._regime_detector is not None:
            try:
                if self._regime_detector.current_regime == "CRISIS":
                    self._metrics.signals_filtered += 1
                    return None
            except Exception:
                pass  # graceful fallback

        # US-198: Korean exchange filter for latency_boost mode
        # Korean exchanges have stale orderbook data, making latency-based decisions unreliable
        if self.config.latency_boost:
            if signal.buy_exchange in KRW_EXCHANGES or signal.sell_exchange in KRW_EXCHANGES:
                self._metrics.signals_filtered += 1
                return None

        # 2026-04-22 fix: KRW exchange × USDT pair 조합 차단 (latency_boost 무관)
        # coinone/upbit/bithumb는 KRW base (BTC/KRW) 거래소. USDT pair signal은
        # KRW→USDT 환율 변환 누락 위험 + toxicity reject 33K root cause.
        # 정상 KRW arb는 별도 KRW pair 전략으로 처리.
        krw_exchange_in_legs = (
            signal.buy_exchange in KRW_EXCHANGES or signal.sell_exchange in KRW_EXCHANGES
        )
        if krw_exchange_in_legs and signal.symbol.endswith("/USDT"):
            self._metrics.signals_filtered += 1
            return None

        # BUG-225: per-exchange symbol availability gate
        # Prevents Korean-leg failures where Upbit/Bithumb don't list a USDT pair.
        if self._exchange_registry:
            for leg_exchange_id in (signal.buy_exchange, signal.sell_exchange):
                adapter = self._exchange_registry.get(leg_exchange_id)
                if adapter is not None and hasattr(adapter, "supports_symbol"):
                    if not adapter.supports_symbol(signal.symbol):
                        self._metrics.signals_filtered += 1
                        SIGNALS_REJECTED_SYMBOL_UNSUPPORTED.labels(
                            strategy=self.STRATEGY_TYPE,
                            exchange=leg_exchange_id,
                        ).inc()
                        key = (signal.symbol, leg_exchange_id)
                        if key not in self._symbol_unsupported_logged:
                            self._symbol_unsupported_logged.add(key)
                            logger.warning(
                                "cross_exchange.symbol_unsupported symbol=%s exchange=%s — signal rejected",
                                signal.symbol,
                                leg_exchange_id,
                            )
                        return None

        # Spread threshold: use static min_spread_bps only.
        # Adaptive threshold disabled for cross_exchange — arbitrage should trade
        # every profitable opportunity, not just top 5% (95th percentile = mean-reversion logic).
        # SignalGenerator already guarantees net_edge >= MIN_EDGE_BPS after full friction.
        min_spread = self.config.min_spread_bps / Decimal("10000")
        if signal.spread_pct < min_spread:
            self._metrics.signals_filtered += 1
            logger.info(
                "strategy.rejected strategy=cross_exchange reason=min_spread symbol=%s "
                "spread_bps=%.2f threshold_bps=%.2f",
                signal.symbol, float(signal.spread_pct) * 10000, float(min_spread) * 10000,
            )
            return None

        # US-235: Reject anomalously wide spreads (likely stale/bad orderbook data)
        if self.config.max_spread_bps > Decimal("0"):
            max_spread = self.config.max_spread_bps / Decimal("10000")
            if signal.spread_pct > max_spread:
                self._metrics.signals_filtered += 1
                # BUG-133: DEBUG level — routine anomaly filter, not a warning
                logger.debug(
                    "cross_exchange.spread_too_wide symbol=%s spread_pct=%.6f max_spread_bps=%s",
                    signal.symbol,
                    float(signal.spread_pct),
                    self.config.max_spread_bps,
                )
                return None

        # US-260: Feed spread to adaptive threshold tracker after quality filters.
        # BUG-39: update() must run AFTER min_spread and max_spread filters to prevent
        # sub-threshold noise and anomalous outliers from contaminating the distribution.
        _spread_bps = float(signal.spread_pct) * 10000
        if self._adaptive_threshold is not None:
            self._adaptive_threshold.update(_spread_bps)

        # US-235: Reject signals where available liquidity (volume * price) is too thin
        if self.config.min_book_depth_usd > Decimal("0"):
            book_depth_usd = signal.volume * signal.buy_price
            if book_depth_usd < self.config.min_book_depth_usd:
                self._metrics.signals_filtered += 1
                # BUG-133: DEBUG level — routine liquidity filter, not a warning
                logger.debug(
                    "cross_exchange.book_depth_insufficient symbol=%s depth_usd=%.2f min=%.2f",
                    signal.symbol,
                    float(book_depth_usd),
                    float(self.config.min_book_depth_usd),
                )
                return None

        # Latency-boost gate
        if self.config.latency_boost:
            advantage_ms = self._latency_advantage_ms(signal.buy_exchange, signal.sell_exchange)
            if advantage_ms < self.config.min_latency_advantage_ms:
                self._metrics.signals_filtered += 1
                return None

        # PHOENIX: max_position_size is USD notional cap — divide by price to get base units
        _ce_avg_price = (signal.buy_price + signal.sell_price) / Decimal("2")
        size = min(signal.volume, (self.config.max_position_size / _ce_avg_price) if _ce_avg_price > 0 else signal.volume)

        # Use pre-computed net_profit from SignalGenerator (already includes fee+slippage+network+rollback).
        # DO NOT re-calculate friction here — that causes double-counting (see CLAUDE.md "이중 슬리피지 금지").
        net_profit_str = signal.metadata.get("net_profit")
        if net_profit_str is not None:
            net_profit = Decimal(net_profit_str)
        else:
            # Fallback: estimate from gross spread (no double friction)
            gross_profit = (signal.sell_price - signal.buy_price) * size
            net_profit = gross_profit  # friction already applied by SignalGenerator

        if net_profit <= Decimal("0"):
            self._metrics.signals_filtered += 1
            logger.info(
                "strategy.rejected strategy=cross_exchange reason=net_profit_negative symbol=%s "
                "net_profit=%.6f",
                signal.symbol, float(net_profit),
            )
            return None

        gross_profit = (signal.sell_price - signal.buy_price) * size
        metadata: dict = {"gross_profit": str(gross_profit), "net_profit": str(net_profit)}
        if self.config.latency_boost:
            metadata["mode"] = "latency_boost"
            metadata["latency_advantage_ms"] = str(
                self._latency_advantage_ms(signal.buy_exchange, signal.sell_exchange)
            )

        self._metrics.trade_requests_generated += 1
        return TradeRequest(
            strategy_id=self.strategy_id,
            legs=[
                TradeLeg(
                    exchange_id=signal.buy_exchange,
                    symbol=signal.symbol,
                    side=OrderSide.BUY,
                    size=size,
                    order_type=OrderType.MARKET,
                    price=signal.buy_price,
                ),
                TradeLeg(
                    exchange_id=signal.sell_exchange,
                    symbol=signal.symbol,
                    side=OrderSide.SELL,
                    size=size,
                    order_type=OrderType.MARKET,
                    price=signal.sell_price,
                ),
            ],
            expected_profit_usdt=net_profit,
            confidence=signal.confidence,
            metadata=metadata,
        )

    async def on_fill(self, trade: Trade) -> None:
        await super().on_fill(trade)
