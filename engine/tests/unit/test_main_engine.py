"""Extended coverage tests for src/main.py — DataMode, init methods, lifecycle."""
from __future__ import annotations

import asyncio
import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch, call

from src.main import DataMode, Engine, EngineState, _StubCostCalculator


# ---------------------------------------------------------------------------
# DataMode constants
# ---------------------------------------------------------------------------

class TestDataMode:
    def test_synthetic_constant(self):
        assert DataMode.SYNTHETIC == "synthetic"

    def test_real_public_constant(self):
        assert DataMode.REAL_PUBLIC == "real_public"

    def test_real_authenticated_constant(self):
        assert DataMode.REAL_AUTHENTICATED == "real_authenticated"

    def test_shadow_constant(self):
        assert DataMode.SHADOW == "shadow"

    def test_all_four_modes_exist(self):
        modes = [DataMode.SYNTHETIC, DataMode.REAL_PUBLIC, DataMode.REAL_AUTHENTICATED, DataMode.SHADOW]
        assert len(set(modes)) == 4  # all distinct


# ---------------------------------------------------------------------------
# _StubCostCalculator
# ---------------------------------------------------------------------------

class TestStubCostCalculator:
    def test_estimate_cost_returns_decimal(self):
        stub = _StubCostCalculator()
        cost = stub.estimate_cost(
            exchange_id="binance",
            symbol="BTC/USDT",
            side="buy",
            size=Decimal("1.0"),
            price=Decimal("50000"),
        )
        assert isinstance(cost, Decimal)

    def test_estimate_cost_is_0_1_pct_of_position(self):
        stub = _StubCostCalculator()
        cost = stub.estimate_cost(
            exchange_id="binance",
            symbol="BTC/USDT",
            side="buy",
            size=Decimal("1.0"),
            price=Decimal("100"),
        )
        assert cost == Decimal("0.1")  # 100 * 1.0 * 0.001

    def test_estimate_cost_scales_with_size(self):
        stub = _StubCostCalculator()
        cost1 = stub.estimate_cost("ex", "SYM", "buy", Decimal("1"), Decimal("100"))
        cost2 = stub.estimate_cost("ex", "SYM", "buy", Decimal("2"), Decimal("100"))
        assert cost2 == cost1 * 2


# ---------------------------------------------------------------------------
# Engine.__init__
# ---------------------------------------------------------------------------

class TestEngineInit:
    def test_init_default_context(self):
        engine = Engine()
        assert engine.context is not None
        assert engine.state is not None
        assert engine.state.running is False

    def test_init_with_custom_context(self):
        from src.api.server import EngineContext
        ctx = EngineContext()
        engine = Engine(context=ctx)
        assert engine.context is ctx

    def test_init_subsystems_all_none_or_empty(self):
        engine = Engine()
        assert engine._settings is None
        assert engine._event_bus is None
        assert engine._exchanges == {}
        assert engine._price_hub is None
        assert engine._telegram is None
        assert engine._strategy_manager is None
        assert engine._risk_guardian is None
        assert engine._executor is None
        assert engine._trade_consumer is None
        assert engine._db_pool is None
        assert engine._market_recorder is None
        assert engine._paper_mode is None
        assert engine._live_gate is None

    def test_init_data_mode_default_synthetic(self):
        engine = Engine()
        assert engine._data_mode == DataMode.SYNTHETIC

    def test_shutdown_event_is_set_initially_not_set(self):
        engine = Engine()
        assert not engine._shutdown_event.is_set()


# ---------------------------------------------------------------------------
# Engine._init_config
# ---------------------------------------------------------------------------

class TestEngineInitConfig:
    @pytest.mark.asyncio
    async def test_init_config_loads_settings(self):
        engine = Engine()
        mock_settings = MagicMock()
        mock_settings.engine_env = "test"
        mock_settings.execution_mode.value = "paper"
        mock_settings.capital.tier = "alpha"

        with patch("src.main.get_settings", return_value=mock_settings):
            await engine._init_config()

        assert engine._settings is mock_settings
        assert engine.context.environment == "test"

    @pytest.mark.asyncio
    async def test_init_config_fallback_on_exception(self):
        engine = Engine()
        with patch("src.main.get_settings", side_effect=Exception("config error")):
            await engine._init_config()

        assert engine._settings is not None
        assert engine.context.environment == "dev"
        assert engine.context.execution_mode == "paper"


# ---------------------------------------------------------------------------
# Engine._init_telegram / _init_rust_bridge
# ---------------------------------------------------------------------------

class TestEngineInitHelpers:
    def test_init_rust_bridge_exception_nonfatal(self):
        engine = Engine()
        with patch("src.core.rust_bridge.get_feature_flags", side_effect=Exception("rust error")):
            engine._init_rust_bridge()  # must not raise

    def test_init_telegram_exception_nonfatal(self):
        engine = Engine()
        with patch("src.infra.telegram_trade_bot.TradeTelegramBot", side_effect=Exception("tg error")):
            engine._init_telegram()  # must not raise

    def test_init_telegram_disabled_logs_info(self):
        """Phase S21: 3-Bot system — TradeTelegramBot replaces legacy TelegramAlerter."""
        engine = Engine()
        mock_trade_bot = MagicMock()
        mock_trade_bot.enabled = False
        with patch("src.infra.telegram_trade_bot.TradeTelegramBot", return_value=mock_trade_bot):
            engine._init_telegram()
        assert engine._trade_bot is mock_trade_bot
        # self._telegram points to trade_bot for backward compat
        assert engine._telegram is mock_trade_bot


# ---------------------------------------------------------------------------
# Engine._init_exchanges
# ---------------------------------------------------------------------------

class TestEngineInitExchanges:
    @pytest.mark.asyncio
    async def test_init_exchanges_paper_mode_creates_two_adapters(self):
        from src.core.config import ExecutionMode
        engine = Engine()
        mock_settings = MagicMock()
        mock_settings.execution_mode = ExecutionMode.PAPER
        mock_settings.capital.initial_capital = Decimal("70")
        engine._settings = mock_settings

        mock_adapter = AsyncMock()
        mock_executor_inst = MagicMock()
        mock_adapter_inst = mock_adapter

        with patch("src.execution.paper.PaperExecutor", return_value=mock_executor_inst):
            with patch("src.execution.paper_adapter.PaperExchangeAdapter", return_value=mock_adapter_inst):
                await engine._init_exchanges()

        assert len(engine._exchanges) == 2
        assert "paper_binance" in engine._exchanges
        assert "paper_okx" in engine._exchanges

    @pytest.mark.asyncio
    async def test_init_exchanges_sandbox_non_native_no_adapters(self):
        from src.core.config import ExecutionMode
        engine = Engine()
        mock_settings = MagicMock()
        mock_settings.execution_mode = ExecutionMode.SANDBOX
        mock_settings.trading.use_native_adapters = False
        mock_settings.trading.active_exchanges = ["binance"]
        engine._settings = mock_settings

        await engine._init_exchanges()
        assert len(engine._exchanges) == 0  # TODO path, no adapters created

    @pytest.mark.asyncio
    async def test_init_exchanges_live_non_native_no_adapters(self):
        from src.core.config import ExecutionMode
        engine = Engine()
        mock_settings = MagicMock()
        mock_settings.execution_mode = ExecutionMode.LIVE
        mock_settings.trading.use_native_adapters = False
        mock_settings.trading.active_exchanges = ["binance"]
        engine._settings = mock_settings

        await engine._init_exchanges()
        assert len(engine._exchanges) == 0

    @pytest.mark.asyncio
    async def test_init_native_exchanges_value_error_skips(self):
        from src.core.config import ExecutionMode
        engine = Engine()
        mock_settings = MagicMock()
        mock_settings.exchange = None
        engine._settings = mock_settings

        with patch("src.infra.exchange.create_native_adapter", side_effect=ValueError("unsupported")):
            await engine._init_native_exchanges(["binance"], sandbox=False)

        assert len(engine._exchanges) == 0

    @pytest.mark.asyncio
    async def test_init_native_exchanges_generic_exception_skips(self):
        engine = Engine()
        mock_settings = MagicMock()
        mock_settings.exchange = None
        engine._settings = mock_settings

        with patch("src.infra.exchange.create_native_adapter", side_effect=Exception("connect fail")):
            await engine._init_native_exchanges(["binance"], sandbox=False)

        assert len(engine._exchanges) == 0

    @pytest.mark.asyncio
    async def test_init_native_exchanges_success(self):
        engine = Engine()
        mock_settings = MagicMock()
        mock_settings.exchange = MagicMock()
        mock_settings.exchange.binance_api_key = "key"
        mock_settings.exchange.binance_api_secret = "secret"
        mock_settings.exchange.binance_passphrase = ""
        engine._settings = mock_settings

        mock_adapter = AsyncMock()
        with patch("src.infra.exchange.create_native_adapter", return_value=mock_adapter):
            await engine._init_native_exchanges(["binance"], sandbox=True)

        assert "binance" in engine._exchanges


# ---------------------------------------------------------------------------
# Engine.stop() with various active subsystems
# ---------------------------------------------------------------------------

class TestEngineStopSubsystems:
    @pytest.mark.asyncio
    async def test_stop_with_trade_consumer(self):
        engine = Engine()
        engine.state.running = True
        mock_consumer = AsyncMock()
        engine._trade_consumer = mock_consumer

        await engine.stop()

        mock_consumer.stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop_with_strategy_manager(self):
        engine = Engine()
        engine.state.running = True
        mock_manager = AsyncMock()
        engine._strategy_manager = mock_manager

        await engine.stop()

        mock_manager.stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop_disconnects_exchanges(self):
        engine = Engine()
        engine.state.running = True
        mock_adapter = AsyncMock()
        engine._exchanges = {"binance": mock_adapter, "okx": mock_adapter}

        await engine.stop()

        assert mock_adapter.disconnect.call_count == 2

    @pytest.mark.asyncio
    async def test_stop_with_shadow_mode(self):
        engine = Engine()
        engine.state.running = True
        mock_shadow = AsyncMock()
        engine._paper_mode = mock_shadow

        await engine.stop()

        mock_shadow.stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop_with_live_gate(self):
        engine = Engine()
        engine.state.running = True
        mock_gate = AsyncMock()
        engine._live_gate = mock_gate

        await engine.stop()

        mock_gate.stop_auto_evaluation.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop_with_market_recorder(self):
        engine = Engine()
        engine.state.running = True
        mock_recorder = AsyncMock()
        engine._market_recorder = mock_recorder

        await engine.stop()

        mock_recorder.stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop_with_db_pool(self):
        engine = Engine()
        engine.state.running = True
        mock_db = AsyncMock()
        engine._db_pool = mock_db

        await engine.stop()

        mock_db.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop_trade_consumer_exception_nonfatal(self):
        engine = Engine()
        engine.state.running = True
        mock_consumer = AsyncMock()
        mock_consumer.stop.side_effect = Exception("stop error")
        engine._trade_consumer = mock_consumer

        await engine.stop()  # must not raise

    @pytest.mark.asyncio
    async def test_stop_exchange_disconnect_exception_nonfatal(self):
        engine = Engine()
        engine.state.running = True
        mock_adapter = AsyncMock()
        mock_adapter.disconnect.side_effect = Exception("disconnect error")
        engine._exchanges = {"binance": mock_adapter}

        await engine.stop()  # must not raise

    @pytest.mark.asyncio
    async def test_stop_cancels_background_tasks(self):
        engine = Engine()
        engine.state.running = True
        mock_task = MagicMock()
        mock_task.done.return_value = False
        mock_task.cancel = MagicMock()
        engine.state.background_tasks.append(mock_task)

        with patch("asyncio.wait", new=AsyncMock()):
            await engine.stop()

        mock_task.cancel.assert_called_once()


# ---------------------------------------------------------------------------
# Engine._handle_signal
# ---------------------------------------------------------------------------

class TestEngineHandleSignal:
    def test_handle_signal_sets_shutdown_event(self):
        engine = Engine()
        assert not engine._shutdown_event.is_set()
        engine._handle_signal()
        assert engine._shutdown_event.is_set()


# ---------------------------------------------------------------------------
# Engine._populate_context
# ---------------------------------------------------------------------------

class TestEnginePopulateContext:
    @pytest.mark.asyncio
    async def test_populate_context_wires_strategy_manager(self):
        engine = Engine()
        mock_manager = MagicMock()
        mock_manager.list_strategies.return_value = ["arb_v1"]
        mock_strategy = MagicMock()
        mock_strategy.is_active = True
        mock_strategy.STRATEGY_TYPE = "cross_exchange"
        mock_manager.get_strategy.return_value = mock_strategy
        engine._strategy_manager = mock_manager

        await engine._populate_context()

        assert engine.context.strategy_manager is mock_manager
        assert "arb_v1" in engine.context.strategies

    @pytest.mark.asyncio
    async def test_populate_context_no_strategy_manager(self):
        engine = Engine()
        engine._strategy_manager = None
        await engine._populate_context()  # must not raise


# ---------------------------------------------------------------------------
# build_app
# ---------------------------------------------------------------------------

class TestBuildApp:
    def test_build_app_returns_fastapi_app(self):
        from src.main import build_app
        app = build_app()
        assert app is not None
