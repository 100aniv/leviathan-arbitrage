"""Power-law slippage model per Amendment 6 (NOT exponential).

Square-root impact model:
    Impact = sigma * k * sqrt(size / ADV)

Power-law decay:
    Impact_decay(t) = Impact_0 * (1 + t/t_0)^(-gamma),  gamma=0.5, t_0=60s

Cross-venue propagation:
    Impact_B(t) = alpha_AB * Impact_A * (1 + t/t_prop)^(-gamma)

Cold-start: k_initial = 1.5 * k_fitted (conservative multiplier)

Confidence intervals based on extrapolation distance (size / ADV):
    <= 1x: ±20%
    1-3x:  ±50%
    3-10x: ±100%
    >10x:  DO NOT TRADE (extremely wide CI)
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol, runtime_checkable

from src.core.order_book import OrderBook


@dataclass
class SlippagePrediction:
    expected: Decimal
    lower: Decimal
    upper: Decimal
    model_type: str
    extrapolation_distance: float  # ratio of order_size to ADV


@runtime_checkable
class SlippageModel(Protocol):
    def predict(
        self,
        book: OrderBook,
        size: Decimal,
        adv: Decimal,
        sigma: Decimal,
    ) -> SlippagePrediction: ...

    def impact_decay(
        self, impact_0: float, t: float, t_0: float, gamma: float
    ) -> float: ...

    def cross_venue_impact(
        self, impact_a: float, t: float, alpha_ab: float, t_prop: float, gamma: float
    ) -> float: ...


class CEXOrderbookSlippage:
    """
    Power-law market impact model for CEX orderbooks (Amendment 6).

    Uses square-root impact model with power-law decay.
    Cold-start: apply 1.5x conservative multiplier until calibrated.

    GAMMA and k are configurable via environment variables:
      - SLIPPAGE_GAMMA (default 0.5)
      - SLIPPAGE_K_DEFAULT (default 1.0)
      - SLIPPAGE_CONSERVATIVE_MULTIPLIER (default 1.5)
    """

    COLD_START_MULTIPLIER = Decimal(os.getenv("SLIPPAGE_CONSERVATIVE_MULTIPLIER", "1.5"))
    GAMMA = float(os.getenv("SLIPPAGE_GAMMA", "0.5"))
    T_0 = 60.0  # seconds

    # Flag indicating whether GAMMA has been calibrated against live data.
    GAMMA_CALIBRATED: bool = os.getenv("SLIPPAGE_GAMMA_CALIBRATED", "false").lower() == "true"

    def __init__(
        self,
        k: Decimal = Decimal(os.getenv("SLIPPAGE_K_DEFAULT", "1.0")),
        cold_start: bool = True,
    ) -> None:
        self.k = k
        self.cold_start = cold_start

    def predict(
        self,
        book: OrderBook,
        size: Decimal,
        adv: Decimal,
        sigma: Decimal,
    ) -> SlippagePrediction:
        """
        Predict market impact slippage.

        Args:
            book:  Current orderbook (used for mid-price).
            size:  Order size in base asset units.
            adv:   Average Daily Volume in same units as size.
            sigma: Price volatility (e.g., 0.01 = 1%).

        Returns SlippagePrediction with expected impact and confidence bounds.
        """
        if adv <= 0:
            raise ValueError("ADV must be positive")

        # Support both property-based (models.OrderBook) and method-based (order_book.OrderBook)
        _ask = book.best_ask
        _bid = book.best_bid
        best_ask = _ask() if callable(_ask) else _ask
        best_bid = _bid() if callable(_bid) else _bid
        if best_ask is None or best_bid is None:
            raise ValueError("Empty orderbook — cannot compute slippage")

        k = self.k
        if self.cold_start:
            k = k * self.COLD_START_MULTIPLIER

        ratio = float(size / adv)
        # Impact as fraction of price: sigma * k * sqrt(size/ADV)
        impact_fraction = float(sigma) * float(k) * math.sqrt(ratio)

        mid = (best_ask + best_bid) / 2
        expected_abs = Decimal(str(impact_fraction)) * mid

        # Confidence intervals based on extrapolation distance
        if ratio <= 1.0:
            ci_pct = Decimal("0.20")
        elif ratio <= 3.0:
            ci_pct = Decimal("0.50")
        elif ratio <= 10.0:
            ci_pct = Decimal("1.00")
        else:
            # >10x ADV: flag as do-not-trade via very wide CI
            ci_pct = Decimal("10.0")

        lower = max(Decimal("0"), expected_abs * (1 - ci_pct))
        upper = expected_abs * (1 + ci_pct)

        return SlippagePrediction(
            expected=expected_abs,
            lower=lower,
            upper=upper,
            model_type="cex_sqrt_power_law",
            extrapolation_distance=ratio,
        )

    def impact_decay(
        self,
        impact_0: float,
        t: float,
        t_0: float = 60.0,
        gamma: float = 0.5,
    ) -> float:
        """
        Power-law impact decay (Amendment 6).

        Impact_decay(t) = Impact_0 * (1 + t/t_0)^(-gamma)

        NOT exponential — power-law decays slower, which is empirically accurate.
        """
        return impact_0 * (1 + t / t_0) ** (-gamma)

    def cross_venue_impact(
        self,
        impact_a: float,
        t: float,
        alpha_ab: float,
        t_prop: float = 60.0,
        gamma: float = 0.5,
    ) -> float:
        """
        Cross-venue impact propagation.

        Impact_B(t) = alpha_AB * Impact_A * (1 + t/t_prop)^(-gamma)

        Args:
            impact_a:  Initial impact on venue A.
            t:         Time elapsed since impact (seconds).
            alpha_ab:  Cross-venue propagation coefficient (0-1).
            t_prop:    Propagation time scale (seconds).
            gamma:     Decay exponent (default 0.5).
        """
        return alpha_ab * impact_a * (1 + t / t_prop) ** (-gamma)
