"""Tests for engine/src/core/config.py"""
from __future__ import annotations

from decimal import Decimal

import pytest

import src.core.config as cfg_module
from src.core.config import (
    DatabaseSettings,
    ExchangeSettings,
    MonitoringSettings,
    RedisSettings,
    RiskSettings,
    Settings,
    get_settings,
)


class TestRedisSettings:
    def test_defaults(self, monkeypatch):
        # conftest sets REDIS_URL; unset it to test actual defaults
        monkeypatch.delenv("REDIS_URL", raising=False)
        s = RedisSettings()
        assert s.url == "redis://localhost:6379/0"
        assert s.max_connections == 50
        assert s.socket_timeout == 2.0

    def test_from_env(self, monkeypatch):
        monkeypatch.setenv("REDIS_URL", "redis://redis:6379/1")
        monkeypatch.setenv("REDIS_MAX_CONNECTIONS", "100")
        s = RedisSettings()
        assert s.url == "redis://redis:6379/1"
        assert s.max_connections == 100

    def test_invalid_max_connections(self):
        with pytest.raises(Exception):
            RedisSettings(max_connections=0)


class TestDatabaseSettings:
    def test_defaults(self):
        s = DatabaseSettings()
        assert "postgresql+asyncpg" in s.url
        assert s.pool_size == 20

    def test_from_env(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:pass@db:5432/mydb")
        s = DatabaseSettings()
        assert "mydb" in s.url


class TestRiskSettings:
    def test_defaults(self):
        s = RiskSettings()
        assert s.max_position_pct == Decimal("0.10")
        assert s.max_drawdown_pct == Decimal("0.02")
        assert s.kill_switch_enabled is True
        assert s.max_rollback_threshold == Decimal("0.02")

    def test_invalid_percentage_over_one(self):
        with pytest.raises(Exception):
            RiskSettings(max_position_pct=Decimal("1.5"))

    def test_zero_percentage_rejected(self):
        with pytest.raises(Exception):
            RiskSettings(max_position_pct=Decimal("0"))

    def test_one_is_valid(self):
        s = RiskSettings(max_position_pct=Decimal("1.0"))
        assert s.max_position_pct == Decimal("1.0")


class TestMonitoringSettings:
    def test_log_level_normalized_to_upper(self):
        s = MonitoringSettings(log_level="debug")
        assert s.log_level == "DEBUG"

    def test_invalid_log_level(self):
        with pytest.raises(Exception):
            MonitoringSettings(log_level="VERBOSE")

    def test_valid_log_levels(self):
        for level in ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]:
            s = MonitoringSettings(log_level=level)
            assert s.log_level == level

    def test_prometheus_port_bounds(self):
        with pytest.raises(Exception):
            MonitoringSettings(prometheus_port=80)  # below 1024


class TestSettings:
    def test_loads_from_env(self, monkeypatch):
        monkeypatch.setenv("EXECUTION_MODE", "paper")
        s = Settings()
        assert s.engine_env == "test"  # set by conftest
        assert "localhost" in s.redis.url

    def test_prod_requires_binance_key(self, monkeypatch):
        monkeypatch.setenv("ENGINE_ENV", "prod")
        monkeypatch.setenv("BINANCE_API_KEY", "")
        monkeypatch.setenv("OKX_API_KEY", "some_key")
        with pytest.raises(Exception, match="Production requires"):
            Settings()

    def test_prod_requires_okx_key(self, monkeypatch):
        monkeypatch.setenv("ENGINE_ENV", "prod")
        monkeypatch.setenv("BINANCE_API_KEY", "real_key")
        monkeypatch.setenv("OKX_API_KEY", "")
        with pytest.raises(Exception, match="Production requires"):
            Settings()

    def test_prod_ok_with_all_keys(self, monkeypatch):
        monkeypatch.setenv("ENGINE_ENV", "prod")
        monkeypatch.setenv("BINANCE_API_KEY", "real_binance_key")
        monkeypatch.setenv("OKX_API_KEY", "real_okx_key")
        s = Settings()
        assert s.engine_env == "prod"

    def test_dev_env_no_keys_required(self, monkeypatch):
        monkeypatch.setenv("ENGINE_ENV", "dev")
        monkeypatch.setenv("BINANCE_API_KEY", "")
        s = Settings()
        assert s.engine_env == "dev"

    def test_nested_settings_accessible(self):
        s = Settings()
        assert s.redis.max_connections > 0
        assert s.risk.max_drawdown_pct > 0
        assert s.monitoring.prometheus_port > 0


class TestGetSettings:
    def test_returns_settings_instance(self):
        cfg_module._settings = None
        s = get_settings()
        assert isinstance(s, Settings)
        cfg_module._settings = None

    def test_singleton_same_object(self):
        cfg_module._settings = None
        s1 = get_settings()
        s2 = get_settings()
        assert s1 is s2
        cfg_module._settings = None
