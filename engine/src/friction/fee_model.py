"""Exchange fee calculator — maker/taker per tier, per exchange.

Includes trading fees, withdrawal fees, and network cost estimation
for cross-exchange arbitrage friction modeling (Amendment 3D).

Fee data sourced from official exchange docs (2026-03, researcher-1 verified).
"""
from __future__ import annotations

from dataclasses import dataclass, field
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
# Sources: Binance/Bybit/OKX/Bitget official fee pages, Upbit/Bithumb/Coinone Korean docs
DEFAULT_FEES: dict[str, list[FeeConfig]] = {
    "binance": [
        FeeConfig("binance", 0, Decimal("0.0010"), Decimal("0.0010")),  # Spot VIP0 (0.10%)
        FeeConfig("binance", 1, Decimal("0.0009"), Decimal("0.0010")),  # VIP1
        FeeConfig("binance", 2, Decimal("0.0008"), Decimal("0.0010")),  # VIP2
    ],
    "okx": [
        FeeConfig("okx", 0, Decimal("0.0008"), Decimal("0.0010")),  # Regular
        FeeConfig("okx", 1, Decimal("0.0007"), Decimal("0.0009")),  # VIP1
    ],
    "bybit": [
        FeeConfig("bybit", 0, Decimal("0.0010"), Decimal("0.0010")),  # Spot VIP0 (0.10%/0.10%)
        FeeConfig("bybit", 1, Decimal("0.0008"), Decimal("0.0009")),  # VIP1
    ],
    "bitget": [
        FeeConfig("bitget", 0, Decimal("0.0010"), Decimal("0.0010")),  # Spot VIP0
        FeeConfig("bitget", 1, Decimal("0.0008"), Decimal("0.0009")),  # VIP1
    ],
    "upbit": [
        FeeConfig("upbit", 0, Decimal("0.0005"), Decimal("0.00139")),  # KRW: maker 0.05%, taker 0.139%
    ],
    "bithumb": [
        FeeConfig("bithumb", 0, Decimal("0.0025"), Decimal("0.0025")),  # KRW base rate
        FeeConfig("bithumb", 1, Decimal("0.0005"), Decimal("0.0014")),  # coupon applied
    ],
    "coinone": [
        FeeConfig("coinone", 0, Decimal("0.0002"), Decimal("0.0002")),  # API rate (0.02%)
    ],
    "binance_futures": [
        FeeConfig("binance_futures", 0, Decimal("0.0002"), Decimal("0.0005")),  # USDT-M VIP0
        FeeConfig("binance_futures", 1, Decimal("0.0000"), Decimal("0.0004")),  # VIP1
    ],
    "bybit_futures": [
        FeeConfig("bybit_futures", 0, Decimal("0.0002"), Decimal("0.00055")),  # VIP0
        FeeConfig("bybit_futures", 1, Decimal("0.00016"), Decimal("0.0005")),  # VIP1
    ],
}

# Withdrawal fees in USD per exchange per coin (cheapest recommended network)
# Sources: withdrawalfees.com, chaincost.app, official exchange withdrawal pages (2026-03)
WITHDRAWAL_FEES_USD: dict[str, dict[str, Decimal]] = {
    "binance": {
        "BTC": Decimal("1.39"),    # Bitcoin network
        "ETH": Decimal("0.06"),    # Arbitrum One (cheapest)
        "XRP": Decimal("0.40"),    # XRP Ledger
        "USDT": Decimal("0.01"),   # BSC (cheapest)
        "DEFAULT": Decimal("0.40"),
    },
    "bybit": {
        "BTC": Decimal("12.40"),   # Bitcoin network (expensive!)
        "ETH": Decimal("0.19"),    # Arbitrum
        "XRP": Decimal("0.40"),    # XRP Ledger
        "USDT": Decimal("1.00"),   # BSC/Arbitrum
        "DEFAULT": Decimal("1.00"),
    },
    "okx": {
        "BTC": Decimal("1.50"),    # Bitcoin network
        "ETH": Decimal("0.10"),    # Arbitrum
        "XRP": Decimal("0.40"),    # XRP Ledger
        "USDT": Decimal("0.10"),   # Arbitrum
        "DEFAULT": Decimal("0.40"),
    },
    "bitget": {
        "BTC": Decimal("1.50"),    # Bitcoin network
        "ETH": Decimal("0.10"),    # Arbitrum
        "XRP": Decimal("0.50"),    # XRP Ledger
        "USDT": Decimal("0.00"),   # BSC/TRC20 FREE
        "DEFAULT": Decimal("0.50"),
    },
    "upbit": {
        "BTC": Decimal("8.40"),    # 0.0009 BTC
        "ETH": Decimal("4.50"),    # 0.018 ETH (no L2 support)
        "XRP": Decimal("0.60"),    # 1 XRP
        "USDT": Decimal("5.00"),   # limited networks
        "DEFAULT": Decimal("0.60"),
    },
    "bithumb": {
        "BTC": Decimal("9.30"),    # ~0.001 BTC
        "ETH": Decimal("2.50"),    # ~0.01 ETH
        "XRP": Decimal("0.60"),    # 1 XRP
        "USDT": Decimal("5.00"),   # limited networks
        "DEFAULT": Decimal("0.60"),
    },
    "coinone": {
        "BTC": Decimal("9.30"),    # 0.001 BTC
        "ETH": Decimal("2.50"),    # 0.01 ETH
        "XRP": Decimal("0.60"),    # ~1 XRP (estimated)
        "USDT": Decimal("5.00"),   # limited
        "DEFAULT": Decimal("0.60"),
    },
    "binance_futures": {
        "DEFAULT": Decimal("0.00"),  # internal transfer to spot (free)
    },
    "bybit_futures": {
        "DEFAULT": Decimal("0.00"),  # internal transfer (free)
    },
}


class FeeModel:
    """
    Exchange fee calculator supporting maker/taker rates per exchange and tier,
    plus withdrawal fee lookup for cross-exchange transfer cost estimation.

    Usage:
        model = FeeModel()
        model.set_tier("binance", 1)
        fee = model.taker_fee("binance", notional=Decimal("50000"))
        withdraw = model.withdrawal_fee("binance", "XRP")
        network = model.network_cost("binance", "bybit", "XRP")
    """

    def __init__(
        self,
        custom_fees: dict[str, list[FeeConfig]] | None = None,
        custom_withdrawal_fees: dict[str, dict[str, Decimal]] | None = None,
    ) -> None:
        self._fees = custom_fees if custom_fees is not None else DEFAULT_FEES
        self._withdrawal_fees = (
            custom_withdrawal_fees
            if custom_withdrawal_fees is not None
            else WITHDRAWAL_FEES_USD
        )
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

    def withdrawal_fee(self, exchange: str, coin: str = "XRP") -> Decimal:
        """Return withdrawal fee in USD for a coin from an exchange.

        Falls back to DEFAULT if coin not found, then Decimal("1.00") if exchange unknown.
        """
        ex = exchange.removeprefix("paper_").removeprefix("sandbox_")
        ex_fees = self._withdrawal_fees.get(ex)
        if not ex_fees:
            return Decimal("1.00")  # conservative default
        return ex_fees.get(coin.upper(), ex_fees.get("DEFAULT", Decimal("1.00")))

    def network_cost(
        self, from_exchange: str, to_exchange: str, coin: str = "XRP"
    ) -> Decimal:
        """Estimate one-way network transfer cost (USD) between two exchanges.

        For same-exchange or futures↔spot internal transfers, returns 0.
        For cross-exchange, returns the withdrawal fee from the source exchange.
        """
        src = from_exchange.removeprefix("paper_").removeprefix("sandbox_")
        dst = to_exchange.removeprefix("paper_").removeprefix("sandbox_")
        # Internal transfers (futures ↔ spot on same exchange)
        src_base = src.replace("_futures", "")
        dst_base = dst.replace("_futures", "")
        if src_base == dst_base:
            return Decimal("0")
        return self.withdrawal_fee(src, coin)

    def round_trip_fee_rate(self, buy_exchange: str, sell_exchange: str) -> Decimal:
        """Return combined taker fee rate for a round-trip (buy + sell)."""
        return self.taker_rate(buy_exchange) + self.taker_rate(sell_exchange)
