"""BUG-228c: Runtime MinNotionalRegistry — replaces hardcoded execution.exchange_min_notional.

Looks up per-exchange adapters (injected at construction) and delegates to the
adapter's ``get_min_notional(symbol)`` method. Adapters are responsible for
caching; the registry adds a thin safety layer (fail-open → Decimal("5")).
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_FALLBACK_USD = Decimal("5")


class MinNotionalRegistry:
    """Thin wrapper over a dict of exchange_id → adapter that proxies
    ``get_min_notional`` calls.

    Fail-open contract: every failure mode (unknown adapter, exception raised
    by adapter, adapter returns None) returns ``Decimal("5")`` so that the
    engine keeps its global min_trade_notional_usd floor.
    """

    def __init__(self, exchanges: dict[str, Any] | None = None) -> None:
        # Hold a reference (not a copy) so mutations by main.py during
        # exchange registration are visible. Empty dict when tests omit it.
        self._exchanges: dict[str, Any] = exchanges if exchanges is not None else {}

    async def get(self, exchange_id: str, symbol: str) -> Decimal:
        """Return the exchange's required min notional (USD or KRW).

        Missing adapter or adapter failure → Decimal("5"). Callers compare
        against leg.size * leg.price (same quote unit as the symbol).
        """
        adapter = self._exchanges.get(exchange_id)
        if adapter is None:
            logger.warning(
                "min_notional_registry.adapter_missing exchange=%s symbol=%s fallback=$%s",
                exchange_id, symbol, _DEFAULT_FALLBACK_USD,
            )
            return _DEFAULT_FALLBACK_USD
        _get = getattr(adapter, "get_min_notional", None)
        if _get is None:
            logger.warning(
                "min_notional_registry.method_missing exchange=%s symbol=%s fallback=$%s",
                exchange_id, symbol, _DEFAULT_FALLBACK_USD,
            )
            return _DEFAULT_FALLBACK_USD
        try:
            result = await _get(symbol)
            if result is None:
                return _DEFAULT_FALLBACK_USD
            return Decimal(str(result))
        except Exception as exc:
            logger.warning(
                "min_notional_registry.call_failed exchange=%s symbol=%s err=%s fallback=$%s",
                exchange_id, symbol, exc, _DEFAULT_FALLBACK_USD,
            )
            return _DEFAULT_FALLBACK_USD
