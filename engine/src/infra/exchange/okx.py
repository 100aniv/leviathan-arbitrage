"""OKX adapter."""
from __future__ import annotations

from typing import Any

from src.infra.exchange.ccxt_adapter import CCXTAdapter


class OKXAdapter(CCXTAdapter):
    """OKX adapter supporting spot and futures."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(exchange_id="okx", **kwargs)
