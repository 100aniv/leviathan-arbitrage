"""Phase 5.1 모든 7 Ports runtime_checkable Protocol 검증."""
from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from src.ports import (
    DataFeedPort,
    ExchangeAdapterPort,
    ExecutorPort,
    JournalPort,
    KillSwitchPort,
    LedgerPort,
    RiskPort,
)


class _MockExchangeAdapter:
    exchange_id = "mock"
    async def connect(self) -> None: pass
    async def disconnect(self) -> None: pass
    async def place_order(self, order: Any) -> Any: return None
    async def cancel_order(self, order_id: str, symbol: str | None = None) -> bool: return True
    def supports_symbol(self, symbol: str) -> bool: return True
    async def get_min_notional(self, symbol: str) -> Decimal: return Decimal("0")
    @property
    def _market_type(self) -> str: return "spot"
    async def get_balances(self) -> dict[str, Any]: return {}
    @property
    def health_score(self) -> float: return 1.0


class _MockExecutor:
    async def execute_trade_request(self, tr: Any) -> Any: return None
    def add_listener(self, l: Any) -> None: pass
    def remove_listener(self, l: Any) -> None: pass


class _MockRisk:
    async def check_proposal(self, tr: Any, sid: str) -> Any: return None
    def record_loss(self, sid: str, pnl: float) -> None: pass
    def record_win(self, sid: str, pnl: float) -> None: pass
    @property
    def is_halted(self) -> bool: return False


class _MockDataFeed:
    async def subscribe(self, symbols: list[str], exchanges: list[str]) -> None: pass
    async def unsubscribe(self) -> None: pass
    def on_orderbook(self, callback: Any) -> None: pass
    def on_trade(self, callback: Any) -> None: pass
    def get_book(self, exchange_id: str, symbol: str) -> Any: return None


class _MockJournal:
    async def start(self) -> None: pass
    async def append(self, order_id: str, state: str, payload=None) -> None: pass
    async def replay(self, since_ts=None): yield None
    async def flush(self) -> None: pass


class _MockLedger:
    def record_pnl(self, sid: str, eid: str, pnl: Decimal, comm: Decimal = Decimal("0")) -> None: pass
    def get_total(self) -> Decimal: return Decimal("0")
    def get_per_strategy(self) -> dict: return {}
    def get_per_exchange(self) -> dict: return {}


class _MockKillSwitch:
    async def halt(self, reason: str) -> None: pass
    def clear(self) -> None: pass
    @property
    def is_active(self) -> bool: return False


class TestAllPortsRuntimeCheckable:
    def test_exchange_adapter_port(self) -> None:
        assert isinstance(_MockExchangeAdapter(), ExchangeAdapterPort)

    def test_executor_port(self) -> None:
        assert isinstance(_MockExecutor(), ExecutorPort)

    def test_risk_port(self) -> None:
        assert isinstance(_MockRisk(), RiskPort)

    def test_data_feed_port(self) -> None:
        assert isinstance(_MockDataFeed(), DataFeedPort)

    def test_journal_port(self) -> None:
        assert isinstance(_MockJournal(), JournalPort)

    def test_ledger_port(self) -> None:
        assert isinstance(_MockLedger(), LedgerPort)

    def test_kill_switch_port(self) -> None:
        assert isinstance(_MockKillSwitch(), KillSwitchPort)

    def test_all_ports_exported(self) -> None:
        """Codex NIT (2026-04-26 v2): required-subset 검증으로 churn 제거.
        새 Port 추가 시 (ConfigPort, AlertPort 등) 본 테스트 수정 불필요."""
        from src.ports import __all__
        required = {"DataFeedPort", "EventBusPort", "ExchangeAdapterPort",
                    "ExecutorPort", "JournalPort", "KillSwitchPort",
                    "LedgerPort", "MetricsPort", "RiskPort"}
        assert required.issubset(set(__all__)), \
            f"missing required ports: {required - set(__all__)}"
