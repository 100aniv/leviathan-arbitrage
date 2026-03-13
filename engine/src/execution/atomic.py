"""Atomic order executor — IOC limit with market fallback.

US-119: Try IOC limit first; fall back to market order if partially filled or timed out.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

import httpx
import structlog

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

    def __init__(self, timeout_ms: float = 1000) -> None:
        self._timeout_ms = timeout_ms
        self._ioc_fills: int = 0
        self._market_fills: int = 0
        self._ioc_slippage_sum: float = 0.0
        self._market_slippage_sum: float = 0.0
        self._executed_keys: dict[str, float] = {}  # US-153: key → timestamp

    def _cleanup_old_keys(self) -> None:
        """Remove idempotency keys older than 5 minutes (US-153)."""
        cutoff = time.time() - 300
        expired = [k for k, ts in self._executed_keys.items() if ts < cutoff]
        for k in expired:
            del self._executed_keys[k]

    async def execute(
        self,
        exchange: ExchangeOrderAPI,
        symbol: str,
        side: str,
        price: Decimal,
        size: Decimal,
        signal_id: str = "",
    ) -> OrderResult:
        """Try IOC limit; fall back to market for remainder if partial or timed out."""
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
        remaining = size

        try:
            result = await asyncio.wait_for(
                exchange.place_ioc_limit(symbol, side, price, size),
                timeout=self._timeout_ms / 1000,
            )
            if result.filled_size >= size * self.IOC_MIN_FILL_RATIO:
                slippage = abs(float((result.avg_price - price) / price)) * 10_000 if price else 0.0
                self._ioc_fills += 1
                self._ioc_slippage_sum += slippage
                self._update_metrics(slippage, via_ioc=True)
                elapsed = (time.monotonic() - start) * 1000
                return OrderResult(
                    filled_size=result.filled_size,
                    avg_price=result.avg_price,
                    order_type="ioc_limit",
                    latency_ms=elapsed,
                )
            remaining = size - result.filled_size
        except asyncio.TimeoutError:
            logger.warning("ioc_order_timeout", symbol=symbol, side=side)
            ioc_filled = Decimal("0")
            remaining = size
        except Exception:
            logger.warning("ioc_order_error", symbol=symbol, side=side, exc_info=True)
            ioc_filled = Decimal("0")
            remaining = size
        else:
            ioc_filled = result.filled_size if remaining < size else Decimal("0")

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

        elapsed = (time.monotonic() - start) * 1000
        return OrderResult(
            filled_size=ioc_filled + market_result.filled_size,
            avg_price=market_result.avg_price,
            order_type="market_fallback",
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
