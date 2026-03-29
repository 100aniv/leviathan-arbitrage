"""Market Impact Cost Model — Almgren-Chriss linear approximation.

US-284: Estimates market impact in basis points for a given order size
relative to daily volume. Used as a signal filter only — never applied
to fill prices in PaperExecutor.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_ETA_DEFAULT = float(os.getenv("MARKET_IMPACT_ETA", "0.1"))


def estimate_market_impact(
    size_usd: float,
    daily_volume_usd: float,
    eta: float = _ETA_DEFAULT,
) -> float:
    """Almgren-Chriss linear approximation. Returns impact in basis points.

    impact_bps = eta * (size / ADV) * 10_000

    Args:
        size_usd: Order notional in USD.
        daily_volume_usd: 24h average daily volume in USD.
        eta: Market impact coefficient (default 0.1 from MARKET_IMPACT_ETA env).

    Returns:
        Estimated market impact in basis points (float >= 0).
    """
    if daily_volume_usd <= 0:
        return 0.0
    ratio = size_usd / daily_volume_usd
    if ratio > 0.01:
        logger.warning(
            "market_impact.large_order: ratio=%.4f (>1%%) size_usd=%.2f adv=%.2f",
            ratio, size_usd, daily_volume_usd,
        )
    return eta * ratio * 10_000
