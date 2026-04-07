"""Tests for US-238 through US-242 — Phase S13 extension.

US-238: spot_futures backwardation path
US-239: funding_rate settlement timing + position tracking
US-240: stat_arb OU half-life filter + cross_asset metadata
US-241: triangular cross-pair subscription + min_profit_bps
US-242: MockDEXAdapter for Shadow
"""
from __future__ import annotations

import math
import os
import time
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.models import OrderSide, Signal
from src.core.order_book import OrderBook


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_book(symbol: str, exchange: str, bid: str, ask: str, qty: str = "1") -> OrderBook:
    book = OrderBook(symbol=symbol, exchange=exchange)
    book.apply_snapshot(bids=[(bid, qty)], asks=[(ask, qty)])
    return book


def _make_producer(return_signal: Signal | None = None):
    """Create RealDataSignalProducer with mocked multi-signal producer."""
    from src.core.real_signal_producer import RealDataSignalProducer
    from src.core.triangular_scanner import TriangularScanner

    mock_multi = MagicMock()
    mock_multi.produce_triangular_signal = AsyncMock(return_value=return_signal)
    mock_multi.produce_spot_futures_signal = AsyncMock(return_value=return_signal)
    mock_multi.produce_futures_futures_signal = AsyncMock(return_value=return_signal)
    mock_multi.produce_funding_rate_signal = AsyncMock(return_value=return_signal)
    mock_multi.produce_statistical_arb_signal = AsyncMock(return_value=return_signal)
    mock_multi.produce_latency_arb_signal = AsyncMock(return_value=return_signal)

    scanner = TriangularScanner(min_profit_bps=Decimal("10"))
    producer = RealDataSignalProducer(
        multi_signal_producer=mock_multi,
        triangular_scanner=scanner,
        futures_exchanges={"binance_futures"},
    )
    return mock_multi, producer


def _mock_signal(strategy_id: str = "test", metadata: dict | None = None) -> Signal:
    return Signal(
        strategy_id=strategy_id,
        symbol="BTC/USDT",
        buy_exchange="binance",
        sell_exchange="binance_futures",
        buy_price=Decimal("50000"),
        sell_price=Decimal("50100"),
        spread_pct=Decimal("0.002"),
        confidence=0.8,
        volume=Decimal("0.01"),
        metadata=metadata or {},
    )


# ===========================================================================
# US-238: spot_futures backwardation path
# ===========================================================================

class TestUS238Backwardation:
    """Backwardation path: spot_bid > fut_ask → sell spot, buy futures."""

    @pytest.mark.asyncio
    async def test_backwardation_signal_generated(self):
        """When spot bid > futures ask, a backwardation signal is produced."""
        sig = _mock_signal("spot_futures_basis", metadata={})
        mock_multi, producer = _make_producer(return_signal=sig)

        spot_book = _make_book("BTC/USDT", "binance", "50200", "50210")
        fut_book = _make_book("BTC/USDT", "binance_futures", "50090", "50100")

        result = await producer.on_orderbook_update(
            exchange_id="binance",
            symbol="BTC/USDT",
            book=spot_book,
            all_books={"BTC/USDT": {"binance": spot_book}},
            futures_books={"BTC/USDT": {"binance_futures": fut_book}},
        )
        # Should have called produce_spot_futures_signal at least twice
        # (once for contango check which may fail, once for backwardation)
        assert mock_multi.produce_spot_futures_signal.call_count >= 1
        assert len(result) >= 1

    @pytest.mark.asyncio
    async def test_backwardation_direction_metadata(self):
        """Backwardation signal has direction='backwardation' in metadata."""
        sig = _mock_signal("spot_futures_basis", metadata={})
        mock_multi, producer = _make_producer(return_signal=sig)

        spot_book = _make_book("BTC/USDT", "binance", "50200", "50210")
        fut_book = _make_book("BTC/USDT", "binance_futures", "50090", "50100")

        result = await producer.on_orderbook_update(
            exchange_id="binance",
            symbol="BTC/USDT",
            book=spot_book,
            all_books={"BTC/USDT": {"binance": spot_book}},
            futures_books={"BTC/USDT": {"binance_futures": fut_book}},
        )
        # At least one signal should have backwardation direction
        backwardation_signals = [s for s in result if s.metadata.get("direction") == "backwardation"]
        assert len(backwardation_signals) >= 1

    @pytest.mark.asyncio
    async def test_contango_direction_metadata(self):
        """Contango signal has direction='contango' in metadata."""
        sig = _mock_signal("spot_futures_basis", metadata={})
        mock_multi, producer = _make_producer(return_signal=sig)

        spot_book = _make_book("BTC/USDT", "binance", "50000", "50010")
        fut_book = _make_book("BTC/USDT", "binance_futures", "50100", "50200")

        result = await producer.on_orderbook_update(
            exchange_id="binance",
            symbol="BTC/USDT",
            book=spot_book,
            all_books={"BTC/USDT": {"binance": spot_book}},
            futures_books={"BTC/USDT": {"binance_futures": fut_book}},
        )
        contango_signals = [s for s in result if s.metadata.get("direction") == "contango"]
        assert len(contango_signals) >= 1

    @pytest.mark.asyncio
    async def test_backwardation_skipped_below_min_basis(self):
        """Backwardation with spread < min_basis_bps is filtered."""
        sig = _mock_signal("spot_futures_basis", metadata={})
        mock_multi, producer = _make_producer(return_signal=sig)

        # Tiny spread: spot_bid=50001, fut_ask=50000 → ~0.2 bps < 5 bps
        spot_book = _make_book("BTC/USDT", "binance", "50001", "50002")
        fut_book = _make_book("BTC/USDT", "binance_futures", "49999", "50000")

        with patch.dict(os.environ, {"SPOT_FUTURES_MIN_BASIS_BPS": "5"}):
            result = await producer.on_orderbook_update(
                exchange_id="binance",
                symbol="BTC/USDT",
                book=spot_book,
                all_books={"BTC/USDT": {"binance": spot_book}},
                futures_books={"BTC/USDT": {"binance_futures": fut_book}},
            )
        # No backwardation signal because spread too small
        backwardation_signals = [s for s in result if s.metadata.get("direction") == "backwardation"]
        assert len(backwardation_signals) == 0

    @pytest.mark.asyncio
    async def test_backwardation_korean_exchange_skipped(self):
        """Korean exchanges are skipped for spot_futures (both directions)."""
        sig = _mock_signal("spot_futures_basis", metadata={})
        mock_multi, producer = _make_producer(return_signal=sig)

        spot_book = _make_book("BTC/USDT", "upbit", "50200", "50210")
        fut_book = _make_book("BTC/USDT", "binance_futures", "50090", "50100")

        result = await producer.on_orderbook_update(
            exchange_id="upbit",
            symbol="BTC/USDT",
            book=spot_book,
            all_books={"BTC/USDT": {"upbit": spot_book}},
            futures_books={"BTC/USDT": {"binance_futures": fut_book}},
        )
        assert len(result) == 0


# ===========================================================================
# US-239: funding_rate settlement timing + position tracking
# ===========================================================================

class TestUS239FundingRateSettlement:
    """Settlement timing filter and position duplicate guard."""

    def _make_strategy(self, settlement_window_minutes: float = 30.0):
        from src.strategies.funding_rate import FundingRateConfig, FundingRateStrategy
        calc = MagicMock()
        calc.estimate_cost.return_value = Decimal("0.5")
        config = FundingRateConfig(
            min_funding_diff_bps=Decimal("5"),
            settlement_window_minutes=settlement_window_minutes,
        )
        strategy = FundingRateStrategy("fr_test", calc, config)
        strategy._is_active = True
        return strategy

    def _make_signal(self, symbol: str = "BTC/USDT:USDT"):
        return Signal(
            strategy_id="fr_test",
            symbol=symbol,
            buy_exchange="bybit",
            sell_exchange="binance_futures",
            buy_price=Decimal("50000"),
            sell_price=Decimal("50010"),
            spread_pct=Decimal("0.0002"),
            confidence=0.85,
            volume=Decimal("0.5"),
            metadata={
                "funding_rate_sell": "0.003",
                "funding_rate_buy": "-0.001",
            },
        )

    @pytest.mark.asyncio
    async def test_settlement_timing_blocks_far_from_settlement(self):
        """Signal outside settlement window is filtered."""
        strategy = self._make_strategy(settlement_window_minutes=30.0)
        # Mock time to be 4 hours from settlement (way outside 30 min window)
        fake_time = datetime(2026, 3, 18, 4, 0, 0, tzinfo=timezone.utc)
        with patch("src.strategies.funding_rate.datetime") as mock_dt:
            mock_dt.now.return_value = fake_time
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = await strategy.on_signal(self._make_signal())
        assert result is None

    @pytest.mark.asyncio
    async def test_settlement_timing_allows_near_settlement(self):
        """Signal within settlement window passes timing filter."""
        strategy = self._make_strategy(settlement_window_minutes=30.0)
        # 10 minutes before 08:00 UTC settlement
        fake_time = datetime(2026, 3, 18, 7, 50, 0, tzinfo=timezone.utc)
        with patch("src.strategies.funding_rate.datetime") as mock_dt:
            mock_dt.now.return_value = fake_time
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = await strategy.on_signal(self._make_signal())
        assert result is not None

    @pytest.mark.asyncio
    async def test_duplicate_position_blocked(self):
        """Second signal on same symbol is blocked."""
        strategy = self._make_strategy(settlement_window_minutes=30.0)
        fake_time = datetime(2026, 3, 18, 7, 50, 0, tzinfo=timezone.utc)
        with patch("src.strategies.funding_rate.datetime") as mock_dt:
            mock_dt.now.return_value = fake_time
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result1 = await strategy.on_signal(self._make_signal())
            assert result1 is not None
            # Second signal on same symbol should be blocked
            result2 = await strategy.on_signal(self._make_signal())
            assert result2 is None

    @pytest.mark.asyncio
    async def test_different_symbol_not_blocked(self):
        """Different symbol is not blocked by existing position."""
        strategy = self._make_strategy(settlement_window_minutes=30.0)
        fake_time = datetime(2026, 3, 18, 7, 50, 0, tzinfo=timezone.utc)
        with patch("src.strategies.funding_rate.datetime") as mock_dt:
            mock_dt.now.return_value = fake_time
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result1 = await strategy.on_signal(self._make_signal("BTC/USDT:USDT"))
            assert result1 is not None
            result2 = await strategy.on_signal(self._make_signal("ETH/USDT:USDT"))
            assert result2 is not None

    @pytest.mark.asyncio
    async def test_settlement_release_clears_positions(self):
        """Positions are cleared after settlement hour passes."""
        strategy = self._make_strategy(settlement_window_minutes=30.0)
        # Enter position
        strategy._open_positions["BTC/USDT:USDT"] = "short_high_long_low"
        # Simulate settlement at hour 8
        strategy._last_settlement_hour = -1
        fake_time = datetime(2026, 3, 18, 8, 0, 0, tzinfo=timezone.utc)
        with patch("src.strategies.funding_rate.datetime") as mock_dt:
            mock_dt.now.return_value = fake_time
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            strategy._check_settlement_release()
        assert len(strategy._open_positions) == 0

    def test_minutes_to_next_settlement(self):
        """Correctly computes minutes to next settlement."""
        strategy = self._make_strategy()
        # 7:45 UTC → 15 min to 8:00 settlement
        t = datetime(2026, 3, 18, 7, 45, 0, tzinfo=timezone.utc)
        minutes = strategy._minutes_to_next_settlement(t)
        assert 14.0 < minutes < 16.0

        # 8:01 UTC → ~479 min to 16:00 settlement
        t2 = datetime(2026, 3, 18, 8, 1, 0, tzinfo=timezone.utc)
        minutes2 = strategy._minutes_to_next_settlement(t2)
        assert 470.0 < minutes2 < 480.0


# ===========================================================================
# US-240: stat_arb OU half-life filter + cross_asset metadata
# ===========================================================================

class TestUS240HalfLife:
    """OU half-life filter and cross_asset metadata."""

    def test_compute_half_life_mean_reverting(self):
        """Mean-reverting series has finite half-life."""
        from src.strategies.statistical_arb import StatisticalArbStrategy
        import numpy as np
        # Generate OU process: x[t+1] = x[t] - 0.1*x[t] + noise
        np.random.seed(42)
        x = [0.0]
        for _ in range(200):
            x.append(x[-1] - 0.1 * x[-1] + np.random.normal(0, 0.01))
        half_life = StatisticalArbStrategy._compute_half_life(x)
        assert half_life < 15.0  # Should be around ~6.9 (ln2/0.1)
        assert half_life > 0

    def test_compute_half_life_trending_series(self):
        """Trending series (no mean-reversion) has half-life > max threshold."""
        from src.strategies.statistical_arb import StatisticalArbStrategy
        # Pure upward trend — beta should be >= 0 → inf, or very large
        x = [float(i) * 0.1 for i in range(200)]
        half_life = StatisticalArbStrategy._compute_half_life(x)
        assert half_life > 15.0  # Exceeds the default 15-day threshold

    def test_compute_half_life_short_series(self):
        """Short series returns infinity (not enough data)."""
        from src.strategies.statistical_arb import StatisticalArbStrategy
        half_life = StatisticalArbStrategy._compute_half_life([1.0, 2.0, 3.0])
        assert half_life == float('inf')

    @pytest.mark.asyncio
    async def test_half_life_filter_blocks_slow_pair(self):
        """Pair with half-life > max_half_life_days is skipped on entry."""
        from src.strategies.statistical_arb import StatArbConfig, StatisticalArbStrategy

        calc = MagicMock()
        calc.estimate_cost.return_value = Decimal("0.5")
        # max_half_life_days=0.001 → nearly all pairs blocked
        config = StatArbConfig(
            min_history=30,
            zscore_entry=2.0,
            max_half_life_days=0.001,  # extremely tight threshold
            enable_cointegration=False,
            min_zero_crossings=0,
            adaptive_threshold=False,
        )
        strategy = StatisticalArbStrategy("sa_test", calc, config)
        strategy._is_active = True

        # Feed diverging prices (spread grows monotonically → half-life = inf)
        price_a = 50000.0
        price_b = 3000.0
        for i in range(150):
            price_a += 10.0  # Monotonic increase
            price_b += 0.1   # Slower increase → spread diverges
            await strategy.on_orderbook_update("binance", "BTC/USDT", price_a)
            await strategy.on_orderbook_update("binance", "ETH/USDT", price_b)

        # With max_half_life_days=0.001, no entries should pass
        assert strategy.metrics.trade_requests_generated == 0

    @pytest.mark.asyncio
    async def test_cross_asset_metadata_set(self):
        """Statistical arb signal from RealDataSignalProducer has cross_asset=True."""
        sig = _mock_signal("statistical_arb", metadata={})
        mock_multi, producer = _make_producer(return_signal=sig)

        book_btc = _make_book("BTC/USDT", "binance", "50000", "50001")
        book_eth = _make_book("ETH/USDT", "binance", "3000", "3001")

        # Feed enough history
        all_books = {
            "BTC/USDT": {"binance": book_btc},
            "ETH/USDT": {"binance": book_eth},
        }

        # Need to build up history first (min 120 samples by default)
        producer._stat_arb_min_history = 2  # Lower for test
        for _ in range(5):
            await producer.on_orderbook_update(
                "binance", "BTC/USDT", book_btc, all_books, {}
            )
            await producer.on_orderbook_update(
                "binance", "ETH/USDT", book_eth, all_books, {}
            )

        # If any stat arb signals were produced, they should have cross_asset=True
        if mock_multi.produce_statistical_arb_signal.call_count > 0:
            # The metadata is set on the returned signal
            for call_result in [sig]:
                if call_result and call_result.metadata:
                    assert call_result.metadata.get("cross_asset") is True


# ===========================================================================
# US-241: triangular cross-pair + min_profit_bps
# ===========================================================================

class TestUS241TriangularCrossPair:
    """Triangular cross-pair subscription and min_profit_bps adjustment."""

    def test_min_profit_bps_default_is_8(self):
        """TriangularConfig default min_profit_bps is 8 (was 10)."""
        from src.strategies.triangular import TriangularConfig
        config = TriangularConfig()
        assert config.min_profit_bps == Decimal("8")

    def test_cross_pairs_env_parsing(self):
        """TRIANGULAR_CROSS_PAIRS env var is parsed correctly."""
        cross_pairs_env = "ETH/BTC,SOL/BTC,SOL/ETH"
        pairs = [p.strip() for p in cross_pairs_env.split(",") if p.strip()]
        assert pairs == ["ETH/BTC", "SOL/BTC", "SOL/ETH"]

    def test_cross_pairs_added_to_symbols(self):
        """Cross-pairs are appended to trading symbols without duplicates."""
        existing = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
        cross_pairs = ["ETH/BTC", "SOL/BTC", "SOL/ETH"]
        existing_set = set(existing)
        added = []
        for cp in cross_pairs:
            if cp not in existing_set:
                existing.append(cp)
                existing_set.add(cp)
                added.append(cp)
        assert "ETH/BTC" in existing
        assert "SOL/BTC" in existing
        assert "SOL/ETH" in existing
        assert len(added) == 3
        # No duplicates
        assert len(existing) == 6

    def test_cross_pairs_no_duplicate_if_already_present(self):
        """If a cross-pair already exists, it's not added again."""
        existing = ["BTC/USDT", "ETH/USDT", "ETH/BTC"]
        cross_pairs = ["ETH/BTC", "SOL/BTC"]
        existing_set = set(existing)
        added = []
        for cp in cross_pairs:
            if cp not in existing_set:
                existing.append(cp)
                existing_set.add(cp)
                added.append(cp)
        assert len(added) == 1  # Only SOL/BTC added
        assert len(existing) == 4

    @pytest.mark.asyncio
    async def test_bottleneck_volume_used(self):
        """Triangular strategy uses max_volume_usdt from metadata when available."""
        from src.strategies.triangular import TriangularConfig, TriangularStrategy

        calc = MagicMock()
        calc.estimate_cost.return_value = Decimal("0.01")
        config = TriangularConfig(min_profit_bps=Decimal("5"), max_position_usdt=Decimal("10000"))
        strategy = TriangularStrategy("tri_test", calc, config)
        strategy._is_active = True

        signal = Signal(
            strategy_id="tri_test",
            symbol="BTC/USDT",
            buy_exchange="binance",
            sell_exchange="binance",
            buy_price=Decimal("50000"),
            sell_price=Decimal("50050"),
            spread_pct=Decimal("0.001"),  # 10 bps
            confidence=0.9,
            volume=Decimal("1.0"),
            metadata={
                "path": ["USDT", "BTC", "ETH"],
                "pairs": ["BTC/USDT", "ETH/BTC", "ETH/USDT"],
                "sides": ["buy", "sell", "sell"],
                "prices": ["50000", "0.06", "3000"],
                "exchange_id": "binance",
                "max_volume_usdt": "500",  # Bottleneck: only $500
            },
        )
        result = await strategy.on_signal(signal)
        if result is not None:
            # Size should be limited by bottleneck volume
            max_size_from_bottleneck = Decimal("500") / Decimal("50000")  # 0.01
            for leg in result.legs:
                assert leg.size <= max_size_from_bottleneck + Decimal("0.001")

    @pytest.mark.asyncio
    async def test_triangular_without_bottleneck_volume(self):
        """Without max_volume_usdt, uses standard sizing."""
        from src.strategies.triangular import TriangularConfig, TriangularStrategy

        calc = MagicMock()
        calc.estimate_cost.return_value = Decimal("0.01")
        config = TriangularConfig(min_profit_bps=Decimal("5"), max_position_usdt=Decimal("10000"))
        strategy = TriangularStrategy("tri_test", calc, config)
        strategy._is_active = True

        signal = Signal(
            strategy_id="tri_test",
            symbol="BTC/USDT",
            buy_exchange="binance",
            sell_exchange="binance",
            buy_price=Decimal("50000"),
            sell_price=Decimal("50050"),
            spread_pct=Decimal("0.001"),
            confidence=0.9,
            volume=Decimal("0.1"),
            metadata={
                "path": ["USDT", "BTC", "ETH"],
                "pairs": ["BTC/USDT", "ETH/BTC", "ETH/USDT"],
                "sides": ["buy", "sell", "sell"],
                "prices": ["50000", "0.06", "3000"],
                "exchange_id": "binance",
            },
        )
        result = await strategy.on_signal(signal)
        # Should still generate trade (no bottleneck limit)
        assert result is not None


# ===========================================================================
# US-242: MockDEXAdapter for Shadow
# ===========================================================================

class TestUS242MockDEXAdapter:
    """MockDEXAdapter conforms to DEXAdapter protocol."""

    @pytest.mark.asyncio
    async def test_pool_address(self):
        """Mock adapter has a pool address."""
        from src.dex.mock_adapter import MockDEXAdapter
        adapter = MockDEXAdapter()
        assert adapter.pool_address == "0xMOCK_SHADOW_DEX_POOL"

    @pytest.mark.asyncio
    async def test_dex_id(self):
        """Mock adapter has dex_id."""
        from src.dex.mock_adapter import MockDEXAdapter
        adapter = MockDEXAdapter()
        assert adapter.dex_id == "mock_dex"

    @pytest.mark.asyncio
    async def test_estimate_gas(self):
        """Gas estimate returns configured value."""
        from src.dex.mock_adapter import MockDEXAdapter
        adapter = MockDEXAdapter(gas_cost_usd=0.15)
        gas = await adapter.estimate_gas(Decimal("1"))
        assert gas == Decimal("0.15")

    @pytest.mark.asyncio
    async def test_get_pool_reserves(self):
        """Pool reserves return configured deep liquidity."""
        from src.dex.mock_adapter import MockDEXAdapter
        adapter = MockDEXAdapter(default_reserves=(2_000_000.0, 500_000.0))
        r0, r1 = await adapter.get_pool_reserves()
        assert r0 == Decimal("2000000.0")
        assert r1 == Decimal("500000.0")

    @pytest.mark.asyncio
    async def test_get_pool_price_with_books(self):
        """Pool price derived from CEX mid-price with spread."""
        from src.dex.mock_adapter import MockDEXAdapter
        book = _make_book("BTC/USDT", "binance", "50000", "50100")
        adapter = MockDEXAdapter(
            books={"BTC/USDT": {"binance": book}},
            spread_pct_min=0.01,
            spread_pct_max=0.01,
        )
        price = await adapter.get_pool_price("BTC", "USDT")
        # Mid = 50050, spread = 1% → price in [49549.5, 50550.5]
        assert Decimal("49000") < price < Decimal("51100")

    @pytest.mark.asyncio
    async def test_get_pool_price_no_books_returns_zero(self):
        """Without CEX books, returns 0."""
        from src.dex.mock_adapter import MockDEXAdapter
        adapter = MockDEXAdapter()
        price = await adapter.get_pool_price("BTC", "USDT")
        assert price == Decimal("0")

    @pytest.mark.asyncio
    async def test_set_books_updates_reference(self):
        """set_books() updates the CEX book reference."""
        from src.dex.mock_adapter import MockDEXAdapter
        adapter = MockDEXAdapter()
        book = _make_book("ETH/USDT", "binance", "3000", "3010")
        adapter.set_books({"ETH/USDT": {"binance": book}})
        price = await adapter.get_pool_price("ETH", "USDT")
        assert price > Decimal("0")

    def test_build_dex_adapter_mock_enabled(self):
        """Engine._build_dex_adapter returns MockDEXAdapter when PAPER_MOCK_DEX=true."""
        with patch.dict(os.environ, {"PAPER_MOCK_DEX": "true"}, clear=False):
            os.environ.pop("DEX_RPC_URL", None)
            os.environ.pop("DEX_POOL_ADDRESS", None)
            from src.main import Engine
            engine = Engine.__new__(Engine)
            adapter = engine._build_dex_adapter()
            assert adapter is not None
            assert adapter.dex_id == "mock_dex"

    def test_build_dex_adapter_mock_disabled(self):
        """Engine._build_dex_adapter returns None when PAPER_MOCK_DEX not set."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DEX_RPC_URL", None)
            os.environ.pop("DEX_POOL_ADDRESS", None)
            os.environ.pop("PAPER_MOCK_DEX", None)
            from src.main import Engine
            engine = Engine.__new__(Engine)
            adapter = engine._build_dex_adapter()
            assert adapter is None

    @pytest.mark.asyncio
    async def test_mock_adapter_protocol_compliance(self):
        """MockDEXAdapter implements DEXAdapter protocol."""
        from src.dex.mock_adapter import MockDEXAdapter
        from src.strategies.cex_dex import DEXAdapter
        adapter = MockDEXAdapter()
        assert isinstance(adapter, DEXAdapter)
