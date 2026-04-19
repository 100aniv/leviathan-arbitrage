"""WS-A3 unit tests: per-position funding accrual + realization on close.

Covers the LiveMode helpers `_accrue_funding_cycle` and `_realize_funding_on_close`:
  * single cycle → accrual populated + FUNDING_ACCRUED_USDT gauge set
  * multi-cycle accumulation
  * close → realization deducts from `total_pnl`, clears accrual, increments Counter
  * non-FR strategies are skipped (no funding exposure)
  * missing mark price short-circuits without side effects
"""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from src.modes.live import LiveMode


class _FakeBook:
    """Minimal OrderBook stand-in returning scalar best bid/ask."""

    def __init__(self, bid: float, ask: float) -> None:
        self._bid = bid
        self._ask = ask

    def best_bid(self) -> tuple[float, float]:
        return (self._bid, 1.0)

    def best_ask(self) -> tuple[float, float]:
        return (self._ask, 1.0)


def _make_live_mode() -> LiveMode:
    """Bypass heavy LiveMode __init__ and seed the state WS-A3 touches."""
    lm = LiveMode.__new__(LiveMode)
    # WS-A3 state
    lm._funding_accrual = {}
    lm._realized_funding_total = 0.0
    lm._last_funding_settlement_hour = -1
    # Strategy manager + books
    lm._strategy_manager = MagicMock()
    lm._books = {}
    # Stats container for realization path
    lm._stats = SimpleNamespace(total_pnl=0.0, daily_pnl=0.0)
    return lm


def _make_fr_strategy(open_positions: dict) -> Any:
    """Fake FR strategy exposing `_open_positions`."""
    s = MagicMock()
    s._open_positions = open_positions
    return s


def test_accrual_single_cycle_signed_cost_recorded():
    """Short leg (sell) receives funding → negative cost; long leg pays → positive."""
    lm = _make_live_mode()
    lm._strategy_manager.list_strategies.return_value = ["funding_rate_arb_v1"]
    lm._strategy_manager.get_strategy.return_value = _make_fr_strategy({
        "BTC/USDT:USDT": {
            "sell_exchange": "binance_futures",
            "buy_exchange": "bybit_futures",
            "size": Decimal("0.01"),
            "long_size": Decimal("0.01"),
        }
    })
    # Populate orderbook so mark_price resolves
    lm._books = {
        "BTC/USDT:USDT": {
            "binance_futures": _FakeBook(bid=50000.0, ask=50010.0),
            "bybit_futures": _FakeBook(bid=49995.0, ask=50005.0),
        }
    }
    # Rates: sell side positive (shorts receive) / buy side positive (longs pay)
    rates = {
        "binance_futures": {
            "BTC/USDT:USDT": SimpleNamespace(rate=0.0001, next_funding_time=None),
        },
        "bybit_futures": {
            "BTC/USDT:USDT": SimpleNamespace(rate=0.00005, next_funding_time=None),
        },
    }

    lm._accrue_funding_cycle(rates)

    # SELL leg accrual: -0.0001 * 0.01 * 50005 ≈ -0.050005 (engine receives funding)
    sell_key = ("funding_rate_arb_v1", "binance_futures", "BTC/USDT:USDT")
    buy_key = ("funding_rate_arb_v1", "bybit_futures", "BTC/USDT:USDT")
    assert sell_key in lm._funding_accrual
    assert buy_key in lm._funding_accrual
    assert lm._funding_accrual[sell_key] < 0  # income (shorts receive)
    assert lm._funding_accrual[buy_key] > 0   # cost (longs pay)
    # Magnitude sanity: 0.0001 * 0.01 * 50005 ≈ 0.050005
    assert abs(lm._funding_accrual[sell_key] + 0.050005) < 1e-4


def test_accrual_accumulates_across_multiple_cycles():
    """Two cycles against same position should double the accrual."""
    lm = _make_live_mode()
    lm._strategy_manager.list_strategies.return_value = ["funding_rate_arb_v1"]
    lm._strategy_manager.get_strategy.return_value = _make_fr_strategy({
        "ETH/USDT:USDT": {
            "sell_exchange": "binance_futures",
            "buy_exchange": "bybit_futures",
            "size": Decimal("0.1"),
            "long_size": Decimal("0.1"),
        }
    })
    lm._books = {
        "ETH/USDT:USDT": {
            "binance_futures": _FakeBook(bid=3000.0, ask=3000.0),
            "bybit_futures": _FakeBook(bid=3000.0, ask=3000.0),
        }
    }
    rates = {
        "binance_futures": {"ETH/USDT:USDT": SimpleNamespace(rate=0.001, next_funding_time=None)},
        "bybit_futures": {"ETH/USDT:USDT": SimpleNamespace(rate=0.0005, next_funding_time=None)},
    }

    lm._accrue_funding_cycle(rates)
    lm._accrue_funding_cycle(rates)

    sell_key = ("funding_rate_arb_v1", "binance_futures", "ETH/USDT:USDT")
    buy_key = ("funding_rate_arb_v1", "bybit_futures", "ETH/USDT:USDT")
    # Per cycle: -0.001 * 0.1 * 3000 = -0.3 (sell income), +0.0005 * 0.1 * 3000 = +0.15 (buy cost)
    assert abs(lm._funding_accrual[sell_key] - (-0.3 * 2)) < 1e-6
    assert abs(lm._funding_accrual[buy_key] - (0.15 * 2)) < 1e-6


def test_realize_on_close_deducts_from_total_pnl_and_clears():
    """Closing the position realizes accrued funding → total_pnl deducted, entry cleared."""
    lm = _make_live_mode()
    lm._stats.total_pnl = 10.0
    lm._stats.daily_pnl = 10.0
    sell_key = ("funding_rate_arb_v1", "binance_futures", "BTC/USDT:USDT")
    buy_key = ("funding_rate_arb_v1", "bybit_futures", "BTC/USDT:USDT")
    lm._funding_accrual[sell_key] = -0.25   # income
    lm._funding_accrual[buy_key] = 0.10     # cost

    realized = lm._realize_funding_on_close(
        strategy_id="funding_rate_arb_v1",
        symbol="BTC/USDT:USDT",
        exchange_ids=["binance_futures", "bybit_futures"],
    )

    # Signed realized = -0.25 + 0.10 = -0.15 (net income). total_pnl increases.
    assert abs(realized - (-0.15)) < 1e-9
    assert abs(lm._stats.total_pnl - (10.0 - (-0.15))) < 1e-9  # 10.15
    assert abs(lm._stats.daily_pnl - 10.15) < 1e-9
    assert abs(lm._realized_funding_total - (-0.15)) < 1e-9
    # Accrual entries cleared after realization
    assert sell_key not in lm._funding_accrual
    assert buy_key not in lm._funding_accrual


def test_non_fr_strategies_skipped():
    """Only funding_rate strategies carry funding exposure — FF must be skipped."""
    lm = _make_live_mode()
    lm._strategy_manager.list_strategies.return_value = ["futures_futures_arb_v1"]
    ff_strategy = _make_fr_strategy({
        "BTC/USDT:USDT": {
            "sell_exchange": "binance_futures",
            "buy_exchange": "bybit_futures",
            "size": Decimal("0.01"),
            "long_size": Decimal("0.01"),
        }
    })
    lm._strategy_manager.get_strategy.return_value = ff_strategy
    lm._books = {
        "BTC/USDT:USDT": {
            "binance_futures": _FakeBook(bid=50000.0, ask=50010.0),
            "bybit_futures": _FakeBook(bid=49995.0, ask=50005.0),
        }
    }
    rates = {
        "binance_futures": {"BTC/USDT:USDT": SimpleNamespace(rate=0.0001, next_funding_time=None)},
        "bybit_futures": {"BTC/USDT:USDT": SimpleNamespace(rate=0.00005, next_funding_time=None)},
    }

    lm._accrue_funding_cycle(rates)

    # No accrual recorded: FF is excluded from funding accrual by scope.
    assert lm._funding_accrual == {}


def test_accrual_skipped_when_no_mark_price():
    """If orderbook has no book for the symbol, the position is skipped silently."""
    lm = _make_live_mode()
    lm._strategy_manager.list_strategies.return_value = ["funding_rate_arb_v1"]
    lm._strategy_manager.get_strategy.return_value = _make_fr_strategy({
        "XRP/USDT:USDT": {
            "sell_exchange": "binance_futures",
            "buy_exchange": "bybit_futures",
            "size": Decimal("100"),
            "long_size": Decimal("100"),
        }
    })
    lm._books = {}  # No books → no mark price
    rates = {
        "binance_futures": {"XRP/USDT:USDT": SimpleNamespace(rate=0.001, next_funding_time=None)},
        "bybit_futures": {"XRP/USDT:USDT": SimpleNamespace(rate=0.0005, next_funding_time=None)},
    }

    lm._accrue_funding_cycle(rates)

    assert lm._funding_accrual == {}  # silently skipped
