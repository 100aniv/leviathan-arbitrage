"""LEVIATHAN Paper Mode — canonical import path (Phase I+).

This module re-exports all public symbols from ``src.modes.shadow`` so that
callers can use either import path:

    from src.modes.paper import PaperMode          # preferred (Phase I+)
    from src.modes.shadow import ShadowMode        # legacy alias (still works)

The actual implementation lives in ``src.modes.shadow`` for now to minimise
churn on the 40+ test files that import from that path.  A future cleanup
pass can inline the implementation here and reduce shadow.py to a pure shim.
"""
from __future__ import annotations

from src.modes.shadow import (  # noqa: F401
    BookWalkSlippage,
    PaperMode,
    PaperRateLimiter,
    PaperStats,
    PowerLawSlippage,
    ROUTING_FALLBACK_TOTAL,
    ShadowMode,
    StrategyStats,
    VirtualBalanceTracker,
)

# Backward-compatibility aliases (Phase L rename: Shadow → Paper)
ShadowRateLimiter = PaperRateLimiter
ShadowStats = PaperStats

__all__ = [
    "PaperMode",
    "PaperRateLimiter",
    "PaperStats",
    "ShadowMode",        # backward-compat alias
    "ShadowRateLimiter", # backward-compat alias
    "ShadowStats",       # backward-compat alias
    "BookWalkSlippage",
    "PowerLawSlippage",
    "ROUTING_FALLBACK_TOTAL",
    "StrategyStats",
    "VirtualBalanceTracker",
]
