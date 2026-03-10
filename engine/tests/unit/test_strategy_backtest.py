"""Unit tests for src/tuning/strategy_backtest.py.

Covers:
- All 7 signal generator functions (_make_*_signals)
- StrategyBacktestEngine construction, validation, and run()
- _FlatRateCostCalculator.estimate_cost()
- _MockDEXAdapter set_price / get_pool_price / estimate_gas
- _build_strategy factory for all 7 types + unknown type error
"""
from __future__ import annotations

import random
from decimal import Decimal

import pytest

from src.tuning.strategy_backtest import (
    STRATEGY_TYPES,
    StrategyBacktestEngine,
    _FlatRateCostCalculator,
    _MockDEXAdapter,
    _build_strategy,
    _make_cex_dex_signals,
    _make_cross_exchange_signals,
    _make_funding_rate_signals,
    _make_futures_futures_signals,
    _make_spot_futures_signals,
    _make_statistical_arb_signals,
    _make_triangular_signals,
)
from src.tuning.backtest import StrategyParams
from src.tuning.data_loader import OHLCVWindow
from src.tuning.file_data_loader import generate_synthetic_ohlcv
from src.core.models import OrderSide


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

OHLCV_200 = generate_synthetic_ohlcv(200)
OHLCV_2 = generate_synthetic_ohlcv(2)

DEFAULT_PARAMS = StrategyParams()
FIXED_RNG = random.Random(0)

# A representative list of close prices for signal generator tests
CLOSES_10 = list(OHLCV_200.closes[:10])


# ---------------------------------------------------------------------------
# _FlatRateCostCalculator
# ---------------------------------------------------------------------------


class TestFlatRateCostCalculator:
    def test_estimate_cost_returns_fee_rate_times_price_times_size(self):
        calc = _FlatRateCostCalculator(fee_rate=0.001)
        price = Decimal("50000.0")
        size = Decimal("0.1")
        cost = calc.estimate_cost("binance", "BTC/USDT", OrderSide.BUY, size, price)
        assert cost == price * size * Decimal("0.001")

    def test_estimate_cost_custom_fee_rate(self):
        calc = _FlatRateCostCalculator(fee_rate=0.002)
        price = Decimal("1000.0")
        size = Decimal("1.0")
        cost = calc.estimate_cost("bybit", "ETH/USDT", OrderSide.SELL, size, price)
        assert cost == Decimal("2.0")

    def test_estimate_cost_zero_fee_rate_returns_zero(self):
        calc = _FlatRateCostCalculator(fee_rate=0.0)
        cost = calc.estimate_cost("binance", "BTC/USDT", OrderSide.BUY, Decimal("1.0"), Decimal("50000.0"))
        assert cost == Decimal("0")

    def test_estimate_cost_is_always_non_negative(self):
        calc = _FlatRateCostCalculator(fee_rate=0.001)
        cost = calc.estimate_cost("binance", "BTC/USDT", OrderSide.BUY, Decimal("0.5"), Decimal("30000.0"))
        assert cost >= Decimal("0")


# ---------------------------------------------------------------------------
# _MockDEXAdapter
# ---------------------------------------------------------------------------


class TestMockDEXAdapter:
    def test_default_price_is_set_on_construction(self):
        adapter = _MockDEXAdapter(price=55000.0)
        assert adapter._price == Decimal("55000.0")

    def test_set_price_updates_internal_price(self):
        adapter = _MockDEXAdapter()
        adapter.set_price(60000.0)
        assert adapter._price == Decimal("60000.0")

    def test_get_pool_price_returns_current_price(self):
        import asyncio
        adapter = _MockDEXAdapter(price=48000.0)
        result = asyncio.run(adapter.get_pool_price("WBTC", "USDC"))
        assert result == Decimal("48000.0")

    def test_get_pool_price_reflects_set_price(self):
        import asyncio
        adapter = _MockDEXAdapter()
        adapter.set_price(99999.0)
        result = asyncio.run(adapter.get_pool_price("WBTC", "USDC"))
        assert result == Decimal("99999.0")

    def test_estimate_gas_returns_decimal(self):
        import asyncio
        adapter = _MockDEXAdapter()
        gas = asyncio.run(adapter.estimate_gas(Decimal("0.1")))
        assert isinstance(gas, Decimal)

    def test_estimate_gas_is_positive(self):
        import asyncio
        adapter = _MockDEXAdapter()
        gas = asyncio.run(adapter.estimate_gas(Decimal("1.0")))
        assert gas > Decimal("0")

    def test_pool_address_is_string(self):
        adapter = _MockDEXAdapter()
        assert isinstance(adapter.pool_address, str)

    def test_dex_id_is_uniswap_v3(self):
        adapter = _MockDEXAdapter()
        assert adapter.dex_id == "uniswap_v3"


# ---------------------------------------------------------------------------
# Signal generators — structural invariants
# ---------------------------------------------------------------------------


class TestMakeCrossExchangeSignals:
    def test_returns_one_signal_per_close_price(self):
        signals = _make_cross_exchange_signals(CLOSES_10, DEFAULT_PARAMS, random.Random(1))
        assert len(signals) == len(CLOSES_10)

    def test_each_signal_has_correct_strategy_id(self):
        signals = _make_cross_exchange_signals(CLOSES_10, DEFAULT_PARAMS, random.Random(1))
        for sig in signals:
            assert sig.strategy_id == "backtest_cross_exchange"

    def test_each_signal_has_btcusdt_symbol(self):
        signals = _make_cross_exchange_signals(CLOSES_10, DEFAULT_PARAMS, random.Random(1))
        for sig in signals:
            assert sig.symbol == "BTC/USDT"

    def test_buy_and_sell_exchanges_set(self):
        signals = _make_cross_exchange_signals(CLOSES_10, DEFAULT_PARAMS, random.Random(1))
        for sig in signals:
            assert sig.buy_exchange in ("binance", "bybit")
            assert sig.sell_exchange in ("binance", "bybit")

    def test_prices_are_positive_decimals(self):
        signals = _make_cross_exchange_signals(CLOSES_10, DEFAULT_PARAMS, random.Random(1))
        for sig in signals:
            assert sig.buy_price > Decimal("0")
            assert sig.sell_price > Decimal("0")

    def test_spread_pct_is_non_negative(self):
        signals = _make_cross_exchange_signals(CLOSES_10, DEFAULT_PARAMS, random.Random(1))
        for sig in signals:
            assert sig.spread_pct >= Decimal("0")

    def test_volume_is_positive(self):
        signals = _make_cross_exchange_signals(CLOSES_10, DEFAULT_PARAMS, random.Random(1))
        for sig in signals:
            assert sig.volume > Decimal("0")

    def test_confidence_in_valid_range(self):
        signals = _make_cross_exchange_signals(CLOSES_10, DEFAULT_PARAMS, random.Random(1))
        for sig in signals:
            assert 0.0 <= sig.confidence <= 1.0


class TestMakeTriangularSignals:
    def test_returns_one_signal_per_close(self):
        signals = _make_triangular_signals(CLOSES_10, DEFAULT_PARAMS, random.Random(2))
        assert len(signals) == len(CLOSES_10)

    def test_strategy_id_is_backtest_triangular(self):
        signals = _make_triangular_signals(CLOSES_10, DEFAULT_PARAMS, random.Random(2))
        for sig in signals:
            assert sig.strategy_id == "backtest_triangular"

    def test_metadata_has_path_field(self):
        signals = _make_triangular_signals(CLOSES_10, DEFAULT_PARAMS, random.Random(2))
        for sig in signals:
            assert "path" in sig.metadata
            assert sig.metadata["path"] == ["USDT", "BTC", "ETH"]

    def test_metadata_has_pairs_field(self):
        signals = _make_triangular_signals(CLOSES_10, DEFAULT_PARAMS, random.Random(2))
        for sig in signals:
            assert "pairs" in sig.metadata

    def test_metadata_has_exchange_id(self):
        signals = _make_triangular_signals(CLOSES_10, DEFAULT_PARAMS, random.Random(2))
        for sig in signals:
            assert "exchange_id" in sig.metadata
            assert sig.metadata["exchange_id"] == "binance"

    def test_prices_are_positive(self):
        signals = _make_triangular_signals(CLOSES_10, DEFAULT_PARAMS, random.Random(2))
        for sig in signals:
            assert sig.buy_price > Decimal("0")
            assert sig.sell_price > Decimal("0")


class TestMakeSpotFuturesSignals:
    def test_returns_one_signal_per_close(self):
        signals = _make_spot_futures_signals(CLOSES_10, DEFAULT_PARAMS, random.Random(3))
        assert len(signals) == len(CLOSES_10)

    def test_strategy_id_is_backtest_spot_futures(self):
        signals = _make_spot_futures_signals(CLOSES_10, DEFAULT_PARAMS, random.Random(3))
        for sig in signals:
            assert sig.strategy_id == "backtest_spot_futures"

    def test_metadata_has_basis_bps(self):
        signals = _make_spot_futures_signals(CLOSES_10, DEFAULT_PARAMS, random.Random(3))
        for sig in signals:
            assert "basis_bps" in sig.metadata

    def test_metadata_has_spot_and_futures_symbol(self):
        signals = _make_spot_futures_signals(CLOSES_10, DEFAULT_PARAMS, random.Random(3))
        for sig in signals:
            assert "spot_symbol" in sig.metadata
            assert "futures_symbol" in sig.metadata

    def test_metadata_has_funding_rate(self):
        signals = _make_spot_futures_signals(CLOSES_10, DEFAULT_PARAMS, random.Random(3))
        for sig in signals:
            assert "funding_rate" in sig.metadata

    def test_spread_pct_is_non_negative(self):
        signals = _make_spot_futures_signals(CLOSES_10, DEFAULT_PARAMS, random.Random(3))
        for sig in signals:
            assert sig.spread_pct >= Decimal("0")


class TestMakeFundingRateSignals:
    def test_returns_one_signal_per_close(self):
        signals = _make_funding_rate_signals(CLOSES_10, DEFAULT_PARAMS, random.Random(4))
        assert len(signals) == len(CLOSES_10)

    def test_strategy_id_is_backtest_funding_rate(self):
        signals = _make_funding_rate_signals(CLOSES_10, DEFAULT_PARAMS, random.Random(4))
        for sig in signals:
            assert sig.strategy_id == "backtest_funding_rate"

    def test_metadata_has_funding_rate_fields(self):
        signals = _make_funding_rate_signals(CLOSES_10, DEFAULT_PARAMS, random.Random(4))
        for sig in signals:
            assert "funding_rate_sell" in sig.metadata
            assert "funding_rate_buy" in sig.metadata
            assert "funding_diff_bps" in sig.metadata

    def test_buy_exchange_is_binance_sell_is_bybit(self):
        signals = _make_funding_rate_signals(CLOSES_10, DEFAULT_PARAMS, random.Random(4))
        for sig in signals:
            assert sig.buy_exchange == "binance"
            assert sig.sell_exchange == "bybit"

    def test_prices_are_positive(self):
        signals = _make_funding_rate_signals(CLOSES_10, DEFAULT_PARAMS, random.Random(4))
        for sig in signals:
            assert sig.buy_price > Decimal("0")
            assert sig.sell_price > Decimal("0")


class TestMakeStatisticalArbSignals:
    def test_returns_one_signal_per_close(self):
        signals = _make_statistical_arb_signals(CLOSES_10, DEFAULT_PARAMS, random.Random(5))
        assert len(signals) == len(CLOSES_10)

    def test_strategy_id_is_backtest_statistical_arb(self):
        signals = _make_statistical_arb_signals(CLOSES_10, DEFAULT_PARAMS, random.Random(5))
        for sig in signals:
            assert sig.strategy_id == "backtest_statistical_arb"

    def test_buy_exchange_binance_sell_exchange_okx(self):
        signals = _make_statistical_arb_signals(CLOSES_10, DEFAULT_PARAMS, random.Random(5))
        for sig in signals:
            assert sig.buy_exchange == "binance"
            assert sig.sell_exchange == "okx"

    def test_prices_are_positive(self):
        signals = _make_statistical_arb_signals(CLOSES_10, DEFAULT_PARAMS, random.Random(5))
        for sig in signals:
            assert sig.buy_price > Decimal("0")
            assert sig.sell_price > Decimal("0")

    def test_spread_pct_is_non_negative(self):
        signals = _make_statistical_arb_signals(CLOSES_10, DEFAULT_PARAMS, random.Random(5))
        for sig in signals:
            assert sig.spread_pct >= Decimal("0")


class TestMakeCexDexSignals:
    def test_returns_one_signal_per_close(self):
        signals = _make_cex_dex_signals(CLOSES_10, DEFAULT_PARAMS, random.Random(6))
        assert len(signals) == len(CLOSES_10)

    def test_strategy_id_is_backtest_cex_dex(self):
        signals = _make_cex_dex_signals(CLOSES_10, DEFAULT_PARAMS, random.Random(6))
        for sig in signals:
            assert sig.strategy_id == "backtest_cex_dex"

    def test_sell_exchange_is_uniswap_v3(self):
        signals = _make_cex_dex_signals(CLOSES_10, DEFAULT_PARAMS, random.Random(6))
        for sig in signals:
            assert sig.sell_exchange == "uniswap_v3"

    def test_metadata_has_dex_price(self):
        signals = _make_cex_dex_signals(CLOSES_10, DEFAULT_PARAMS, random.Random(6))
        for sig in signals:
            assert "dex_price" in sig.metadata

    def test_metadata_has_gas_cost_usd(self):
        signals = _make_cex_dex_signals(CLOSES_10, DEFAULT_PARAMS, random.Random(6))
        for sig in signals:
            assert "gas_cost_usd" in sig.metadata

    def test_prices_are_positive(self):
        signals = _make_cex_dex_signals(CLOSES_10, DEFAULT_PARAMS, random.Random(6))
        for sig in signals:
            assert sig.buy_price > Decimal("0")
            assert sig.sell_price > Decimal("0")


class TestMakeFuturesFuturesSignals:
    def test_returns_one_signal_per_close(self):
        signals = _make_futures_futures_signals(CLOSES_10, DEFAULT_PARAMS, random.Random(7))
        assert len(signals) == len(CLOSES_10)

    def test_strategy_id_is_backtest_futures_futures(self):
        signals = _make_futures_futures_signals(CLOSES_10, DEFAULT_PARAMS, random.Random(7))
        for sig in signals:
            assert sig.strategy_id == "backtest_futures_futures"

    def test_symbol_is_perp(self):
        signals = _make_futures_futures_signals(CLOSES_10, DEFAULT_PARAMS, random.Random(7))
        for sig in signals:
            assert sig.symbol == "BTC/USDT:USDT"

    def test_buy_binance_sell_bybit(self):
        signals = _make_futures_futures_signals(CLOSES_10, DEFAULT_PARAMS, random.Random(7))
        for sig in signals:
            assert sig.buy_exchange == "binance"
            assert sig.sell_exchange == "bybit"

    def test_metadata_has_margin_available(self):
        signals = _make_futures_futures_signals(CLOSES_10, DEFAULT_PARAMS, random.Random(7))
        for sig in signals:
            assert "margin_available" in sig.metadata

    def test_sell_price_greater_than_buy_price(self):
        signals = _make_futures_futures_signals(CLOSES_10, DEFAULT_PARAMS, random.Random(7))
        for sig in signals:
            # buy_price is lowered, sell_price is raised from the mid
            assert sig.sell_price >= sig.buy_price


# ---------------------------------------------------------------------------
# _build_strategy factory
# ---------------------------------------------------------------------------


class TestBuildStrategy:
    def _cost_calc(self):
        return _FlatRateCostCalculator()

    def test_cross_exchange_returns_strategy_and_no_dex(self):
        strategy, dex = _build_strategy("cross_exchange", DEFAULT_PARAMS, self._cost_calc())
        assert strategy is not None
        assert dex is None

    def test_triangular_returns_strategy_and_no_dex(self):
        strategy, dex = _build_strategy("triangular", DEFAULT_PARAMS, self._cost_calc())
        assert strategy is not None
        assert dex is None

    def test_spot_futures_returns_strategy_and_no_dex(self):
        strategy, dex = _build_strategy("spot_futures", DEFAULT_PARAMS, self._cost_calc())
        assert strategy is not None
        assert dex is None

    def test_funding_rate_returns_strategy_and_no_dex(self):
        strategy, dex = _build_strategy("funding_rate", DEFAULT_PARAMS, self._cost_calc())
        assert strategy is not None
        assert dex is None

    def test_statistical_arb_returns_strategy_and_no_dex(self):
        strategy, dex = _build_strategy("statistical_arb", DEFAULT_PARAMS, self._cost_calc())
        assert strategy is not None
        assert dex is None

    def test_cex_dex_returns_strategy_and_dex_adapter(self):
        strategy, dex = _build_strategy("cex_dex", DEFAULT_PARAMS, self._cost_calc())
        assert strategy is not None
        assert isinstance(dex, _MockDEXAdapter)

    def test_futures_futures_returns_strategy_and_no_dex(self):
        strategy, dex = _build_strategy("futures_futures", DEFAULT_PARAMS, self._cost_calc())
        assert strategy is not None
        assert dex is None

    def test_unknown_type_raises_value_error(self):
        with pytest.raises(ValueError, match="Unknown strategy type"):
            _build_strategy("nonexistent_strategy", DEFAULT_PARAMS, self._cost_calc())


# ---------------------------------------------------------------------------
# StrategyBacktestEngine — construction
# ---------------------------------------------------------------------------


class TestStrategyBacktestEngineConstruction:
    def test_all_valid_strategy_types_construct_without_error(self):
        for stype in STRATEGY_TYPES:
            engine = StrategyBacktestEngine(strategy_type=stype)
            assert engine is not None

    def test_invalid_strategy_type_raises_value_error(self):
        with pytest.raises(ValueError, match="strategy_type must be one of"):
            StrategyBacktestEngine(strategy_type="invalid_type")

    def test_default_strategy_type_is_cross_exchange(self):
        engine = StrategyBacktestEngine()
        assert engine._strategy_type == "cross_exchange"

    def test_custom_initial_capital_stored(self):
        engine = StrategyBacktestEngine(initial_capital=50_000.0)
        assert engine._initial_capital == 50_000.0

    def test_custom_fee_rate_stored(self):
        engine = StrategyBacktestEngine(fee_rate=0.002)
        assert engine._fee_rate == 0.002

    def test_custom_seed_stored(self):
        engine = StrategyBacktestEngine(seed=99)
        assert engine._seed == 99


# ---------------------------------------------------------------------------
# StrategyBacktestEngine — run() with minimal data (2 candles)
# ---------------------------------------------------------------------------


class TestStrategyBacktestEngineMinimalData:
    def test_single_candle_returns_zero_trades(self):
        import numpy as np
        ohlcv = OHLCVWindow(
            times=np.array([0.0]),
            opens=np.array([50000.0]),
            highs=np.array([51000.0]),
            lows=np.array([49000.0]),
            closes=np.array([50000.0]),
            volumes=np.array([1.0]),
        )
        result = StrategyBacktestEngine("cross_exchange").run(DEFAULT_PARAMS, ohlcv)
        assert result.num_trades == 0
        assert result.total_pnl == 0.0

    def test_two_candles_returns_backtest_result(self):
        result = StrategyBacktestEngine("cross_exchange").run(DEFAULT_PARAMS, OHLCV_2)
        assert result is not None
        assert isinstance(result.total_pnl, float)
        assert isinstance(result.sharpe_ratio, float)
        assert isinstance(result.max_drawdown, float)
        assert isinstance(result.win_rate, float)
        assert isinstance(result.num_trades, int)


# ---------------------------------------------------------------------------
# StrategyBacktestEngine — run() with 200 candles (all strategy types)
# ---------------------------------------------------------------------------


class TestStrategyBacktestEngineRun:
    def test_run_returns_backtest_result_with_required_fields(self):
        engine = StrategyBacktestEngine("cross_exchange", seed=42)
        result = engine.run(DEFAULT_PARAMS, OHLCV_200)
        assert hasattr(result, "total_pnl")
        assert hasattr(result, "sharpe_ratio")
        assert hasattr(result, "max_drawdown")
        assert hasattr(result, "win_rate")
        assert hasattr(result, "num_trades")

    def test_win_rate_is_between_zero_and_one(self):
        engine = StrategyBacktestEngine("cross_exchange", seed=42)
        result = engine.run(DEFAULT_PARAMS, OHLCV_200)
        assert 0.0 <= result.win_rate <= 1.0

    def test_max_drawdown_is_non_positive(self):
        engine = StrategyBacktestEngine("cross_exchange", seed=42)
        result = engine.run(DEFAULT_PARAMS, OHLCV_200)
        assert result.max_drawdown <= 0.0

    def test_num_trades_is_non_negative_integer(self):
        engine = StrategyBacktestEngine("cross_exchange", seed=42)
        result = engine.run(DEFAULT_PARAMS, OHLCV_200)
        assert isinstance(result.num_trades, int)
        assert result.num_trades >= 0

    def test_returns_field_is_list(self):
        engine = StrategyBacktestEngine("cross_exchange", seed=42)
        result = engine.run(DEFAULT_PARAMS, OHLCV_200)
        assert isinstance(result.returns, list)

    @pytest.mark.parametrize("strategy_type", STRATEGY_TYPES)
    def test_each_strategy_type_produces_valid_result(self, strategy_type):
        engine = StrategyBacktestEngine(strategy_type, seed=42)
        result = engine.run(DEFAULT_PARAMS, OHLCV_200)
        assert isinstance(result.total_pnl, float)
        assert isinstance(result.num_trades, int)
        assert result.num_trades >= 0
        assert 0.0 <= result.win_rate <= 1.0
        assert result.max_drawdown <= 0.0

    def test_same_seed_produces_same_result(self):
        engine_a = StrategyBacktestEngine("cross_exchange", seed=7)
        engine_b = StrategyBacktestEngine("cross_exchange", seed=7)
        result_a = engine_a.run(DEFAULT_PARAMS, OHLCV_200)
        result_b = engine_b.run(DEFAULT_PARAMS, OHLCV_200)
        assert result_a.total_pnl == pytest.approx(result_b.total_pnl)
        assert result_a.num_trades == result_b.num_trades

    def test_different_seeds_may_produce_different_results(self):
        engine_a = StrategyBacktestEngine("cross_exchange", seed=1)
        engine_b = StrategyBacktestEngine("cross_exchange", seed=999)
        result_a = engine_a.run(DEFAULT_PARAMS, OHLCV_200)
        result_b = engine_b.run(DEFAULT_PARAMS, OHLCV_200)
        # At least one metric should differ with different seeds
        assert (result_a.total_pnl != result_b.total_pnl or
                result_a.num_trades != result_b.num_trades)

    def test_cross_exchange_produces_trades_with_200_candles(self):
        engine = StrategyBacktestEngine("cross_exchange", seed=42)
        result = engine.run(DEFAULT_PARAMS, OHLCV_200)
        assert result.num_trades > 0

    def test_strategy_types_constant_has_eight_entries(self):
        assert len(STRATEGY_TYPES) == 8

    def test_sharpe_ratio_is_float(self):
        engine = StrategyBacktestEngine("funding_rate", seed=42)
        result = engine.run(DEFAULT_PARAMS, OHLCV_200)
        assert isinstance(result.sharpe_ratio, float)
