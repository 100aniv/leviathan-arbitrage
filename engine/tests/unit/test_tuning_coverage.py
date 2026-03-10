"""Coverage tests for param_bridge.py and StrategyManager additional paths."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.tuning.backtest import StrategyParams
from src.tuning.param_bridge import (
    CEX_DEX,
    CROSS_EXCHANGE,
    FUNDING_RATE,
    FUTURES_FUTURES,
    LATENCY_ARB,
    SPOT_FUTURES,
    STATISTICAL_ARB,
    TRIANGULAR,
    apply_params_to_strategy,
    params_to_strategy_config,
    strategy_config_to_params,
)


# ---------------------------------------------------------------------------
# params_to_strategy_config
# ---------------------------------------------------------------------------


class TestParamsToStrategyConfig:
    def _p(self, **kwargs) -> StrategyParams:
        return StrategyParams(**kwargs)

    def test_cross_exchange_maps_min_spread_and_position(self):
        config = params_to_strategy_config(self._p(min_spread_bps=10.0, max_position_size=500.0), CROSS_EXCHANGE)
        assert config["min_spread_bps"] == pytest.approx(10.0)
        assert config["max_position_size_usdt"] == pytest.approx(500.0)

    def test_cross_exchange_maps_all_fields(self):
        params = self._p(
            min_spread_bps=5.0,
            entry_threshold=0.001,
            exit_threshold=0.0005,
            max_position_size=1000.0,
            stop_loss_pct=0.02,
        )
        config = params_to_strategy_config(params, CROSS_EXCHANGE)
        assert "min_spread_bps" in config
        assert "entry_threshold" in config
        assert "exit_threshold" in config
        assert "max_position_size_usdt" in config
        assert "stop_loss_pct" in config

    def test_triangular_maps_min_profit_bps(self):
        config = params_to_strategy_config(self._p(min_spread_bps=15.0), TRIANGULAR)
        assert "min_profit_bps" in config
        assert config["min_profit_bps"] == pytest.approx(15.0)
        assert "max_notional_usdt" in config

    def test_spot_futures_maps_min_basis_bps(self):
        config = params_to_strategy_config(self._p(min_spread_bps=8.0), SPOT_FUTURES)
        assert "min_basis_bps" in config
        assert config["min_basis_bps"] == pytest.approx(8.0)

    def test_funding_rate_maps_min_funding_rate_bps(self):
        config = params_to_strategy_config(self._p(min_spread_bps=5.0), FUNDING_RATE)
        assert "min_funding_rate_bps" in config

    def test_statistical_arb_maps_z_score_entry(self):
        config = params_to_strategy_config(self._p(min_spread_bps=2.0), STATISTICAL_ARB)
        assert "z_score_entry" in config
        assert config["z_score_entry"] == pytest.approx(2.0)

    def test_latency_arb_maps_min_edge_bps(self):
        config = params_to_strategy_config(self._p(min_spread_bps=3.0), LATENCY_ARB)
        assert "min_edge_bps" in config

    def test_futures_futures_maps_min_spread_bps(self):
        config = params_to_strategy_config(self._p(min_spread_bps=7.0), FUTURES_FUTURES)
        assert "min_spread_bps" in config

    def test_cex_dex_maps_min_spread_bps(self):
        config = params_to_strategy_config(self._p(min_spread_bps=12.0), CEX_DEX)
        assert "min_spread_bps" in config

    def test_unknown_strategy_type_falls_back_to_cross_exchange(self):
        config = params_to_strategy_config(self._p(min_spread_bps=6.0), "unknown_strategy_type")
        # Falls back to CROSS_EXCHANGE mapping
        assert "min_spread_bps" in config

    def test_with_overrides_merges_extra_keys(self):
        params = self._p(min_spread_bps=5.0)
        config = params_to_strategy_config(params, CROSS_EXCHANGE, overrides={"custom_key": 99, "another": "val"})
        assert config["custom_key"] == 99
        assert config["another"] == "val"
        assert "min_spread_bps" in config

    def test_overrides_none_does_not_raise(self):
        params = self._p()
        config = params_to_strategy_config(params, CROSS_EXCHANGE, overrides=None)
        assert "min_spread_bps" in config

    def test_overrides_can_overwrite_mapped_keys(self):
        params = self._p(min_spread_bps=5.0)
        config = params_to_strategy_config(params, CROSS_EXCHANGE, overrides={"min_spread_bps": 999.0})
        assert config["min_spread_bps"] == pytest.approx(999.0)


# ---------------------------------------------------------------------------
# strategy_config_to_params
# ---------------------------------------------------------------------------


class TestStrategyConfigToParams:
    def test_cross_exchange_reverse_mapping(self):
        config = {"min_spread_bps": 10.0, "max_position_size_usdt": 500.0, "stop_loss_pct": 0.02}
        params = strategy_config_to_params(config, CROSS_EXCHANGE)
        assert params.min_spread_bps == pytest.approx(10.0)
        assert params.max_position_size == pytest.approx(500.0)
        assert params.stop_loss_pct == pytest.approx(0.02)

    def test_triangular_reverse_mapping(self):
        config = {"min_profit_bps": 15.0, "max_notional_usdt": 300.0}
        params = strategy_config_to_params(config, TRIANGULAR)
        assert params.min_spread_bps == pytest.approx(15.0)
        assert params.max_position_size == pytest.approx(300.0)

    def test_spot_futures_reverse_mapping(self):
        config = {"min_basis_bps": 8.0}
        params = strategy_config_to_params(config, SPOT_FUTURES)
        assert params.min_spread_bps == pytest.approx(8.0)

    def test_statistical_arb_reverse_mapping(self):
        config = {"z_score_entry": 2.5}
        params = strategy_config_to_params(config, STATISTICAL_ARB)
        assert params.min_spread_bps == pytest.approx(2.5)

    def test_unknown_type_uses_cross_exchange_fallback(self):
        config = {"min_spread_bps": 5.0}
        params = strategy_config_to_params(config, "unknown_type")
        assert params.min_spread_bps == pytest.approx(5.0)

    def test_empty_config_uses_defaults(self):
        params = strategy_config_to_params({}, CROSS_EXCHANGE)
        assert isinstance(params, StrategyParams)


# ---------------------------------------------------------------------------
# apply_params_to_strategy
# ---------------------------------------------------------------------------


class TestApplyParamsToStrategy:
    def test_with_explicit_strategy_type_skips_manager_lookup(self):
        # Lines 153-168: strategy_type provided → no get_strategy call
        manager = MagicMock()
        params = StrategyParams(min_spread_bps=8.0)
        config = apply_params_to_strategy(manager, "s1", params, strategy_type=CROSS_EXCHANGE)
        assert "min_spread_bps" in config
        manager.get_strategy.assert_not_called()

    def test_auto_detect_strategy_type_from_manager(self):
        # Lines 153-156: strategy_type=None → fetches from manager
        strategy = MagicMock()
        strategy.STRATEGY_TYPE = "triangular"
        manager = MagicMock()
        manager.get_strategy.return_value = strategy
        params = StrategyParams(min_spread_bps=12.0)

        config = apply_params_to_strategy(manager, "s1", params, strategy_type=None)
        assert "min_profit_bps" in config  # triangular mapping
        manager.get_strategy.assert_called_once_with("s1")

    def test_auto_detect_falls_back_to_cross_exchange_when_not_found(self):
        # Lines 157-158: strategy not in manager → CROSS_EXCHANGE fallback
        manager = MagicMock()
        manager.get_strategy.return_value = None
        params = StrategyParams(min_spread_bps=5.0)

        config = apply_params_to_strategy(manager, "missing_id", params, strategy_type=None)
        assert "min_spread_bps" in config  # cross_exchange mapping

    def test_auto_detect_strategy_without_strategy_type_attr(self):
        # Strategy exists but has no STRATEGY_TYPE → getattr fallback to CROSS_EXCHANGE
        strategy = MagicMock(spec=[])  # empty spec — no STRATEGY_TYPE attribute
        manager = MagicMock()
        manager.get_strategy.return_value = strategy
        params = StrategyParams(min_spread_bps=5.0)

        config = apply_params_to_strategy(manager, "s1", params, strategy_type=None)
        assert "min_spread_bps" in config

    def test_returns_config_dict(self):
        manager = MagicMock()
        params = StrategyParams()
        config = apply_params_to_strategy(manager, "s1", params, strategy_type=SPOT_FUTURES)
        assert isinstance(config, dict)


# ---------------------------------------------------------------------------
# StrategyManager — additional coverage for uncovered lines
# ---------------------------------------------------------------------------


from src.core.models import Signal
from src.strategies.base import BaseStrategy, CostCalculator, TradeRequest
from src.strategies.cross_exchange import CrossExchangeConfig, CrossExchangeStrategy
from src.strategies.manager import StrategyManager


def _make_event_bus() -> MagicMock:
    bus = MagicMock()
    bus.create_consumer_group = AsyncMock()
    bus.subscribe = AsyncMock(return_value=[])
    bus.publish = AsyncMock()
    return bus


def _make_strategy(strategy_id: str = "s1") -> CrossExchangeStrategy:
    calc = MagicMock(spec=CostCalculator)
    calc.estimate_cost.return_value = Decimal("1")
    return CrossExchangeStrategy(strategy_id, calc, CrossExchangeConfig(min_spread_bps=Decimal("10")))


def _make_signal_dict(strategy_id: str = "cross_exchange_v1") -> dict:
    return {
        "event_type": "signal",
        "event_id": "test-event-001",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": "test",
        "signal": {
            "strategy_id": strategy_id,
            "symbol": "BTC/USDT",
            "buy_exchange": "binance",
            "sell_exchange": "okx",
            "buy_price": "50000",
            "sell_price": "50100",
            "spread_pct": "0.002",
            "confidence": 0.9,
            "volume": "0.5",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    }


class TestStrategyManagerAdditionalCoverage:
    @pytest.mark.asyncio
    async def test_stop_unregistered_strategy_raises_key_error(self):
        # Line 85: KeyError for unknown strategy_id
        manager = StrategyManager(_make_event_bus())
        with pytest.raises(KeyError):
            await manager.stop_strategy("nonexistent_strategy")

    @pytest.mark.asyncio
    async def test_reconfigure_unregistered_raises_key_error(self):
        # Line 98: KeyError for unknown strategy_id
        manager = StrategyManager(_make_event_bus())
        with pytest.raises(KeyError):
            await manager.reconfigure("nonexistent_strategy", {})

    @pytest.mark.asyncio
    async def test_start_idempotent_when_already_running(self):
        # Line 117: if self._running: return early
        manager = StrategyManager(_make_event_bus())
        manager._consume_loop = AsyncMock()
        await manager.start()
        await manager.start()  # Second call is a no-op
        # create_consumer_group called only once
        manager._event_bus.create_consumer_group.assert_awaited_once()
        await manager.stop()

    @pytest.mark.asyncio
    async def test_dispatch_routes_wildcard_to_all_active(self):
        # Lines 204-206: signal.strategy_id == "" routes to ALL active
        bus = _make_event_bus()
        manager = StrategyManager(bus)
        strategy = _make_strategy("s1")
        manager.register(strategy)
        await strategy.start()

        event_dict = _make_signal_dict(strategy_id="")
        await manager._dispatch(event_dict)
        assert strategy.metrics.signals_received == 1

    @pytest.mark.asyncio
    async def test_dispatch_routes_asterisk_to_all_active(self):
        # Lines 204-206: signal.strategy_id == "*" routes to ALL active
        bus = _make_event_bus()
        manager = StrategyManager(bus)
        strategy = _make_strategy("s2")
        manager.register(strategy)
        await strategy.start()

        event_dict = _make_signal_dict(strategy_id="*")
        await manager._dispatch(event_dict)
        assert strategy.metrics.signals_received == 1

    @pytest.mark.asyncio
    async def test_dispatch_skips_inactive_strategy(self):
        # Lines 173-175: is_active check
        bus = _make_event_bus()
        manager = StrategyManager(bus)
        strategy = _make_strategy("s3")
        manager.register(strategy)
        # Not started — is_active = False

        event_dict = _make_signal_dict(strategy_id="s3")
        await manager._dispatch(event_dict)
        assert strategy.metrics.signals_received == 0

    @pytest.mark.asyncio
    async def test_dispatch_no_active_match_logs_debug(self):
        # Lines 199-200: no active strategy matched
        bus = _make_event_bus()
        manager = StrategyManager(bus)
        # No registered strategies

        # Should not raise
        await manager._dispatch(_make_signal_dict(strategy_id="some_unmatched_type_v99"))

    @pytest.mark.asyncio
    async def test_dispatch_shadow_strategy_does_not_emit_trade_request(self):
        # Lines 184-188: shadow_mode=True → no emit
        bus = _make_event_bus()
        manager = StrategyManager(bus)

        # Use a MagicMock strategy that returns a TradeRequest but is in shadow_mode
        strategy = MagicMock(spec=BaseStrategy)
        strategy.strategy_id = "shadow_strat"
        strategy.is_active = True
        strategy.shadow_mode = True
        trade_req = MagicMock(spec=TradeRequest)
        strategy.on_signal = AsyncMock(return_value=trade_req)
        manager._strategies["shadow_strat"] = strategy

        event_dict = _make_signal_dict(strategy_id="shadow_strat")
        with patch.object(manager, "_emit_trade_request", new_callable=AsyncMock) as mock_emit:
            await manager._dispatch(event_dict)
            mock_emit.assert_not_called()

    @pytest.mark.asyncio
    async def test_dispatch_handles_strategy_on_signal_exception(self):
        # Lines 192-197: exception in strategy.on_signal → logged but does not propagate
        bus = _make_event_bus()
        manager = StrategyManager(bus)

        strategy = MagicMock(spec=BaseStrategy)
        strategy.strategy_id = "failing_strat"
        strategy.is_active = True
        strategy.shadow_mode = False
        strategy.on_signal = AsyncMock(side_effect=RuntimeError("signal processing error"))
        manager._strategies["failing_strat"] = strategy

        event_dict = _make_signal_dict(strategy_id="failing_strat")
        # Should not raise
        await manager._dispatch(event_dict)

    @pytest.mark.asyncio
    async def test_consume_loop_handles_exception_and_continues(self):
        # Lines 155-159: general Exception → log error + asyncio.sleep(1) + continue
        bus = _make_event_bus()
        manager = StrategyManager(bus)
        call_count = 0

        async def subscribe_side_effect(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("transient subscribe error")
            manager._running = False
            return []

        bus.subscribe.side_effect = subscribe_side_effect
        manager._running = True

        with patch("asyncio.sleep", new_callable=AsyncMock):
            await manager._consume_loop()

        assert call_count >= 2  # first call raised, second set running=False

    @pytest.mark.asyncio
    async def test_consume_loop_cancelled_error_propagates(self):
        # Line 155-156: CancelledError must re-raise
        bus = _make_event_bus()
        manager = StrategyManager(bus)
        bus.subscribe.side_effect = asyncio.CancelledError()
        manager._running = True

        with pytest.raises(asyncio.CancelledError):
            await manager._consume_loop()

    def test_should_route_exact_strategy_id_match(self):
        # Line 209: exact match
        bus = _make_event_bus()
        manager = StrategyManager(bus)
        strategy = _make_strategy("exact_id_v1")

        signal = Signal(
            strategy_id="exact_id_v1",
            symbol="BTC/USDT",
            buy_exchange="binance",
            sell_exchange="okx",
            buy_price=Decimal("50000"),
            sell_price=Decimal("50100"),
            spread_pct=Decimal("0.002"),
            confidence=0.9,
            volume=Decimal("0.5"),
            timestamp=datetime.now(timezone.utc),
        )
        assert manager._should_route(strategy, signal) is True

    def test_should_route_strategy_type_substring_match(self):
        # Lines 212-217: STRATEGY_TYPE substring match
        # CrossExchangeStrategy.STRATEGY_TYPE = "cross_exchange_spot"
        bus = _make_event_bus()
        manager = StrategyManager(bus)
        strategy = _make_strategy("cross_exchange_spot_v1")

        signal = Signal(
            strategy_id="cross_exchange_spot_v2",  # contains STRATEGY_TYPE "cross_exchange_spot"
            symbol="BTC/USDT",
            buy_exchange="binance",
            sell_exchange="okx",
            buy_price=Decimal("50000"),
            sell_price=Decimal("50100"),
            spread_pct=Decimal("0.002"),
            confidence=0.9,
            volume=Decimal("0.5"),
            timestamp=datetime.now(timezone.utc),
        )
        # "cross_exchange_spot" in "cross_exchange_spot_v2" → True
        assert manager._should_route(strategy, signal) is True

    def test_should_route_returns_false_for_no_match(self):
        # Line 219: no match → False
        bus = _make_event_bus()
        manager = StrategyManager(bus)
        strategy = _make_strategy("cross_exchange_spot_v1")

        signal = Signal(
            strategy_id="funding_rate_okx_v1",  # completely different type
            symbol="BTC/USDT",
            buy_exchange="binance",
            sell_exchange="okx",
            buy_price=Decimal("50000"),
            sell_price=Decimal("50100"),
            spread_pct=Decimal("0.002"),
            confidence=0.9,
            volume=Decimal("0.5"),
            timestamp=datetime.now(timezone.utc),
        )
        # "cross_exchange" not in "funding_rate_okx_v1" and vice versa
        assert manager._should_route(strategy, signal) is False

    def test_should_route_empty_strategy_id_broadcasts(self):
        # Line 205: empty strategy_id → True (broadcast)
        bus = _make_event_bus()
        manager = StrategyManager(bus)
        strategy = _make_strategy("any_strategy")

        signal = Signal(
            strategy_id="",
            symbol="BTC/USDT",
            buy_exchange="binance",
            sell_exchange="okx",
            buy_price=Decimal("50000"),
            sell_price=Decimal("50100"),
            spread_pct=Decimal("0.002"),
            confidence=0.9,
            volume=Decimal("0.5"),
            timestamp=datetime.now(timezone.utc),
        )
        assert manager._should_route(strategy, signal) is True
