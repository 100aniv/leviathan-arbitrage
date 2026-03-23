"""Tests for SignalGenerator — friction-aware signal pipeline (Amendment 3A)."""
import asyncio
import pytest
from decimal import Decimal
from unittest.mock import AsyncMock

from src.core.models import Signal
from src.core.order_book import OrderBook
from src.core.price_hub import PriceHub
from src.core.signal import SignalConfig, SignalGenerator
from src.friction.cost_calculator import CostCalculator
from src.friction.fee_model import FeeModel
from src.friction.slippage_model import CEXOrderbookSlippage


@pytest.fixture
def buy_book():
    book = OrderBook(symbol="BTC/USDT", exchange="binance")
    book.apply_snapshot(
        bids=[("50000.00", "10.0")],
        asks=[("50001.00", "10.0")],
    )
    return book


@pytest.fixture
def sell_book():
    book = OrderBook(symbol="BTC/USDT", exchange="okx")
    book.apply_snapshot(
        bids=[("50500.00", "10.0")],  # High bid → profitable arb opportunity
        asks=[("50501.00", "10.0")],
    )
    return book


@pytest.fixture
def hub():
    return PriceHub()


@pytest.fixture
def calculator():
    return CostCalculator(
        fee_model=FeeModel(),
        slippage_model=CEXOrderbookSlippage(k=Decimal("1.0"), cold_start=False),
    )


@pytest.fixture
def config():
    return SignalConfig(
        min_edge=Decimal("0.0001"),  # 1 bps
        cooldown_seconds=0.0,        # no cooldown in tests
        max_rollback_cost_usd=Decimal("1000"),
    )


@pytest.fixture
def generator(hub, calculator, config):
    return SignalGenerator(hub, calculator, config)


class TestSignalGeneration:
    @pytest.mark.asyncio
    async def test_emits_signal_on_arb_opportunity(self, generator, buy_book, sell_book):
        books = {"binance": buy_book, "okx": sell_book}
        generator._hub.update(buy_book)
        signal = await generator.on_orderbook_update(sell_book, books, Decimal("1.0"))
        assert signal is not None
        assert isinstance(signal, Signal)
        assert signal.buy_exchange == "binance"
        assert signal.sell_exchange == "okx"

    @pytest.mark.asyncio
    async def test_signal_has_correct_symbol(self, generator, buy_book, sell_book):
        books = {"binance": buy_book, "okx": sell_book}
        generator._hub.update(buy_book)
        signal = await generator.on_orderbook_update(sell_book, books, Decimal("1.0"))
        assert signal is not None
        assert signal.symbol == "BTC/USDT"

    @pytest.mark.asyncio
    async def test_no_signal_when_no_spread(self, hub, calculator, config):
        gen = SignalGenerator(hub, calculator, config)
        book_b = OrderBook(symbol="BTC/USDT", exchange="binance")
        book_b.apply_snapshot([("50000.00", "1.0")], [("50001.00", "1.0")])
        book_o = OrderBook(symbol="BTC/USDT", exchange="okx")
        book_o.apply_snapshot([("50000.00", "1.0")], [("50001.00", "1.0")])
        hub.update(book_b)
        books = {"binance": book_b, "okx": book_o}
        signal = await gen.on_orderbook_update(book_o, books, Decimal("1.0"))
        assert signal is None

    @pytest.mark.asyncio
    async def test_no_signal_single_exchange(self, generator):
        book = OrderBook(symbol="BTC/USDT", exchange="binance")
        book.apply_snapshot([("50000.00", "1.0")], [("50001.00", "1.0")])
        books = {"binance": book}
        signal = await generator.on_orderbook_update(book, books, Decimal("1.0"))
        assert signal is None

    @pytest.mark.asyncio
    async def test_no_signal_below_min_edge(self, hub, calculator):
        config = SignalConfig(min_edge=Decimal("1.0"), cooldown_seconds=0.0)  # 100% min edge
        gen = SignalGenerator(hub, calculator, config)
        book_b = OrderBook(symbol="BTC/USDT", exchange="binance")
        book_b.apply_snapshot([("50000.00", "1.0")], [("50001.00", "1.0")])
        book_o = OrderBook(symbol="BTC/USDT", exchange="okx")
        book_o.apply_snapshot([("50010.00", "1.0")], [("50011.00", "1.0")])
        hub.update(book_b)
        books = {"binance": book_b, "okx": book_o}
        signal = await gen.on_orderbook_update(book_o, books, Decimal("1.0"))
        assert signal is None

    @pytest.mark.asyncio
    async def test_signal_metadata_contains_costs(self, generator, buy_book, sell_book):
        generator._hub.update(buy_book)
        books = {"binance": buy_book, "okx": sell_book}
        signal = await generator.on_orderbook_update(sell_book, books, Decimal("1.0"))
        assert signal is not None
        assert "net_profit" in signal.metadata
        assert "fee_total" in signal.metadata
        assert "slippage_total" in signal.metadata

    @pytest.mark.asyncio
    async def test_no_signal_missing_orderbook(self, generator, buy_book, sell_book):
        # Don't provide buy_book in books dict
        generator._hub.update(buy_book)
        books = {"okx": sell_book}  # missing binance
        signal = await generator.on_orderbook_update(sell_book, books, Decimal("1.0"))
        assert signal is None


class TestSignalDeduplication:
    @pytest.mark.asyncio
    async def test_dedup_suppresses_duplicate(self, buy_book, sell_book, hub, calculator):
        config = SignalConfig(
            min_edge=Decimal("0.0001"),
            cooldown_seconds=10.0,  # 10s cooldown
        )
        gen = SignalGenerator(hub, calculator, config)
        hub.update(buy_book)
        books = {"binance": buy_book, "okx": sell_book}

        signal1 = await gen.on_orderbook_update(sell_book, books, Decimal("1.0"))
        signal2 = await gen.on_orderbook_update(sell_book, books, Decimal("1.0"))

        assert signal1 is not None
        assert signal2 is None  # deduplicated

    @pytest.mark.asyncio
    async def test_dedup_allows_after_cooldown(self, buy_book, sell_book, hub, calculator):
        config = SignalConfig(
            min_edge=Decimal("0.0001"),
            cooldown_seconds=0.01,  # very short cooldown
        )
        gen = SignalGenerator(hub, calculator, config)
        hub.update(buy_book)
        books = {"binance": buy_book, "okx": sell_book}

        signal1 = await gen.on_orderbook_update(sell_book, books, Decimal("1.0"))
        await asyncio.sleep(0.05)  # wait out cooldown
        signal2 = await gen.on_orderbook_update(sell_book, books, Decimal("1.0"))

        assert signal1 is not None
        assert signal2 is not None  # allowed after cooldown

    @pytest.mark.asyncio
    async def test_different_pairs_not_deduped(self, hub, calculator, config):
        gen = SignalGenerator(hub, calculator, config)

        book_btc_b = OrderBook(symbol="BTC/USDT", exchange="binance")
        book_btc_b.apply_snapshot([("50000.00", "1.0")], [("50001.00", "1.0")])
        book_btc_o = OrderBook(symbol="BTC/USDT", exchange="okx")
        book_btc_o.apply_snapshot([("50500.00", "1.0")], [("50501.00", "1.0")])

        book_eth_b = OrderBook(symbol="ETH/USDT", exchange="binance")
        book_eth_b.apply_snapshot([("3000.00", "1.0")], [("3001.00", "1.0")])
        book_eth_o = OrderBook(symbol="ETH/USDT", exchange="okx")
        book_eth_o.apply_snapshot([("3030.00", "1.0")], [("3031.00", "1.0")])

        hub.update(book_btc_b)
        hub.update(book_eth_b)

        sig_btc = await gen.on_orderbook_update(
            book_btc_o, {"binance": book_btc_b, "okx": book_btc_o}, Decimal("1.0")
        )
        sig_eth = await gen.on_orderbook_update(
            book_eth_o, {"binance": book_eth_b, "okx": book_eth_o}, Decimal("1.0")
        )

        assert sig_btc is not None
        assert sig_eth is not None  # different symbol, not deduped


class TestSignalRedisPublish:
    @pytest.mark.asyncio
    async def test_publishes_to_event_bus(self, buy_book, sell_book, hub, calculator, config):
        mock_bus = AsyncMock()
        mock_bus.publish = AsyncMock(return_value=b"1234-0")
        gen = SignalGenerator(hub, calculator, config, event_bus=mock_bus)
        hub.update(buy_book)
        books = {"binance": buy_book, "okx": sell_book}
        signal = await gen.on_orderbook_update(sell_book, books, Decimal("1.0"))
        assert signal is not None
        mock_bus.publish.assert_called_once()
        call_args = mock_bus.publish.call_args
        assert call_args[0][0] == "leviathan:signals"

    @pytest.mark.asyncio
    async def test_no_publish_when_signal_filtered(self, buy_book, hub, calculator):
        mock_bus = AsyncMock()
        config = SignalConfig(min_edge=Decimal("1.0"), cooldown_seconds=0.0)
        gen = SignalGenerator(hub, calculator, config, event_bus=mock_bus)
        book_o = OrderBook(symbol="BTC/USDT", exchange="okx")
        book_o.apply_snapshot([("50005.00", "1.0")], [("50006.00", "1.0")])
        hub.update(buy_book)
        books = {"binance": buy_book, "okx": book_o}
        signal = await gen.on_orderbook_update(book_o, books, Decimal("1.0"))
        assert signal is None
        mock_bus.publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_bus_error_does_not_raise(self, buy_book, sell_book, hub, calculator, config):
        mock_bus = AsyncMock()
        mock_bus.publish = AsyncMock(side_effect=Exception("Redis down"))
        gen = SignalGenerator(hub, calculator, config, event_bus=mock_bus)
        hub.update(buy_book)
        books = {"binance": buy_book, "okx": sell_book}
        # Should not raise even if publish fails
        signal = await gen.on_orderbook_update(sell_book, books, Decimal("1.0"))
        assert signal is not None  # signal still returned despite publish failure


class TestMinPriceFilter:
    """Tests for the min_price_usd gate — filters out penny coins."""

    @pytest.mark.asyncio
    async def test_returns_none_when_buy_price_below_threshold(self, hub, calculator):
        """buy_price (0.05) < min_price_usd (0.10) returns None."""
        config = SignalConfig(
            min_edge=Decimal("0.0001"),
            cooldown_seconds=0.0,
            min_price_usd=Decimal("0.10"),
        )
        gen = SignalGenerator(hub, calculator, config)
        book_b = OrderBook(symbol="PENNY/USDT", exchange="binance")
        book_b.apply_snapshot(bids=[("0.04", "1000.0")], asks=[("0.05", "1000.0")])
        book_o = OrderBook(symbol="PENNY/USDT", exchange="okx")
        book_o.apply_snapshot(bids=[("0.06", "1000.0")], asks=[("0.07", "1000.0")])
        hub.update(book_b)
        books = {"binance": book_b, "okx": book_o}
        signal = await gen.on_orderbook_update(book_o, books, Decimal("100.0"))
        assert signal is None

    @pytest.mark.asyncio
    async def test_passes_gate_when_buy_price_equals_threshold(self, hub, calculator):
        """buy_price == min_price_usd uses strict < so equals-case passes the gate."""
        config = SignalConfig(
            min_edge=Decimal("0.0001"),
            cooldown_seconds=0.0,
            min_price_usd=Decimal("50001"),  # exactly equal to buy_price from fixture
        )
        gen = SignalGenerator(hub, calculator, config)
        book_b = OrderBook(symbol="BTC/USDT", exchange="binance")
        book_b.apply_snapshot(bids=[("50000.00", "10.0")], asks=[("50001.00", "10.0")])
        book_o = OrderBook(symbol="BTC/USDT", exchange="okx")
        book_o.apply_snapshot(bids=[("50500.00", "10.0")], asks=[("50501.00", "10.0")])
        hub.update(book_b)
        books = {"binance": book_b, "okx": book_o}
        signal = await gen.on_orderbook_update(book_o, books, Decimal("1.0"))
        # 50001 < 50001 is False — price gate does NOT block
        assert signal is not None

    @pytest.mark.asyncio
    async def test_returns_none_when_buy_price_is_zero(self, hub, calculator):
        """buy_price = Decimal('0') returns None (0 < 0.10 threshold)."""
        config = SignalConfig(
            min_edge=Decimal("0.0001"),
            cooldown_seconds=0.0,
            min_price_usd=Decimal("0.10"),
        )
        gen = SignalGenerator(hub, calculator, config)
        book_b = OrderBook(symbol="ZERO/USDT", exchange="binance")
        book_b.apply_snapshot(bids=[("0.00", "1000.0")], asks=[("0.00", "1000.0")])
        book_o = OrderBook(symbol="ZERO/USDT", exchange="okx")
        book_o.apply_snapshot(bids=[("0.01", "1000.0")], asks=[("0.02", "1000.0")])
        hub.update(book_b)
        books = {"binance": book_b, "okx": book_o}
        signal = await gen.on_orderbook_update(book_o, books, Decimal("100.0"))
        assert signal is None

    @pytest.mark.asyncio
    async def test_passes_gate_when_buy_price_above_threshold(
        self, buy_book, sell_book, hub, calculator
    ):
        """buy_price (BTC ~50k) >> min_price_usd (0.10) — gate does not block."""
        config = SignalConfig(
            min_edge=Decimal("0.0001"),
            cooldown_seconds=0.0,
            min_price_usd=Decimal("0.10"),
        )
        gen = SignalGenerator(hub, calculator, config)
        hub.update(buy_book)
        books = {"binance": buy_book, "okx": sell_book}
        signal = await gen.on_orderbook_update(sell_book, books, Decimal("1.0"))
        assert signal is not None

    @pytest.mark.asyncio
    async def test_all_prices_pass_when_min_price_usd_is_zero(
        self, buy_book, sell_book, hub, calculator
    ):
        """min_price_usd=0 disables the filter; any price passes the gate."""
        config = SignalConfig(
            min_edge=Decimal("0.0001"),
            cooldown_seconds=0.0,
            min_price_usd=Decimal("0"),  # filter disabled
        )
        gen = SignalGenerator(hub, calculator, config)
        hub.update(buy_book)
        books = {"binance": buy_book, "okx": sell_book}
        signal = await gen.on_orderbook_update(sell_book, books, Decimal("1.0"))
        assert signal is not None


class TestMaxRollbackGate:
    @pytest.mark.asyncio
    async def test_signal_blocked_by_max_rollback_cost(self, buy_book, sell_book, hub, calculator):
        config = SignalConfig(
            min_edge=Decimal("0.0001"),
            cooldown_seconds=0.0,
            max_rollback_cost_usd=Decimal("0.000001"),  # essentially zero
        )
        # Force rollback probability to 100% to make rollback cost huge
        from src.friction.cost_calculator import TradeOutcome
        for _ in range(5):
            calculator.record_trade(TradeOutcome(rolled_back=True, rollback_cost=Decimal("100")))

        gen = SignalGenerator(hub, calculator, config)
        hub.update(buy_book)
        books = {"binance": buy_book, "okx": sell_book}
        signal = await gen.on_orderbook_update(sell_book, books, Decimal("1.0"))
        assert signal is None


class TestSlippageBuffer:
    """US-326: slippage_buffer_bps subtracts safety margin from net_edge."""

    @pytest.mark.asyncio
    async def test_large_buffer_blocks_marginal_signal(self, buy_book, sell_book, hub, calculator):
        """A large slippage buffer should block a signal that would otherwise pass."""
        # Without buffer: signal passes (spread ~1% = 100bps, well above 1bps min_edge)
        config_no_buf = SignalConfig(
            min_edge=Decimal("0.0001"),
            cooldown_seconds=0.0,
            max_rollback_cost_usd=Decimal("1000"),
            slippage_buffer_bps=Decimal("0"),
        )
        gen_no_buf = SignalGenerator(hub, calculator, config_no_buf)
        hub.update(buy_book)
        books = {"binance": buy_book, "okx": sell_book}
        signal = await gen_no_buf.on_orderbook_update(sell_book, books, Decimal("1.0"))
        assert signal is not None, "Signal should pass without buffer"

        # With huge buffer (10000 bps = 100%): signal should be blocked
        hub2 = PriceHub()
        config_buf = SignalConfig(
            min_edge=Decimal("0.0001"),
            cooldown_seconds=0.0,
            max_rollback_cost_usd=Decimal("1000"),
            slippage_buffer_bps=Decimal("10000"),  # 100% buffer → kills all signals
        )
        gen_buf = SignalGenerator(hub2, calculator, config_buf)
        hub2.update(buy_book)
        signal2 = await gen_buf.on_orderbook_update(sell_book, books, Decimal("1.0"))
        assert signal2 is None, "Huge slippage buffer should block signal"

    @pytest.mark.asyncio
    async def test_zero_buffer_no_effect(self, buy_book, sell_book, hub, calculator):
        """Zero buffer should not affect signal generation."""
        config = SignalConfig(
            min_edge=Decimal("0.0001"),
            cooldown_seconds=0.0,
            max_rollback_cost_usd=Decimal("1000"),
            slippage_buffer_bps=Decimal("0"),
        )
        gen = SignalGenerator(hub, calculator, config)
        hub.update(buy_book)
        books = {"binance": buy_book, "okx": sell_book}
        signal = await gen.on_orderbook_update(sell_book, books, Decimal("1.0"))
        assert signal is not None

    @pytest.mark.asyncio
    async def test_buffer_default_is_zero(self):
        """Default slippage_buffer_bps should be 0."""
        config = SignalConfig()
        assert config.slippage_buffer_bps == Decimal("0")


class TestActiveHoursKST:
    """US-327: active_hours_kst time-based signal gating."""

    @pytest.mark.asyncio
    async def test_none_active_hours_always_active(self, buy_book, sell_book, hub, calculator):
        """None active_hours means always active."""
        config = SignalConfig(
            min_edge=Decimal("0.0001"),
            cooldown_seconds=0.0,
            max_rollback_cost_usd=Decimal("1000"),
            active_hours_kst=None,
        )
        gen = SignalGenerator(hub, calculator, config)
        hub.update(buy_book)
        books = {"binance": buy_book, "okx": sell_book}
        signal = await gen.on_orderbook_update(sell_book, books, Decimal("1.0"))
        assert signal is not None

    @pytest.mark.asyncio
    async def test_outside_active_hours_blocks_signal(self, buy_book, sell_book, hub, calculator):
        """Signal should be blocked when current KST hour is outside active_hours."""
        from unittest.mock import patch
        from datetime import datetime, timezone, timedelta

        # Force KST hour to 3 AM (outside 9-21)
        fake_dt = datetime(2026, 3, 23, 3, 0, 0, tzinfo=timezone(timedelta(hours=9)))
        config = SignalConfig(
            min_edge=Decimal("0.0001"),
            cooldown_seconds=0.0,
            max_rollback_cost_usd=Decimal("1000"),
            active_hours_kst=(9, 21),
        )
        gen = SignalGenerator(hub, calculator, config)
        hub.update(buy_book)
        books = {"binance": buy_book, "okx": sell_book}
        with patch("src.core.signal.datetime") as mock_dt:
            mock_dt.now.return_value = fake_dt
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            signal = await gen.on_orderbook_update(sell_book, books, Decimal("1.0"))
        assert signal is None, "Should block signal outside active hours"

    @pytest.mark.asyncio
    async def test_inside_active_hours_allows_signal(self, buy_book, sell_book, hub, calculator):
        """Signal should pass when current KST hour is inside active_hours."""
        from unittest.mock import patch
        from datetime import datetime, timezone, timedelta

        # Force KST hour to 14:00 (inside 9-21)
        fake_dt = datetime(2026, 3, 23, 14, 0, 0, tzinfo=timezone(timedelta(hours=9)))
        config = SignalConfig(
            min_edge=Decimal("0.0001"),
            cooldown_seconds=0.0,
            max_rollback_cost_usd=Decimal("1000"),
            active_hours_kst=(9, 21),
        )
        gen = SignalGenerator(hub, calculator, config)
        hub.update(buy_book)
        books = {"binance": buy_book, "okx": sell_book}
        with patch("src.core.signal.datetime") as mock_dt:
            mock_dt.now.return_value = fake_dt
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            signal = await gen.on_orderbook_update(sell_book, books, Decimal("1.0"))
        assert signal is not None, "Should allow signal inside active hours"

    def test_active_hours_default_is_none(self):
        """Default active_hours_kst should be None (always active)."""
        config = SignalConfig()
        assert config.active_hours_kst is None
