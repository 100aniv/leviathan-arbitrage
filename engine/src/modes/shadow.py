"""LEVIATHAN Shadow Mode — DEPRECATED backward-compat shim (Phase 3, 2026-04-26).

This module is the historical name. All implementation has been moved to
``src.modes.paper``. This shim re-exports public symbols AND forwards all
internal attribute access (e.g. `src.modes.shadow.asyncio` for test mocks)
so 40+ legacy test files continue to import from ``src.modes.shadow`` without
breakage.

**New code should always import from `src.modes.paper`.**

Removal plan: this shim will be deleted after all callers migrate (별도 Day).
"""
from __future__ import annotations

# Forward all attribute lookups to src.modes.paper. Tests like
# `patch("src.modes.shadow.asyncio.sleep", ...)` rely on this — they expect
# every symbol present in paper.py to be reachable via shadow as well.
import src.modes.paper as _paper_impl

from src.modes.paper import (  # noqa: F401  (explicit re-exports for static tools)
    BookWalkSlippage,
    PaperMode,
    PaperRateLimiter,
    PaperStats,
    PowerLawSlippage,
    ROUTING_FALLBACK_TOTAL,
    ShadowMode,
    StrategyStats,
    VirtualBalanceTracker,
    logger,  # legacy tests patch src.modes.shadow.logger directly
)


def __getattr__(name: str):
    """Forward any unresolved attribute access to src.modes.paper.

    Allows `patch("src.modes.shadow.asyncio.sleep", ...)` and similar test
    patterns that target internal symbols (asyncio, time, logger, etc.).
    """
    return getattr(_paper_impl, name)

# Backward-compat aliases (Phase L rename: Shadow → Paper)
ShadowRateLimiter = PaperRateLimiter
ShadowStats = PaperStats

__all__ = [
    "PaperMode",
    "PaperRateLimiter",
    "PaperStats",
    "ShadowMode",
    "ShadowRateLimiter",
    "ShadowStats",
    "BookWalkSlippage",
    "PowerLawSlippage",
    "ROUTING_FALLBACK_TOTAL",
    "StrategyStats",
    "VirtualBalanceTracker",
]
