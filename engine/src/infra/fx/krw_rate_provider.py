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

_UPBIT_TICKER_URL = "https://api.upbit.com/v1/ticker?markets=KRW-USDT"
_POLL_INTERVAL_S = 30.0
_STALE_MAX_S = 60.0


class KRWRateProvider:
    """Live USDT/KRW rate from Upbit. Returns 1/price as KRW→USDT multiplier."""

    def __init__(self, fallback_rate: Decimal = Decimal("0.000676")) -> None:
        self._fallback = fallback_rate
        self._rate: Decimal = fallback_rate  # 1 KRW = N USDT
        self._usdt_krw_price: Decimal = Decimal("0")  # 1 USDT = N KRW
        self._last_update: float = 0.0
        self._task: Optional[asyncio.Task] = None
        self._running = False

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        logger.info("KRWRateProvider started (fallback=%s KRW/USDT)", self._fallback)

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    async def _poll_loop(self) -> None:
        while self._running:
            try:
                await self._fetch_rate()
            except Exception as exc:
                logger.warning("KRWRateProvider.fetch_failed: %s", exc)
            try:
                await asyncio.sleep(_POLL_INTERVAL_S)
            except asyncio.CancelledError:
                break

    async def _fetch_rate(self) -> None:
        async with httpx.AsyncClient(timeout=5.0) as cl:
            r = await cl.get(_UPBIT_TICKER_URL)
            r.raise_for_status()
            data = r.json()
            if not data:
                return
            usdt_krw = Decimal(str(data[0]["trade_price"]))
            if usdt_krw <= 0:
                return
            self._usdt_krw_price = usdt_krw
            self._rate = Decimal("1") / usdt_krw
            self._last_update = time.monotonic()
            logger.info(
                "KRWRateProvider.updated usdt_krw=%s krw_usdt=%s",
                usdt_krw, round(float(self._rate), 8),
            )

    def get_rate(self) -> Decimal:
        """Return 1 KRW → N USDT multiplier. Falls back to config if stale."""
        if self._last_update == 0.0:
            return self._fallback
        age = time.monotonic() - self._last_update
        if age > _STALE_MAX_S:
            logger.warning("KRWRateProvider.stale age=%.1fs — using fallback", age)
            return self._fallback
        return self._rate
