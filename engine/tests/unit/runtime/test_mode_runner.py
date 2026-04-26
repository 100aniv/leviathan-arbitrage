"""Phase 5.4 ModeRunner ABC + factory 검증."""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from src.runtime.mode_runner import (
    BacktestRunner,
    LiveRunner,
    ModeRunner,
    PaperRunner,
    create_mode_runner,
)


class TestModeRunnerABC:
    def test_abc_cannot_instantiate(self) -> None:
        with pytest.raises(TypeError, match="abstract"):
            ModeRunner()  # type: ignore

    def test_paper_runner_extends_abc(self) -> None:
        engine = MagicMock()
        runner = PaperRunner(engine)
        assert isinstance(runner, ModeRunner)
        assert runner.name == "paper"

    def test_live_runner_name(self) -> None:
        assert LiveRunner(MagicMock()).name == "live"

    def test_backtest_runner_name(self) -> None:
        assert BacktestRunner(MagicMock()).name == "backtest"


class TestCreateModeRunner:
    def test_paper(self) -> None:
        runner = create_mode_runner("paper", MagicMock())
        assert isinstance(runner, PaperRunner)

    def test_shadow_alias_returns_paper(self) -> None:
        runner = create_mode_runner("shadow", MagicMock())
        assert isinstance(runner, PaperRunner)

    def test_live(self) -> None:
        runner = create_mode_runner("live", MagicMock())
        assert isinstance(runner, LiveRunner)

    def test_backtest(self) -> None:
        runner = create_mode_runner("backtest", MagicMock())
        assert isinstance(runner, BacktestRunner)

    def test_uppercase_normalized(self) -> None:
        runner = create_mode_runner("PAPER", MagicMock())
        assert isinstance(runner, PaperRunner)

    def test_unknown_mode_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown mode"):
            create_mode_runner("invalid_mode", MagicMock())
