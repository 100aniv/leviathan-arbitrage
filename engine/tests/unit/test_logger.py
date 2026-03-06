"""Tests for engine/src/infra/logger.py"""
from __future__ import annotations

import logging

import pytest


class TestCorrelationId:
    def test_set_and_get(self):
        from src.infra.logger import get_correlation_id, set_correlation_id
        set_correlation_id("test-correlation-123")
        assert get_correlation_id() == "test-correlation-123"

    def test_auto_generate_uuid(self):
        from src.infra.logger import get_correlation_id, set_correlation_id
        cid = set_correlation_id()
        # UUID4 format: 8-4-4-4-12 = 36 chars with dashes
        assert len(cid) == 36
        assert cid.count("-") == 4
        assert get_correlation_id() == cid

    def test_returns_placeholder_when_empty(self):
        from src.infra.logger import _correlation_id, get_correlation_id
        _correlation_id.set("")
        result = get_correlation_id()
        assert result == "no-correlation-id"

    def test_set_explicit_value(self):
        from src.infra.logger import get_correlation_id, set_correlation_id
        cid = set_correlation_id("my-custom-id")
        assert cid == "my-custom-id"
        assert get_correlation_id() == "my-custom-id"


class TestConfigureLogging:
    def test_json_format_no_error(self):
        from src.infra.logger import configure_logging
        configure_logging(log_level="INFO", log_format="json")

    def test_console_format_no_error(self):
        from src.infra.logger import configure_logging
        configure_logging(log_level="DEBUG", log_format="console")

    def test_invalid_log_level_raises(self):
        from src.infra.logger import configure_logging
        with pytest.raises((ValueError, AttributeError)):
            configure_logging(log_level="TRACE")

    def test_all_valid_log_levels(self):
        from src.infra.logger import configure_logging
        for level in ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]:
            configure_logging(log_level=level, log_format="json")

    def test_sets_root_logger_level(self):
        from src.infra.logger import configure_logging
        configure_logging(log_level="WARNING", log_format="json")
        root = logging.getLogger()
        assert root.level == logging.WARNING

    def test_quiets_noisy_libraries(self):
        from src.infra.logger import configure_logging
        configure_logging(log_level="DEBUG", log_format="json")
        assert logging.getLogger("asyncio").level >= logging.WARNING
        assert logging.getLogger("aiohttp").level >= logging.WARNING


class TestGetLogger:
    def test_returns_logger(self):
        from src.infra.logger import configure_logging, get_logger
        configure_logging(log_level="INFO", log_format="json")
        logger = get_logger("test.module")
        assert logger is not None

    def test_logger_has_name(self):
        from src.infra.logger import configure_logging, get_logger
        configure_logging(log_level="INFO", log_format="json")
        logger = get_logger("my.custom.module")
        assert logger is not None

    def test_multiple_loggers_independent(self):
        from src.infra.logger import configure_logging, get_logger
        configure_logging()
        l1 = get_logger("module.a")
        l2 = get_logger("module.b")
        assert l1 is not l2
