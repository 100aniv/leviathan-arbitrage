"""Execution mode and capital tier configuration."""
from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ExecutionMode(StrEnum):
    PAPER = "paper"
    SANDBOX = "sandbox"
    LIVE = "live"


class CapitalTierConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CAPITAL_")

    tier: str = Field(default="alpha")  # alpha|beta|production
    initial_capital: Decimal = Field(default=Decimal("70"))


class ExecutionSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="")

    execution_mode: ExecutionMode = Field(
        default=ExecutionMode.PAPER, alias="EXECUTION_MODE"
    )
    capital: CapitalTierConfig = Field(default_factory=CapitalTierConfig)
