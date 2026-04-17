"""Funding Rate Arbitrage strategy.

Exploits divergent funding rates across exchanges.
Go short where funding rate is high (shorts receive funding payments).
Go long where funding rate is low or negative (longs receive funding payments).

signal.metadata must contain:
  - 'funding_rate_sell': float  (funding rate on sell_exchange, high = shorts receive)
  - 'funding_rate_buy': float   (funding rate on buy_exchange, low = longs receive)
  - 'funding_diff_bps': float   (abs difference in basis points)
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from pydantic import BaseModel, Field

from src.core.models import OrderSide, OrderType, Signal, Trade
from src.core.ou_process import OUProcess
from src.strategies.base import BaseStrategy, CostCalculator, TradeLeg, TradeRequest

logger = logging.getLogger(__name__)


class FundingRateConfig(BaseModel):
    """Configuration for FundingRateStrategy."""

    min_funding_diff_bps: Decimal = Field(default=Decimal("2"), ge=Decimal("0"))  # trading.json 우선
    max_position_size: Decimal = Field(default=Decimal("50000"), gt=Decimal("0"))  # USD notional cap
    max_holding_periods: int = Field(default=12, ge=1)  # SIT-3: 3→12 (4일 carry)
    hedge_ratio: Decimal = Field(default=Decimal("1.0"), gt=Decimal("0"))
    settlement_window_minutes: float = Field(default=0.0, ge=0.0)
    settlement_hours: list[int] = Field(default_factory=lambda: [0, 8, 16])
    enable_ou_filter: bool = Field(default=True)
    ou_min_halflife_s: float = Field(default=60.0)  # trading.json strategy_filters 우선
    ou_window: int = Field(default=360)


class FundingRateStrategy(BaseStrategy):
    """
    Funding Rate Arbitrage.

    Simultaneously:
      - SHORT on sell_exchange where funding_rate_sell is positive (shorts receive)
      - LONG on buy_exchange where funding_rate_buy is low/negative (longs receive)

    Net income per period ≈ (funding_rate_sell - funding_rate_buy) * position_size.
    Exits after max_holding_periods or when the differential collapses.
    """

    STRATEGY_TYPE = "funding_rate_arb"
    _SETTLEMENT_COOLDOWN_S: float = 120.0  # BUG-77: cooldown + pending timeout (keep in sync)

    def __init__(
        self,
        strategy_id: str,
        cost_calculator: CostCalculator,
        config: FundingRateConfig | None = None,
        regime_detector: Any = None,
    ) -> None:
        super().__init__(strategy_id, cost_calculator)
        self._regime_detector = regime_detector
        self.config = config or FundingRateConfig()
        # US-239: Track open positions per symbol to prevent duplicate entries
        # BUG-74: value is now a dict {sell_exchange, buy_exchange, size} for settlement exits
        self._open_positions: dict[str, Any] = {}  # symbol → position dict
        # US-239: Last settlement hour seen (for auto-release after settlement)
        # Initialize to -1 (sentinel). _check_settlement_release guards against
        # spurious startup triggers by skipping release when _open_positions is empty.
        self._last_settlement_hour: int = -1
        # US-262: Rolling funding rate history for z-score dynamic threshold
        from collections import deque
        self._funding_diff_history: deque[float] = deque(maxlen=360)  # ~8H at 80s intervals
        # US-268: OU Process for mean-reversion analysis
        self._ou = OUProcess(window=self.config.ou_window)
        # BUG-74: Settlement exit queue — populated by _check_settlement_release, drained in on_signal
        self._pending_exit_requests: list[TradeRequest] = []
        # Issue#4: positions moved here during settlement so they can be restored on failed exit.
        # Cleared automatically after _SETTLEMENT_COOLDOWN_S after routing (market orders settle
        # within seconds; 120s is conservative enough to detect genuine failures).
        self._pending_settlement_positions: dict[str, Any] = {}  # symbol → pos dict
        self._settlement_routed_at: float = 0.0  # monotonic timestamp when exits were routed
        # BUG-77: Settlement cooldown — block ALL new entries after settlement fires.
        # Prevents race condition where timeout clears pending_settlement guard,
        # allowing new entries on symbols whose exits haven't confirmed yet.
        self._settlement_cooldown_until: float = 0.0  # monotonic timestamp

    def _minutes_to_next_settlement(self, now_utc: datetime | None = None) -> float:
        """Return minutes until next funding settlement (UTC 00/08/16).

        Used by on_signal to restrict entries to the settlement window.
        """
        if now_utc is None:
            now_utc = datetime.now(timezone.utc)
        hours_since_midnight = now_utc.hour + now_utc.minute / 60.0 + now_utc.second / 3600.0
        min_hours_before = min(
            ((sh - hours_since_midnight) % 24) for sh in self.config.settlement_hours
        )
        return min_hours_before * 60.0

    def _check_settlement_release(self) -> None:
        """Auto-release all positions after a settlement hour passes.

        BUG-74: Queues exit TradeRequests before clearing _open_positions so
        exchange positions are actually unwound (not just forgotten).
        """
        now_utc = datetime.now(timezone.utc)
        current_hour = now_utc.hour
        if current_hour in self.config.settlement_hours and current_hour != self._last_settlement_hour:
            self._last_settlement_hour = current_hour
            if not self._open_positions:
                # No positions to release — skip spurious trigger (e.g., engine just started)
                return
            for symbol, pos in list(self._open_positions.items()):
                if isinstance(pos, dict) and "sell_exchange" in pos:
                    self._pending_exit_requests.append(TradeRequest(
                        strategy_id=self.strategy_id,
                        legs=[
                            TradeLeg(
                                exchange_id=pos["sell_exchange"],
                                symbol=symbol,
                                side=OrderSide.BUY,  # close the short leg
                                size=pos["size"],
                                order_type=OrderType.MARKET,
                                price=None,
                                metadata={"leg_type": "settlement_close_short", "reduceOnly": True},
                            ),
                            TradeLeg(
                                exchange_id=pos["buy_exchange"],
                                symbol=symbol,
                                side=OrderSide.SELL,  # close the long leg
                                size=pos.get("long_size", pos["size"]),  # use hedge-ratio size
                                order_type=OrderType.MARKET,
                                price=None,
                                metadata={"leg_type": "settlement_close_long", "reduceOnly": True},
                            ),
                        ],
                        expected_profit_usdt=Decimal("0"),
                        confidence=0.0,
                        metadata={"reason": "settlement_exit"},
                    ))
                    # Issue#4 fix: move to pending_settlement (not delete) so failed exits can retry.
                    # Duplicate guard still blocks new entries since symbol is absent from _open_positions.
                    self._pending_settlement_positions[symbol] = pos
            self._open_positions.clear()
            # BUG-77: Block all new entries after settlement
            self._settlement_cooldown_until = time.monotonic() + self._SETTLEMENT_COOLDOWN_S

    async def on_signal(self, signal: Signal) -> Optional[TradeRequest]:
        self._metrics.signals_received += 1
        logger.info(
            "funding_rate.on_signal_entry sym=%s is_active=%s",
            signal.symbol, self._is_active,
        )

        if not self._is_active:
            self._metrics.signals_filtered += 1
            return None


        # US-254: Regime check — SKIP for funding_rate (delta-neutral carry trade)
        # Funding rate arb is hedged (long+short), so CRISIS regime doesn't apply.
        # Other strategies (cross_exchange, spot_futures) still respect regime gate.

        # US-239: Auto-release positions after settlement
        self._check_settlement_release()
        # BUG-74: Drain settlement exit queue — return one exit TradeRequest per call
        if self._pending_exit_requests:
            self._settlement_routed_at = time.monotonic()  # mark routing time for pending timeout
            return self._pending_exit_requests.pop(0)

        # BUG-77: Settlement cooldown — block ALL new entries while exits are being processed
        if time.monotonic() < self._settlement_cooldown_until:
            self._metrics.signals_filtered += 1
            logger.info(
                "funding_rate.settlement_cooldown sym=%s remaining=%.0fs",
                signal.symbol,
                self._settlement_cooldown_until - time.monotonic(),
            )
            return None

        # US-239: Settlement timing filter — only enter within window before settlement
        # Disabled when settlement_window_minutes == 0 (e.g., test mode)
        if self.config.settlement_window_minutes > 0:
            minutes_to_settlement = self._minutes_to_next_settlement()
            if minutes_to_settlement > self.config.settlement_window_minutes:
                self._metrics.signals_filtered += 1
                return None

        # BUG-88: Max concurrent FR positions — prevent margin exhaustion.
        # FR 5+ positions consume all Binance margin → FF can't execute.
        from src.core.config_loader import get_config as _gc_max
        _max_fr_positions = int(_gc_max("strategy_filters.funding_rate_max_positions", default=3))
        if len(self._open_positions) >= _max_fr_positions:
            self._metrics.signals_filtered += 1
            logger.info(
                "funding_rate.max_positions sym=%s open=%d max=%d",
                signal.symbol, len(self._open_positions), _max_fr_positions,
            )
            return None

        # US-239: Duplicate position guard — skip if already have position or pending settlement exit
        # Check both _open_positions AND _pending_settlement_positions: after _check_settlement_release
        # moves positions to _pending_settlement_positions, the symbol is absent from _open_positions
        # but the exchange position still exists. Without this guard, a new entry would stack on top.
        if signal.symbol in self._open_positions or signal.symbol in self._pending_settlement_positions:
            self._metrics.signals_filtered += 1
            logger.info(
                "funding_rate.duplicate_guard sym=%s open_pos=%s pending_settlement=%s",
                signal.symbol,
                list(self._open_positions.keys()),
                list(self._pending_settlement_positions.keys()),
            )
            return None

        # Extract funding rates from metadata
        funding_rate_sell = Decimal(str(signal.metadata.get("funding_rate_sell", "0")))
        funding_rate_buy = Decimal(str(signal.metadata.get("funding_rate_buy", "0")))
        funding_diff = funding_rate_sell - funding_rate_buy
        funding_diff_bps = funding_diff * Decimal("10000")
        logger.info(
            "funding_rate.evaluating sym=%s diff_bps=%.4f sell_rate=%s buy_rate=%s",
            signal.symbol, float(funding_diff_bps), str(funding_rate_sell), str(funding_rate_buy),
        )

        # US-268: OU Process mean-reversion filter
        self._ou.update(float(funding_diff_bps), time.monotonic())
        if self.config.enable_ou_filter and self._ou.is_mean_reverting:
            if self._ou.half_life < self.config.ou_min_halflife_s:
                self._metrics.signals_filtered += 1
                logger.info(
                    "OU filter: half_life=%.1fs < min=%.1fs, skipping signal",
                    self._ou.half_life,
                    self.config.ou_min_halflife_s,
                )
                return None

        # US-262: Z-score dynamic threshold for funding rate
        self._funding_diff_history.append(float(funding_diff_bps))
        if len(self._funding_diff_history) >= 30:
            import math
            _hist = list(self._funding_diff_history)
            _mean = sum(_hist) / len(_hist)
            _var = sum((x - _mean) ** 2 for x in _hist) / (len(_hist) - 1)
            _std = math.sqrt(_var) if _var > 0 else 0.0
            if _std > 0:
                _z_score = (float(funding_diff_bps) - _mean) / _std
                # Only enter when z-score > 1.5 (significant deviation)
                from src.core.config_loader import get_config
                _z_threshold = float(get_config("strategy_filters.funding_zscore_threshold", default=-1))
                if _z_score < _z_threshold:
                    self._metrics.signals_filtered += 1
                    logger.debug(
                        "funding_rate.zscore_filter z=%.2f threshold=%.1f diff_bps=%.1f",
                        _z_score, _z_threshold, float(funding_diff_bps),
                    )
                    return None

        if funding_diff_bps < self.config.min_funding_diff_bps:
            self._metrics.signals_filtered += 1
            logger.info(
                "funding_rate.min_diff_rejected sym=%s diff_bps=%.4f threshold=%.1f",
                signal.symbol, float(funding_diff_bps), float(self.config.min_funding_diff_bps),
            )
            return None

        # PHOENIX: max_position_size is USD notional cap (set in main.py).
        # Convert to base units using avg price so any coin works correctly.
        avg_price = (signal.buy_price + signal.sell_price) / Decimal("2")
        _max_base = (self.config.max_position_size / avg_price) if avg_price > 0 else self.config.max_position_size
        base_size = min(signal.volume, _max_base)
        _position_usd = base_size * avg_price if avg_price > 0 else Decimal("0")

        # PHOENIX: Ensure minimum notional $5 (exchange minimum)
        _MIN_NOTIONAL_USD = Decimal("5")
        if _position_usd < _MIN_NOTIONAL_USD and avg_price > 0:
            min_size_needed = (_MIN_NOTIONAL_USD / avg_price).quantize(Decimal("0.00000001"))
            if min_size_needed <= _max_base:
                base_size = min_size_needed
                _position_usd = base_size * avg_price
                logger.debug(
                    "funding_rate.min_notional_adjusted sym=%s pos_usd=%.2f → %.2f",
                    signal.symbol, float(_position_usd / avg_price * avg_price), float(_position_usd),
                )
            else:
                self._metrics.signals_filtered += 1
                logger.info(
                    "funding_rate.min_notional_skip sym=%s pos_usd=%.2f max_base=%.4f",
                    signal.symbol, float(_position_usd), float(_max_base),
                )
                return None

        size = base_size
        # Apply hedge ratio to the long leg size
        long_size = (size * self.config.hedge_ratio).quantize(Decimal("0.00000001"))

        # Friction costs for both legs — use estimate_futures_cost for accurate futures friction:
        # no ETH network transfer (USDT-settled), rollback proportional to notional (not fixed $5).
        buy_notional = long_size * signal.buy_price
        sell_notional = size * signal.sell_price
        if hasattr(self._cost_calculator, "estimate_futures_cost"):
            total_cost = self._cost_calculator.estimate_futures_cost(
                buy_exchange=str(signal.buy_exchange),
                sell_exchange=str(signal.sell_exchange),
                buy_notional=buy_notional,
                sell_notional=sell_notional,
            )
        else:
            total_cost = (
                self._cost_calculator.estimate_cost(
                    exchange_id=signal.sell_exchange,
                    symbol=signal.symbol,
                    side=OrderSide.SELL,
                    size=size,
                    price=signal.sell_price,
                )
                + self._cost_calculator.estimate_cost(
                    exchange_id=signal.buy_exchange,
                    symbol=signal.symbol,
                    side=OrderSide.BUY,
                    size=long_size,
                    price=signal.buy_price,
                )
            )

        # NOTE: Slippage is already accounted for upstream by SignalGenerator
        # (CEXOrderbookSlippage pre-filter). Adding phantom slippage here
        # would double-count and reject profitable funding rate trades.
        avg_price = (signal.buy_price + signal.sell_price) / Decimal("2")

        # Expected income: funding rate arb is a carry trade — income accrues over
        # multiple settlement periods. Use max_holding_periods as expected hold.
        # 3 periods = 24h (8h each), typical for funding rate arb
        expected_funding_income = (
            funding_diff * avg_price * size * Decimal(str(self.config.max_holding_periods))
        )
        net_profit = expected_funding_income - total_cost

        if net_profit <= Decimal("0"):
            self._metrics.signals_filtered += 1
            logger.info(
                "funding_rate.cost_rejected sym=%s diff_bps=%.1f income=%.4f cost=%.4f net=%.4f periods=%d",
                signal.symbol, float(funding_diff_bps), float(expected_funding_income),
                float(total_cost), float(net_profit), self.config.max_holding_periods,
            )
            return None

        # US-239: Record open position to prevent duplicate entries (BUG-74: store exchange info for exit)
        # Store long_size separately — hedge ratio may differ from short size, needed for settlement close
        self._open_positions[signal.symbol] = {
            "sell_exchange": str(signal.sell_exchange),
            "buy_exchange": str(signal.buy_exchange),
            "size": size,
            "long_size": long_size,
        }

        self._metrics.trade_requests_generated += 1
        logger.info(
            "funding_rate.trade_request_generated sym=%s net_profit=%.4f size=%s buy_ex=%s sell_ex=%s",
            signal.symbol, float(net_profit), str(size),
            str(signal.buy_exchange), str(signal.sell_exchange),
        )
        return TradeRequest(
            strategy_id=self.strategy_id,
            legs=[
                TradeLeg(
                    exchange_id=signal.sell_exchange,
                    symbol=signal.symbol,
                    side=OrderSide.SELL,
                    size=size,
                    order_type=OrderType.MARKET,
                    price=signal.sell_price,
                    metadata={
                        "leg_type": "short",
                        "funding_rate": str(funding_rate_sell),
                    },
                ),
                TradeLeg(
                    exchange_id=signal.buy_exchange,
                    symbol=signal.symbol,
                    side=OrderSide.BUY,
                    size=long_size,
                    order_type=OrderType.MARKET,
                    price=signal.buy_price,
                    metadata={
                        "leg_type": "long",
                        "funding_rate": str(funding_rate_buy),
                    },
                ),
            ],
            expected_profit_usdt=net_profit,
            confidence=signal.confidence,
            metadata={
                "funding_diff_bps": str(funding_diff_bps),
                "max_holding_periods": str(self.config.max_holding_periods),
                "expected_funding_income": str(expected_funding_income),
                "total_cost": str(total_cost),
            },
        )

    async def on_fill(self, trade: Trade) -> None:
        await super().on_fill(trade)

    def pop_exit_requests(self) -> list[TradeRequest]:
        """Drain and return pending settlement-close TradeRequests.

        Called by _strategy_exit_poll_loop in main.py every 60s so that
        settlement exits are routed even when no new signal arrives.

        Issue#4 fix: _pending_settlement_positions retains position data until
        _SETTLEMENT_COOLDOWN_S after routing. Market orders settle in < 2s; 120s is
        generous. After timeout, if positions are still in _pending_settlement_positions,
        they were likely filled successfully — clear them. If fills genuinely failed,
        Telegram kill-switch or /closepositions handles it.
        """
        if self._pending_exit_requests:
            # Exits are queued — record routing timestamp and return them
            self._settlement_routed_at = time.monotonic()
        elif self._pending_settlement_positions:
            elapsed = time.monotonic() - self._settlement_routed_at
            if self._settlement_routed_at > 0 and elapsed > self._SETTLEMENT_COOLDOWN_S:
                logger.info(
                    "fr.settlement_confirmed_by_timeout symbols=%s elapsed=%.0fs",
                    list(self._pending_settlement_positions.keys()), elapsed,
                )
                self._pending_settlement_positions.clear()
                self._settlement_routed_at = 0.0
            elif self._settlement_routed_at == 0.0:
                logger.warning(
                    "fr.settlement_positions_unconfirmed symbols=%s — "
                    "no exit requests routed yet; use /closepositions if positions exist",
                    list(self._pending_settlement_positions.keys()),
                )
        reqs = list(self._pending_exit_requests)
        self._pending_exit_requests.clear()
        return reqs

    def inject_position(self, symbol: str, metadata: dict) -> None:
        """Inject a pre-existing exchange position into tracking.

        Called by live.py._reconcile_positions_on_startup() to sync exchange
        state with strategy state after engine restart. Expects metadata with
        keys: sell_exchange, buy_exchange, size, long_size.
        """
        if symbol in self._open_positions:
            logger.info("fr.inject_position_skip symbol=%s — already tracked", symbol)
            return
        self._open_positions[symbol] = metadata
        logger.info(
            "fr.inject_position symbol=%s sell_exchange=%s buy_exchange=%s size=%s",
            symbol, metadata.get("sell_exchange"), metadata.get("buy_exchange"),
            metadata.get("size"),
        )

    def on_execution_rollback(self, symbol: str) -> None:
        """Legacy — delegates to handle_entry_rollback for backward compat."""
        self.handle_entry_rollback(symbol)

    # WS-2: Separated lifecycle callbacks
    def handle_entry_rollback(self, symbol: str) -> None:
        """Entry rolled back → clear tracking."""
        if symbol in self._open_positions:
            logger.info("fr.entry_rollback_cleared symbol=%s", symbol)
            self._open_positions.pop(symbol, None)

    def handle_exit_rollback(self, symbol: str) -> None:
        """Settlement exit rolled back → restore for retry."""
        if symbol in self._pending_settlement_positions:
            logger.warning(
                "fr.settlement_exit_rollback symbol=%s — restoring to allow retry", symbol,
            )
            restored = self._pending_settlement_positions.pop(symbol)
            self._open_positions[symbol] = restored
            self._settlement_routed_at = 0.0
        # NOTE: _settlement_cooldown_until is NOT cleared on rollback — intentional.

    def handle_entry_success(self, symbol: str) -> None:
        """Entry succeeded — no pending cleanup for FR."""

    def handle_exit_success(self, symbol: str) -> None:
        """Settlement exit succeeded — clear pending."""
        self._pending_settlement_positions.pop(symbol, None)

    def clear_ghost(self, symbol: str) -> None:
        """Exchange has no position → remove ALL tracking."""
        self._open_positions.pop(symbol, None)
        self._pending_settlement_positions.pop(symbol, None)
        logger.warning("fr.ghost_cleared symbol=%s", symbol)
        # If settlement exits are failing, blocking new entries is the safe default.
