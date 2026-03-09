"""Rust PyO3 bridge — optional high-performance hot-path modules.

Per-module feature flags (env vars, default=false):
  USE_RUST_ORDERBOOK  — Rust BTreeMap orderbook (<5μs vs ~100μs Python)
  USE_RUST_SIGNAL     — Rust spread calculator (<5μs vs ~500μs Python)
  USE_RUST_KILLSWITCH — Rust AtomicBool halt flag (<0.01ms)

If enabled but rust_core unavailable, falls back to Python with WARNING log.
"""
from __future__ import annotations

import os
import types
from typing import TYPE_CHECKING, Any

import structlog

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Accepted truthy / falsy string values for feature flags
# ---------------------------------------------------------------------------
_TRUTHY: frozenset[str] = frozenset({"true", "1", "yes"})
_FALSY: frozenset[str] = frozenset({"false", "0", "no"})

# ---------------------------------------------------------------------------
# Module-level lazy state
# ---------------------------------------------------------------------------
_rust_module: types.ModuleType | None | bool = False  # False = not yet attempted
_flags: dict[str, bool] | None = None  # None = not yet parsed


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_feature_flag(name: str) -> bool:
    """Parse a boolean environment variable by *name*.

    Accepted values (case-insensitive):
      truthy  — ``true``, ``1``, ``yes``
      falsy   — ``false``, ``0``, ``no``

    Raises:
        ValueError: If the value is set but is not one of the accepted strings.
    """
    raw = os.environ.get(name, "false").strip().lower()
    if raw in _TRUTHY:
        return True
    if raw in _FALSY:
        return False
    raise ValueError(
        f"Invalid value for environment variable {name!r}: {os.environ[name]!r}. "
        f"Accepted values: true/false, 1/0, yes/no (case-insensitive)."
    )


def _try_import_rust() -> types.ModuleType | None:
    """Attempt to import the compiled ``rust_core`` extension module.

    Returns the module on success, or ``None`` if it cannot be imported.
    The import error is logged at DEBUG level so it is visible in development
    but does not clutter production logs when the Rust build is intentionally
    absent.
    """
    try:
        import rust_core  # type: ignore[import]

        logger.debug("rust_core_loaded", version=getattr(rust_core, "__version__", "unknown"))
        return rust_core
    except ImportError as exc:
        logger.debug("rust_core_unavailable", reason=str(exc))
        return None


def _ensure_initialized() -> None:
    """Lazily parse feature flags and attempt the Rust import on first call.

    Idempotent — subsequent calls are no-ops once state is populated.
    Raises ``ValueError`` immediately if any env var has an invalid value,
    so misconfiguration is caught at first use rather than silently ignored.
    """
    global _rust_module, _flags

    if _flags is not None:
        return  # already initialised

    # Parse all feature flags first (raises ValueError on bad values).
    parsed: dict[str, bool] = {
        "USE_RUST_ORDERBOOK": _parse_feature_flag("USE_RUST_ORDERBOOK"),
        "USE_RUST_SIGNAL": _parse_feature_flag("USE_RUST_SIGNAL"),
        "USE_RUST_KILLSWITCH": _parse_feature_flag("USE_RUST_KILLSWITCH"),
    }

    # Only attempt the Rust import when at least one flag is enabled.
    if any(parsed.values()):
        module = _try_import_rust()
        if module is None:
            # Warn for each flag that was requested but cannot be satisfied.
            for flag, enabled in parsed.items():
                if enabled:
                    logger.warning(
                        "rust_feature_unavailable_fallback_to_python",
                        flag=flag,
                        reason="rust_core import failed",
                    )
            _rust_module = None
        else:
            _rust_module = module
    else:
        _rust_module = None

    _flags = parsed
    logger.debug(
        "rust_bridge_initialized",
        use_rust_orderbook=parsed["USE_RUST_ORDERBOOK"],
        use_rust_signal=parsed["USE_RUST_SIGNAL"],
        use_rust_killswitch=parsed["USE_RUST_KILLSWITCH"],
        rust_available=_rust_module is not None,
    )


# ---------------------------------------------------------------------------
# Public accessors
# ---------------------------------------------------------------------------


class RustOrderBookWrapper:
    """Wraps Rust PyOrderBook to be compatible with Python OrderBook interface.

    Rust returns f64; this wrapper converts to Decimal at the boundary so
    the existing SignalGenerator / PriceHub pipeline works unchanged.
    The BTreeMap operations (apply_snapshot, apply_delta, best_bid, best_ask)
    still run at Rust speed (<5μs).
    """

    def __init__(self, symbol: str, exchange: str) -> None:
        from decimal import Decimal as D

        _ensure_initialized()
        self._rust_book = _rust_module.PyOrderBook(symbol, exchange)
        self.symbol = symbol
        self.exchange = exchange
        # Keep Python-side dicts for compatibility (e.g. depth iteration)
        self.bids: dict = {}
        self.asks: dict = {}

    def apply_snapshot(
        self,
        bids: list[tuple[str, str]],
        asks: list[tuple[str, str]],
    ) -> None:
        from decimal import Decimal as D

        self._rust_book.apply_snapshot(bids, asks)
        # Sync Python dicts for any code that iterates them directly
        self.bids = {D(p): D(q) for p, q in bids if D(q) > 0}
        self.asks = {D(p): D(q) for p, q in asks if D(q) > 0}

    def apply_delta(
        self,
        bid_updates: list[tuple[str, str]],
        ask_updates: list[tuple[str, str]],
    ) -> None:
        from decimal import Decimal as D

        self._rust_book.apply_delta(bid_updates, ask_updates)
        for p, q in bid_updates:
            dp, dq = D(p), D(q)
            if dq == 0:
                self.bids.pop(dp, None)
            else:
                self.bids[dp] = dq
        for p, q in ask_updates:
            dp, dq = D(p), D(q)
            if dq == 0:
                self.asks.pop(dp, None)
            else:
                self.asks[dp] = dq

    def best_bid(self):
        from decimal import Decimal as D

        val = self._rust_book.best_bid()
        return D(str(val)) if val is not None else None

    def best_ask(self):
        from decimal import Decimal as D

        val = self._rust_book.best_ask()
        return D(str(val)) if val is not None else None

    def spread(self):
        from decimal import Decimal as D

        val = self._rust_book.spread()
        return D(str(val)) if val is not None else None

    def depth_weighted_mid_price(self, depth: int = 5):
        from decimal import Decimal as D

        val = self._rust_book.depth_weighted_mid_price(depth)
        return D(str(val))

    def volume_at_price(self, price, side: str):
        from decimal import Decimal as D

        val = self._rust_book.volume_at_price(float(price), side)
        return D(str(val))

    def vwap_walk(self, side: str, size) -> tuple:
        from decimal import Decimal as D

        if side == "buy":
            levels = sorted(self.asks.items())
        elif side == "sell":
            levels = sorted(self.bids.items(), reverse=True)
        else:
            raise ValueError(f"Invalid side '{side}': must be 'buy' or 'sell'")

        if not levels:
            return (D("0"), D("0"))

        remaining = D(str(size))
        weighted_sum = D("0")
        filled = D("0")

        for price, qty in levels:
            fill_qty = min(remaining, qty)
            weighted_sum += price * fill_qty
            filled += fill_qty
            remaining -= fill_qty
            if remaining <= 0:
                break

        if filled > 0:
            return (weighted_sum / filled, filled)
        return (D("0"), D("0"))


def get_orderbook_class() -> type:
    """Return the orderbook implementation class for this process.

    Returns:
        ``RustOrderBookWrapper`` when ``USE_RUST_ORDERBOOK=true`` and
        ``rust_core`` is available; otherwise the pure-Python
        ``src.core.order_book.OrderBook``.
    """
    _ensure_initialized()
    if _flags["USE_RUST_ORDERBOOK"] and _rust_module is not None:
        cls = getattr(_rust_module, "PyOrderBook", None)
        if cls is not None:
            logger.debug("orderbook_class_resolved", backend="rust")
            return RustOrderBookWrapper
        logger.warning(
            "rust_orderbook_class_missing_fallback",
            reason="PyOrderBook not found in rust_core",
        )

    from src.core.order_book import OrderBook  # local import avoids circular deps

    logger.debug("orderbook_class_resolved", backend="python")
    return OrderBook


def get_spread_calculator() -> Any | None:
    """Return a Rust ``PySpreadCalculator`` instance when available.

    Returns:
        ``rust_core.PySpreadCalculator`` (class, not instance) when
        ``USE_RUST_SIGNAL=true`` and ``rust_core`` is available; otherwise
        ``None`` (the Python signal pipeline handles computation).
    """
    _ensure_initialized()
    if _flags["USE_RUST_SIGNAL"] and _rust_module is not None:
        cls = getattr(_rust_module, "PySpreadCalculator", None)
        if cls is not None:
            logger.debug("spread_calculator_resolved", backend="rust")
            return cls
        logger.warning(
            "rust_spread_calculator_missing_fallback",
            reason="PySpreadCalculator not found in rust_core",
        )

    logger.debug("spread_calculator_resolved", backend="python_pipeline")
    return None


def get_rust_kill_switch_functions() -> dict[str, Any] | None:
    """Return Rust kill-switch functions when available.

    Returns a dict with keys ``halt_local``, ``is_halted``, ``clear_halt``
    pointing to the Rust atomic implementations, or ``None`` if
    ``USE_RUST_KILLSWITCH`` is not enabled / ``rust_core`` is unavailable.

    The dict also includes the ``KillSwitch`` and ``KillSwitchEvent`` classes
    so callers can use the full Rust kill-switch surface area.

    Returns:
        ``dict`` with Rust callables, or ``None``.
    """
    _ensure_initialized()
    if _flags["USE_RUST_KILLSWITCH"] and _rust_module is not None:
        missing = [
            sym
            for sym in ("halt_local", "is_halted", "clear_halt")
            if not hasattr(_rust_module, sym)
        ]
        if missing:
            logger.warning(
                "rust_killswitch_symbols_missing_fallback",
                missing=missing,
                reason="symbols not found in rust_core",
            )
        else:
            result: dict[str, Any] = {
                "halt_local": _rust_module.halt_local,
                "is_halted": _rust_module.is_halted,
                "clear_halt": _rust_module.clear_halt,
            }
            # Attach optional class references when present.
            for sym in ("KillSwitch", "KillSwitchEvent"):
                obj = getattr(_rust_module, sym, None)
                if obj is not None:
                    result[sym] = obj
            logger.debug("kill_switch_functions_resolved", backend="rust")
            return result

    logger.debug("kill_switch_functions_resolved", backend="python")
    return None


# ---------------------------------------------------------------------------
# Introspection helpers
# ---------------------------------------------------------------------------


def is_rust_orderbook_enabled() -> bool:
    """Return ``True`` if the Rust orderbook is both requested and available."""
    _ensure_initialized()
    return bool(_flags["USE_RUST_ORDERBOOK"] and _rust_module is not None)


def is_rust_signal_enabled() -> bool:
    """Return ``True`` if the Rust signal calculator is both requested and available."""
    _ensure_initialized()
    return bool(_flags["USE_RUST_SIGNAL"] and _rust_module is not None)


def is_rust_killswitch_enabled() -> bool:
    """Return ``True`` if the Rust kill switch is both requested and available."""
    _ensure_initialized()
    return bool(_flags["USE_RUST_KILLSWITCH"] and _rust_module is not None)


def get_feature_flags() -> dict[str, bool]:
    """Return a snapshot of all Rust feature flag states.

    Each value reflects whether the flag was requested **and** the Rust module
    is actually available — i.e., the same logic used by the ``is_rust_*``
    helpers.

    Returns:
        ``dict`` with keys ``USE_RUST_ORDERBOOK``, ``USE_RUST_SIGNAL``,
        ``USE_RUST_KILLSWITCH`` and ``bool`` values.
    """
    _ensure_initialized()
    rust_available = _rust_module is not None
    return {
        "USE_RUST_ORDERBOOK": bool(_flags["USE_RUST_ORDERBOOK"] and rust_available),
        "USE_RUST_SIGNAL": bool(_flags["USE_RUST_SIGNAL"] and rust_available),
        "USE_RUST_KILLSWITCH": bool(_flags["USE_RUST_KILLSWITCH"] and rust_available),
    }
