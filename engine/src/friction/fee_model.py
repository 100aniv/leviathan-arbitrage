"""Exchange fee calculator — maker/taker per tier, per exchange."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


class FeeType(StrEnum):
    MAKER = "maker"
    TAKER = "taker"


@dataclass
class FeeConfig:
    exchange: str
    tier: int
    maker_rate: Decimal
    taker_rate: Decimal


# Default fee schedules per exchange (tier 0 = retail/VIP0)
DEFAULT_FEES: dict[str, list[FeeConfig]] = {
    "binance": [
        FeeConfig("binance", 0, Decimal("0.0010"), Decimal("0.0010")),  # VIP0
        FeeConfig("binance", 1, Decimal("0.0009"), Decimal("0.0009")),  # VIP1
        FeeConfig("binance", 2, Decimal("0.0008"), Decimal("0.0008")),  # VIP2
    ],
    "okx": [
        FeeConfig("okx", 0, Decimal("0.0008"), Decimal("0.0010")),
        FeeConfig("okx", 1, Decimal("0.0007"), Decimal("0.0009")),
    ],
    "bybit": [
        FeeConfig("bybit", 0, Decimal("0.0001"), Decimal("0.0006")),
        FeeConfig("bybit", 1, Decimal("0.0000"), Decimal("0.0005")),
    ],
}


class FeeModel:
    """
    Exchange fee calculator supporting maker/taker rates per exchange and tier.

    Usage:
        model = FeeModel()
        model.set_tier("binance", 1)
        fee = model.taker_fee("binance", notional=Decimal("50000"))
    """

    def __init__(self, custom_fees: dict[str, list[FeeConfig]] | None = None) -> None:
        self._fees = custom_fees if custom_fees is not None else DEFAULT_FEES
        self._tiers: dict[str, int] = {}

    def set_tier(self, exchange: str, tier: int) -> None:
        """Set the fee tier for an exchange."""
        self._tiers[exchange] = tier

    def get_tier(self, exchange: str) -> int:
        """Return current fee tier for an exchange (default 0)."""
        return self._tiers.get(exchange, 0)

    def _get_config(self, exchange: str) -> FeeConfig:
        configs = self._fees.get(exchange)
        if not configs:
            raise ValueError(f"Unknown exchange: {exchange}")
        tier = self.get_tier(exchange)
        for config in configs:
            if config.tier == tier:
                return config
        return configs[0]  # fallback to tier 0

    def maker_fee(self, exchange: str, notional: Decimal) -> Decimal:
        """Compute absolute maker fee for a given notional."""
        return notional * self._get_config(exchange).maker_rate

    def taker_fee(self, exchange: str, notional: Decimal) -> Decimal:
        """Compute absolute taker fee for a given notional."""
        return notional * self._get_config(exchange).taker_rate

    def fee(self, exchange: str, notional: Decimal, fee_type: FeeType) -> Decimal:
        """Compute fee by FeeType enum."""
        if fee_type == FeeType.MAKER:
            return self.maker_fee(exchange, notional)
        return self.taker_fee(exchange, notional)

    def maker_rate(self, exchange: str) -> Decimal:
        """Return maker rate fraction for exchange at current tier."""
        return self._get_config(exchange).maker_rate

    def taker_rate(self, exchange: str) -> Decimal:
        """Return taker rate fraction for exchange at current tier."""
        return self._get_config(exchange).taker_rate
