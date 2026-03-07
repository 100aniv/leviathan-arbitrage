"""Tests for src/core/execution_config.py — ExecutionMode, CapitalTierConfig, ExecutionSettings."""
from __future__ import annotations

import pytest
from decimal import Decimal
from unittest.mock import patch


class TestExecutionMode:
    def test_paper_value(self):
        from src.core.execution_config import ExecutionMode
        assert ExecutionMode.PAPER == "paper"

    def test_sandbox_value(self):
        from src.core.execution_config import ExecutionMode
        assert ExecutionMode.SANDBOX == "sandbox"

    def test_live_value(self):
        from src.core.execution_config import ExecutionMode
        assert ExecutionMode.LIVE == "live"

    def test_is_str_enum(self):
        from src.core.execution_config import ExecutionMode
        assert isinstance(ExecutionMode.PAPER, str)
        assert isinstance(ExecutionMode.SANDBOX, str)
        assert isinstance(ExecutionMode.LIVE, str)

    def test_all_three_modes_distinct(self):
        from src.core.execution_config import ExecutionMode
        modes = {ExecutionMode.PAPER, ExecutionMode.SANDBOX, ExecutionMode.LIVE}
        assert len(modes) == 3

    def test_comparison_with_string(self):
        from src.core.execution_config import ExecutionMode
        assert ExecutionMode.PAPER == "paper"
        assert ExecutionMode.LIVE != "paper"


class TestCapitalTierConfig:
    def test_default_tier_is_alpha(self):
        from src.core.execution_config import CapitalTierConfig
        cfg = CapitalTierConfig()
        assert cfg.tier == "alpha"

    def test_default_capital_is_70(self):
        from src.core.execution_config import CapitalTierConfig
        cfg = CapitalTierConfig()
        assert cfg.initial_capital == Decimal("70")

    def test_env_override_initial_capital(self):
        from src.core.execution_config import CapitalTierConfig
        with patch.dict("os.environ", {"CAPITAL_INITIAL_CAPITAL": "500"}):
            cfg = CapitalTierConfig()
            assert cfg.initial_capital == Decimal("500")

    def test_env_override_tier(self):
        from src.core.execution_config import CapitalTierConfig
        with patch.dict("os.environ", {"CAPITAL_TIER": "production"}):
            cfg = CapitalTierConfig()
            assert cfg.tier == "production"

    def test_capital_is_decimal_type(self):
        from src.core.execution_config import CapitalTierConfig
        cfg = CapitalTierConfig()
        assert isinstance(cfg.initial_capital, Decimal)


class TestExecutionSettings:
    def test_default_mode_is_paper(self):
        from src.core.execution_config import ExecutionSettings, ExecutionMode
        # Clear any EXECUTION_MODE from environment
        with patch.dict("os.environ", {}, clear=False):
            import os
            if "EXECUTION_MODE" in os.environ:
                del os.environ["EXECUTION_MODE"]
            settings = ExecutionSettings()
            assert settings.execution_mode == ExecutionMode.PAPER

    def test_env_override_to_sandbox(self):
        from src.core.execution_config import ExecutionSettings, ExecutionMode
        with patch.dict("os.environ", {"EXECUTION_MODE": "sandbox"}):
            settings = ExecutionSettings()
            assert settings.execution_mode == ExecutionMode.SANDBOX

    def test_env_override_to_live(self):
        from src.core.execution_config import ExecutionSettings, ExecutionMode
        with patch.dict("os.environ", {"EXECUTION_MODE": "live"}):
            settings = ExecutionSettings()
            assert settings.execution_mode == ExecutionMode.LIVE

    def test_capital_field_is_capital_tier_config(self):
        from src.core.execution_config import ExecutionSettings, CapitalTierConfig
        settings = ExecutionSettings()
        assert isinstance(settings.capital, CapitalTierConfig)

    def test_capital_default_initial_capital(self):
        from src.core.execution_config import ExecutionSettings
        settings = ExecutionSettings()
        assert settings.capital.initial_capital == Decimal("70")
