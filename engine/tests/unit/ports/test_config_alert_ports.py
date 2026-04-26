"""Phase 7 extension: ConfigPort + AlertPort runtime_checkable 검증.

Codex SUGGEST (2026-04-26): runtime의 강한 결합 추가 분리 (ConfigPort, AlertPort).
"""
from __future__ import annotations

from typing import Any

from src.ports import AlertPort, ConfigPort


class _MockConfig:
    def get(self, dotpath: str, default: Any = None) -> Any:
        return default

    def get_bool(self, name: str) -> bool:
        return False


class _MockAlert:
    async def send_alert_kr(self, alert_type: str, data: dict[str, Any]) -> bool:
        return True

    async def send_fill_kr(self, data: dict[str, Any]) -> bool:
        return True


class TestConfigPort:
    def test_mock_implements_port(self) -> None:
        assert isinstance(_MockConfig(), ConfigPort)


class TestAlertPort:
    def test_mock_implements_port(self) -> None:
        assert isinstance(_MockAlert(), AlertPort)


class TestPortsExtended:
    def test_required_11_ports_in_all(self) -> None:
        from src.ports import __all__
        required = {
            "AlertPort", "ConfigPort", "DataFeedPort", "EventBusPort",
            "ExchangeAdapterPort", "ExecutorPort", "JournalPort",
            "KillSwitchPort", "LedgerPort", "MetricsPort", "RiskPort",
        }
        assert required.issubset(set(__all__)), \
            f"missing required ports: {required - set(__all__)}"
