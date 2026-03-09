"""Token bucket rate limiter with per-exchange, per-endpoint configuration."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field


@dataclass
class RateLimitConfig:
    """Per-endpoint rate limit configuration."""

    requests_per_second: float
    burst: int = 1


@dataclass
class TokenBucket:
    """Thread-safe async token bucket rate limiter."""

    rate: float   # tokens per second
    capacity: float  # max burst tokens
    _tokens: float = field(init=False)
    _last_refill: float = field(init=False)
    _lock: asyncio.Lock = field(init=False)

    def __post_init__(self) -> None:
        self._tokens = float(self.capacity)
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
        self._last_refill = now

    def try_acquire(self, tokens: float = 1.0) -> bool:
        """Non-blocking acquire. Returns True if tokens consumed, False if insufficient."""
        self._refill()
        if self._tokens >= tokens:
            self._tokens -= tokens
            return True
        return False

    async def acquire(self, tokens: float = 1.0) -> None:
        async with self._lock:
            while True:
                self._refill()
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return
                wait = (tokens - self._tokens) / self.rate
                await asyncio.sleep(wait)


class ExchangeRateLimiter:
    """Per-exchange configurable rate limiter with multiple named buckets."""

    def __init__(self, exchange_id: str, configs: dict[str, RateLimitConfig]) -> None:
        self.exchange_id = exchange_id
        self._buckets: dict[str, TokenBucket] = {
            name: TokenBucket(rate=cfg.requests_per_second, capacity=float(cfg.burst))
            for name, cfg in configs.items()
        }

    async def acquire(self, endpoint: str = "default", tokens: float = 1.0) -> None:
        """Acquire tokens from the named bucket, falling back to 'default'."""
        bucket = self._buckets.get(endpoint) or self._buckets.get("default")
        if bucket:
            await bucket.acquire(tokens)


# Default rate limit configs per exchange (conservative, safe defaults)
DEFAULT_RATE_LIMITS: dict[str, dict[str, RateLimitConfig]] = {
    "binance": {
        "default": RateLimitConfig(requests_per_second=10, burst=20),
        "order": RateLimitConfig(requests_per_second=5, burst=10),
    },
    "binanceusdm": {
        "default": RateLimitConfig(requests_per_second=10, burst=20),
        "order": RateLimitConfig(requests_per_second=5, burst=10),
    },
    "bybit": {
        "default": RateLimitConfig(requests_per_second=5, burst=10),
        "order": RateLimitConfig(requests_per_second=5, burst=10),
    },
    "okx": {
        "default": RateLimitConfig(requests_per_second=10, burst=20),
        "order": RateLimitConfig(requests_per_second=6, burst=12),
    },
    "upbit": {
        "default": RateLimitConfig(requests_per_second=10, burst=10),
        "order": RateLimitConfig(requests_per_second=8, burst=8),
    },
    "bithumb": {
        "default": RateLimitConfig(requests_per_second=5, burst=10),
        "order": RateLimitConfig(requests_per_second=3, burst=5),
    },
    "coinone": {
        "default": RateLimitConfig(requests_per_second=3, burst=5),
        "order": RateLimitConfig(requests_per_second=2, burst=3),
    },
}
