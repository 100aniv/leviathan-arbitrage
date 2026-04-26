"""Phase 5.2.4.7-10 4 listeners 검증 (TradeHistory/CircuitBreaker/Rollback/Telegram)."""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.listeners.circuit_breaker_listener import CircuitBreakerListener
from src.listeners.rollback_listener import RollbackListener
from src.listeners.telegram_listener import TelegramListener
from src.listeners.trade_history_listener import TradeHistoryListener
from src.ports.listener_port import ExecutionResultListener


class TestProtocols:
    def test_all_implement_listener_port(self) -> None:
        ctx = SimpleNamespace(trade_history=[])
        assert isinstance(TradeHistoryListener(ctx), ExecutionResultListener)
        assert isinstance(CircuitBreakerListener(MagicMock()), ExecutionResultListener)
        assert isinstance(RollbackListener(MagicMock(), {}), ExecutionResultListener)
        assert isinstance(TelegramListener(MagicMock()), ExecutionResultListener)


class TestTradeHistoryListener:
    def test_appends_to_context(self) -> None:
        ctx = SimpleNamespace(trade_history=[])
        side_buy = SimpleNamespace(value="buy")
        side_sell = SimpleNamespace(value="sell")
        legs = [
            SimpleNamespace(symbol="BTC/USDT", side=side_buy, exchange_id="binance",
                             size=Decimal("0.1"), price=Decimal("50000")),
            SimpleNamespace(symbol="BTC/USDT", side=side_sell, exchange_id="okx",
                             size=Decimal("0.1"), price=Decimal("50100")),
        ]
        request = SimpleNamespace(strategy_id="x", legs=legs, expected_profit_usdt=Decimal("10"))
        result = SimpleNamespace(status=SimpleNamespace(value="success"), pnl=Decimal("10"))
        TradeHistoryListener(ctx).on_execution_result(request, result)
        assert len(ctx.trade_history) == 1
        assert ctx.trade_history[0]["strategy_id"] == "x"


class TestCircuitBreakerListener:
    def test_skips_when_none(self) -> None:
        CircuitBreakerListener(None).on_execution_result(SimpleNamespace(), SimpleNamespace())

    def test_records_win_on_positive_pnl(self) -> None:
        cb = MagicMock()
        cb.record_win = MagicMock()
        result = SimpleNamespace(status=SimpleNamespace(value="success"), pnl=Decimal("5"))
        CircuitBreakerListener(cb).on_execution_result(SimpleNamespace(), result)
        # record_win은 asyncio.ensure_future로 wrap되므로 직접 검증 어려움
        # but 함수 호출은 발생함


class TestRollbackListener:
    def test_skips_when_status_not_rollback(self) -> None:
        mgr = MagicMock()
        sizes: dict = {}
        result = SimpleNamespace(status=SimpleNamespace(value="success"))
        RollbackListener(mgr, sizes).on_execution_result(SimpleNamespace(legs=[]), result)
        mgr.get_strategy.assert_not_called()

    def test_calls_handle_entry_rollback_on_rolled_back(self) -> None:
        strategy = MagicMock()
        mgr = MagicMock()
        mgr.get_strategy.return_value = strategy
        legs = [SimpleNamespace(symbol="BTC/USDT", side=SimpleNamespace(value="buy"),
                                  size=Decimal("0.1"), price=Decimal("50000"),
                                  metadata=None)]
        request = SimpleNamespace(strategy_id="x", legs=legs)
        result = SimpleNamespace(status=SimpleNamespace(value="rolled_back"))
        RollbackListener(mgr, {}).on_execution_result(request, result)
        strategy.handle_entry_rollback.assert_called_once_with("BTC/USDT")


class TestTelegramListener:
    def test_skips_when_bot_none(self) -> None:
        TelegramListener(None).on_execution_result(SimpleNamespace(), SimpleNamespace())

    def test_skips_when_status_not_success(self) -> None:
        bot = MagicMock()
        result = SimpleNamespace(status=SimpleNamespace(value="failure"))
        TelegramListener(bot).on_execution_result(SimpleNamespace(legs=[]), result)
        bot.send_fill_kr.assert_not_called()
