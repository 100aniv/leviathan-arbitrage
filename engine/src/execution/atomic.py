"""Atomic order executor — IOC limit with market fallback.

US-119: Try IOC limit first; fall back to market order if partially filled or timed out.
US-275: Partial fill stop — auto-close partial positions internally.
US-275-a: DepthAnalyzer sizing — scale size to available liquidity before execution.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Protocol

import httpx
import structlog

from src.core.config import get_settings
from src.core.depth_analyzer import DepthAnalyzer
from src.core.order_book import OrderBook
from src.infra.metrics import IOC_FILL_RATE, IOC_VS_MARKET

logger = structlog.get_logger(__name__)


class ExchangeOrderAPI(Protocol):
    async def place_ioc_limit(
        self, symbol: str, side: str, price: Decimal, size: Decimal
    ) -> "OrderResult": ...

    async def place_market(
        self, symbol: str, side: str, size: Decimal
    ) -> "OrderResult": ...


@dataclass
class OrderResult:
    filled_size: Decimal
    avg_price: Decimal
    order_type: str   # "ioc_limit" or "market" or "market_fallback"
    latency_ms: float


@dataclass
class FillQuality:
    ioc_slippage_bps: float
    market_slippage_bps: float
    fill_rate: float  # 0-1, fraction of fills that went via IOC


class AtomicOrderExecutor:
    """Execute orders atomically: IOC limit first, market order as fallback."""

    IOC_MIN_FILL_RATIO = Decimal("0.95")  # 95%+ fill counts as full IOC success

    def __init__(
        self,
        timeout_ms: float = 1000,
        depth_analyzer: DepthAnalyzer | None = None,
    ) -> None:
        self._timeout_ms = timeout_ms
        self._ioc_fills: int = 0
        self._market_fills: int = 0
        self._ioc_slippage_sum: float = 0.0
        self._market_slippage_sum: float = 0.0
        self._executed_keys: dict[str, float] = {}  # US-153: key → timestamp
        # US-275: partial fill stop
        _op = get_settings().operational
        self.partial_fill_timeout_s: float = _op.partial_fill_timeout_s
        self.max_loss_pct: float = _op.max_loss_pct
        self.enable_partial_fill_stop: bool = _op.enable_partial_fill_stop
        # US-275-a: depth-based sizing
        self._depth_analyzer: DepthAnalyzer | None = depth_analyzer
        self.enable_depth_sizing: bool = _op.enable_depth_sizing
        # US-331: leg risk detection
        self._leg_risk_events: int = 0

    def _cleanup_old_keys(self) -> None:
        """Remove idempotency keys older than 5 minutes (US-153)."""
        cutoff = time.time() - 300
        expired = [k for k, ts in self._executed_keys.items() if ts < cutoff]
        for k in expired:
            del self._executed_keys[k]

    async def try_ioc(
        self,
        exchange: ExchangeOrderAPI,
        symbol: str,
        side: str,
        price: Decimal,
        size: Decimal,
        ttl_ms: float | None = None,
    ) -> tuple[bool, Decimal, Decimal, float]:
        """Pure IOC attempt — no market fallback, no idempotency, no depth sizing.

        Path-B v2 Day 11: extracted primitive used by the parallel
        cross-exchange executor. `execute()` below still calls this
        internally so existing callers stay byte-compatible.

        Args:
            exchange: Adapter implementing `place_ioc_limit`.
            symbol: Market symbol.
            side: ``"buy"``/``"sell"`` (or ``"bid"``/``"ask"``).
            price: Limit price for the IOC order.
            size: Requested size.
            ttl_ms: Hard timeout. Defaults to ``self._timeout_ms``.

        Returns:
            ``(filled, filled_size, avg_price, elapsed_ms)`` tuple.
            ``filled`` is ``True`` iff ``filled_size >= size * IOC_MIN_FILL_RATIO``.
            On timeout or exception: ``(False, Decimal("0"), price, elapsed_ms)``.
        """
        timeout_s = (ttl_ms if ttl_ms is not None else self._timeout_ms) / 1000
        start = time.monotonic()
        try:
            result = await asyncio.wait_for(
                exchange.place_ioc_limit(symbol, side, price, size),
                timeout=timeout_s,
            )
        except asyncio.TimeoutError:
            elapsed = (time.monotonic() - start) * 1000
            logger.warning("ioc_order_timeout", symbol=symbol, side=side)
            return False, Decimal("0"), price, elapsed
        except Exception:
            elapsed = (time.monotonic() - start) * 1000
            logger.warning("ioc_order_error", symbol=symbol, side=side, exc_info=True)
            return False, Decimal("0"), price, elapsed

        elapsed = (time.monotonic() - start) * 1000
        filled = result.filled_size >= size * self.IOC_MIN_FILL_RATIO
        return filled, result.filled_size, result.avg_price, elapsed

    async def execute(
        self,
        exchange: ExchangeOrderAPI,
        symbol: str,
        side: str,
        price: Decimal,
        size: Decimal,
        signal_id: str = "",
        book: OrderBook | None = None,
    ) -> OrderResult:
        """Try IOC limit; fall back to market for remainder if partial or timed out."""
        # US-275-a: scale size to available liquidity
        min_order_size = Decimal(get_settings().operational.min_order_size)
        if self._depth_analyzer and book and self.enable_depth_sizing:
            side_str = "ask" if side in ("buy", "bid") else "bid"
            available = self._depth_analyzer.liquidity_at_pct_depth(book, Decimal("1"), side_str)
            if available < size:
                scale_factor = available * Decimal("0.8") / size
                size = size * scale_factor
                if size < min_order_size:
                    logger.warning("depth_rejected: size=%s < min=%s", size, min_order_size)
                    return OrderResult(
                        filled_size=Decimal("0"),
                        avg_price=Decimal("0"),
                        order_type="depth_rejected",
                        latency_ms=0.0,
                    )

        # US-153: Idempotency check — deduplicate orders within a 5-min window
        if signal_id:
            exchange_id = getattr(exchange, "exchange_id", str(id(exchange)))
            idem_key = f"{exchange_id}:{symbol}:{signal_id}"
            self._cleanup_old_keys()
            if idem_key in self._executed_keys:
                logger.warning(
                    "duplicate_order_skipped",
                    key=idem_key, symbol=symbol, side=side,
                )
                return OrderResult(
                    filled_size=Decimal("0"),
                    avg_price=price,
                    order_type="duplicate_skip",
                    latency_ms=0.0,
                )
            self._executed_keys[idem_key] = time.time()

        start = time.monotonic()

        # Path-B v2 Day 11: delegate the IOC half to the reusable primitive.
        # Keeps observable behaviour (metrics, return shape) byte-compatible.
        filled, ioc_filled_size, ioc_avg_price, _ioc_elapsed_ms = await self.try_ioc(
            exchange, symbol, side, price, size, ttl_ms=self._timeout_ms
        )
        if filled:
            slippage = abs(float((ioc_avg_price - price) / price)) * 10_000 if price else 0.0
            self._ioc_fills += 1
            self._ioc_slippage_sum += slippage
            self._update_metrics(slippage, via_ioc=True)
            elapsed = (time.monotonic() - start) * 1000
            return OrderResult(
                filled_size=ioc_filled_size,
                avg_price=ioc_avg_price,
                order_type="ioc_limit",
                latency_ms=elapsed,
            )
        # Partial fill / timeout / error: carry forward whatever IOC filled
        # (can be Decimal("0")) and fall through to market fallback.
        remaining = size - ioc_filled_size
        ioc_filled = ioc_filled_size

        # Market fallback for remaining quantity (HIGH FIX: add timeout)
        try:
            market_result = await asyncio.wait_for(
                exchange.place_market(symbol, side, remaining),
                timeout=self._timeout_ms / 1000 * 2,
            )
        except asyncio.TimeoutError:
            logger.error("market_fallback_timeout symbol=%s side=%s remaining=%s", symbol, side, remaining)
            elapsed = (time.monotonic() - start) * 1000
            return OrderResult(
                filled_size=ioc_filled,
                avg_price=price,
                order_type="market_timeout",
                latency_ms=elapsed,
            )
        slippage = abs(float((market_result.avg_price - price) / price)) * 10_000 if price else 0.0
        self._market_fills += 1
        self._market_slippage_sum += slippage
        self._update_metrics(slippage, via_ioc=False)

        total_filled = ioc_filled + market_result.filled_size
        elapsed = (time.monotonic() - start) * 1000

        # US-275: partial fill stop — close partial position internally
        order_type = "market_fallback"
        if self.enable_partial_fill_stop and total_filled > 0 and total_filled < size * Decimal("0.95"):
            close_side = "sell" if side in ("buy", "bid") else "buy"
            await self._close_partial(exchange, symbol, close_side, total_filled, market_result.avg_price)
            order_type = "partial_closed"

        return OrderResult(
            filled_size=total_filled,
            avg_price=market_result.avg_price,
            order_type=order_type,
            latency_ms=elapsed,
        )

    async def _close_partial(
        self,
        exchange: ExchangeOrderAPI,
        symbol: str,
        side: str,
        remaining: Decimal,
        entry_price: Decimal,
    ) -> OrderResult:
        """Close a partial position with a reverse market order (US-275)."""
        start = time.monotonic()
        try:
            result = await asyncio.wait_for(
                exchange.place_market(symbol, side, remaining),
                timeout=self.partial_fill_timeout_s,
            )
            if entry_price and entry_price > 0:
                loss_pct = abs(float((result.avg_price - entry_price) / entry_price)) * 100
                if loss_pct > self.max_loss_pct:
                    logger.critical(
                        "partial_fill_loss_exceeds_limit",
                        symbol=symbol,
                        loss_pct=round(loss_pct, 4),
                        max_loss_pct=self.max_loss_pct,
                        entry_price=float(entry_price),
                        close_price=float(result.avg_price),
                    )
            elapsed = (time.monotonic() - start) * 1000
            return OrderResult(
                filled_size=result.filled_size,
                avg_price=result.avg_price,
                order_type="partial_close",
                latency_ms=elapsed,
            )
        except asyncio.TimeoutError:
            logger.error(
                "partial_close_timeout",
                symbol=symbol, side=side, remaining=float(remaining),
            )
            elapsed = (time.monotonic() - start) * 1000
            return OrderResult(
                filled_size=Decimal("0"),
                avg_price=entry_price,
                order_type="partial_close_timeout",
                latency_ms=elapsed,
            )

    def _update_metrics(self, slippage_bps: float, *, via_ioc: bool) -> None:
        IOC_FILL_RATE.set(self.fill_quality.fill_rate)
        IOC_VS_MARKET.observe(slippage_bps)

    @property
    def fill_quality(self) -> FillQuality:
        total = self._ioc_fills + self._market_fills
        return FillQuality(
            ioc_slippage_bps=self._ioc_slippage_sum / max(self._ioc_fills, 1),
            market_slippage_bps=self._market_slippage_sum / max(self._market_fills, 1),
            fill_rate=self._ioc_fills / max(total, 1),
        )

    # US-331: Leg risk detection
    def record_leg_risk(self, symbol: str = "", buy_filled: bool = True, sell_filled: bool = True) -> None:
        """Record a leg risk event when one side of a two-leg trade fails."""
        if buy_filled == sell_filled:
            return  # Both filled or both failed — not a leg risk
        self._leg_risk_events += 1
        logger.warning(
            "leg_risk_detected",
            symbol=symbol,
            buy_filled=buy_filled,
            sell_filled=sell_filled,
            total_events=self._leg_risk_events,
        )

    def get_leg_risk_count(self) -> int:
        """US-331: Return total leg risk events."""
        return self._leg_risk_events
