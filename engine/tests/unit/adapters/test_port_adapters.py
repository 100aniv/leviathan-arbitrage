"""Phase 7 — Concrete adapters Port conformance 검증 (2026-04-27).

src/adapters/:
- ConfigAdapter — ConfigPort impl (config_loader 모듈 wrap)
- NoOpMetricsAdapter — MetricsPort no-op
- NoOpAlertAdapter — AlertPort no-op
"""
from __future__ import annotations

import pytest

from src.adapters.config_adapter import ConfigAdapter
from src.adapters.no_op_alert import NoOpAlertAdapter
from src.adapters.no_op_metrics import NoOpMetricsAdapter
from src.ports import AlertPort, ConfigPort, MetricsPort


class TestConfigAdapter:
    def test_implements_config_port(self) -> None:
        assert isinstance(ConfigAdapter(), ConfigPort)

    def test_get_returns_default_when_missing(self) -> None:
        adapter = ConfigAdapter()
        # Missing dotpath returns default
        assert adapter.get("does.not.exist", "fallback") == "fallback"

    def test_get_bool_for_unknown_flag(self) -> None:
        adapter = ConfigAdapter()
        # Unknown flag → False
        assert adapter.get_bool("UNKNOWN_FLAG_XYZ_2026") is False


class TestNoOpMetricsAdapter:
    def test_implements_metrics_port(self) -> None:
        assert isinstance(NoOpMetricsAdapter(), MetricsPort)

    def test_calls_are_no_op(self) -> None:
        adapter = NoOpMetricsAdapter()
        adapter.increment_counter("test", 1.0, {"a": "b"})
        adapter.set_gauge("test", 5.0)
        adapter.observe_histogram("test", 100.0)
        # No-op — just verify no exceptions


class TestNoOpAlertAdapter:
    def test_implements_alert_port(self) -> None:
        assert isinstance(NoOpAlertAdapter(), AlertPort)

    @pytest.mark.asyncio
    async def test_send_alert_kr_returns_true(self) -> None:
        adapter = NoOpAlertAdapter()
        assert await adapter.send_alert_kr("test_alert", {"x": 1}) is True

    @pytest.mark.asyncio
    async def test_send_fill_kr_returns_true(self) -> None:
        adapter = NoOpAlertAdapter()
        assert await adapter.send_fill_kr({"data": "test"}) is True
