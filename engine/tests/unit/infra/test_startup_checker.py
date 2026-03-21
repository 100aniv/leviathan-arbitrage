"""Tests for StartupChecker (US-291-e)."""
import pytest
from unittest.mock import patch, AsyncMock
from src.infra.startup_checker import StartupChecker


class TestStartupChecker:
    @pytest.mark.asyncio
    async def test_python_version_pass(self):
        checker = StartupChecker()
        result = await checker._check_python_version()
        assert result is True  # We're running on 3.12+

    @pytest.mark.asyncio
    async def test_disk_space(self):
        checker = StartupChecker()
        result = await checker._check_disk_space()
        assert result is True  # Dev machines have >1GB

    @pytest.mark.asyncio
    async def test_websocket_check(self):
        checker = StartupChecker()
        result = await checker._check_websocket()
        assert result is True  # websockets is installed

    @pytest.mark.asyncio
    async def test_prometheus_check(self):
        checker = StartupChecker()
        result = await checker._check_prometheus()
        assert result is True  # prometheus_client is installed

    @pytest.mark.asyncio
    async def test_env_vars_missing(self):
        checker = StartupChecker()
        with patch.dict("os.environ", {}, clear=True):
            result = await checker._check_env_vars()
            assert result is False

    @pytest.mark.asyncio
    async def test_env_vars_present(self):
        checker = StartupChecker()
        with patch.dict(
            "os.environ",
            {"DATABASE_URL": "postgresql://localhost/test", "REDIS_URL": "redis://localhost"},
        ):
            result = await checker._check_env_vars()
            assert result is True

    def test_format_checklist(self):
        checker = StartupChecker()
        checker._results = {"Redis": True, "TimescaleDB": False}
        checker._details = {"Redis": "OK", "TimescaleDB": "연결 실패"}
        text = checker.format_checklist()
        assert "✅" in text
        assert "❌" in text
        assert "시작 체크리스트" in text

    def test_all_passed_true(self):
        checker = StartupChecker()
        checker._results = {"A": True, "B": True}
        assert checker.all_passed is True

    def test_all_passed_false(self):
        checker = StartupChecker()
        checker._results = {"A": True, "B": False}
        assert checker.all_passed is False

    def test_all_passed_empty(self):
        checker = StartupChecker()
        assert checker.all_passed is False

    @pytest.mark.asyncio
    async def test_check_api_port_available(self):
        checker = StartupChecker()
        # Port 19999 should be available on most systems
        with patch.dict("os.environ", {"API_PORT": "19999"}):
            result = await checker._check_api_port()
            assert result is True
