"""Unit tests for US-067: StrategyValidationOrchestrator + ShadowMode new methods.

Covers:
- StrategyResult / StrategyValidationReport dataclass defaults
- StrategyValidationOrchestrator initialisation + env var overrides
- Cross-exchange shadow_arb_v1 isolation logic
- Profitability classification (via _validate_single_strategy)
- Activation config file writing
- ShadowMode.reset_stats(), .set_disabled_strategies(), .get_strategy_report()
- Telegram report sending via mock
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.modes.shadow import ShadowMode, ShadowStats, StrategyStats, VirtualBalanceTracker
from src.modes.strategy_validation import (
    StrategyResult,
    StrategyValidationOrchestrator,
    StrategyValidationReport,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_signal_gen() -> MagicMock:
    sg = MagicMock()
    sg.on_orderbook_update = AsyncMock(return_value=None)
    return sg


def make_collector() -> MagicMock:
    cm = MagicMock()
    cm.start = AsyncMock()
    cm.stop = AsyncMock()
    return cm


def make_real_shadow() -> ShadowMode:
    """Real ShadowMode with mocked I/O deps — for testing new methods."""
    return ShadowMode(
        signal_generator=make_signal_gen(),
        collector_manager=make_collector(),
        symbols=["BTC/USDT"],
    )


def make_mock_shadow() -> MagicMock:
    """Fully-mocked ShadowMode for orchestrator tests."""
    shadow = MagicMock(spec=ShadowMode)
    shadow.reset_stats = MagicMock()
    shadow.set_disabled_strategies = MagicMock()
    shadow.get_strategy_report = MagicMock(return_value={})
    shadow.start = AsyncMock()
    shadow.stop = AsyncMock()
    return shadow


def make_orch(shadow: MagicMock | None = None, **env_overrides: str) -> StrategyValidationOrchestrator:
    """Create orchestrator with optional env overrides applied via monkeypatch."""
    if shadow is None:
        shadow = make_mock_shadow()
    for k, v in env_overrides.items():
        os.environ[k] = v
    return StrategyValidationOrchestrator(shadow_mode=shadow)


# ---------------------------------------------------------------------------
# StrategyResult / StrategyValidationReport defaults (2)
# ---------------------------------------------------------------------------


class TestDataclassDefaults:
    def test_strategy_result_defaults(self) -> None:
        """StrategyResult 기본값 확인."""
        result = StrategyResult(strategy_id="cross_exchange_v1")
        assert result.strategy_id == "cross_exchange_v1"
        assert result.trades == 0
        assert result.pnl == 0.0
        assert result.win_rate == 0.0
        assert result.profitable is False
        assert result.reason == ""
        assert result.elapsed_s == 0.0

    def test_strategy_validation_report_defaults(self) -> None:
        """StrategyValidationReport 기본값 확인."""
        report = StrategyValidationReport()
        assert isinstance(report.strategies, dict)
        assert report.strategies == {}
        assert report.profitable == []
        assert report.unprofitable == []
        assert report.insufficient_data == []
        assert report.combined_result is None  # None by default


# ---------------------------------------------------------------------------
# Orchestrator init + env var tests (4)
# ---------------------------------------------------------------------------


class TestOrchestratorInit:
    def test_orchestrator_default_config(self) -> None:
        """기본 env var 값 확인 (600s, 5 min_trades, 30s hydration)."""
        shadow = make_mock_shadow()
        for key in [
            "STRATEGY_VALIDATION_DURATION_S",
            "STRATEGY_VALIDATION_MIN_TRADES",
            "STRATEGY_VALIDATION_HYDRATION_S",
            "STRATEGY_ACTIVATION_PATH",
        ]:
            os.environ.pop(key, None)

        orch = StrategyValidationOrchestrator(shadow_mode=shadow)

        assert orch._duration_s == 600
        assert orch._min_trades == 5
        assert orch._hydration_s == 30
        assert orch._output_path == Path("config/strategy_activation.json")

    def test_orchestrator_custom_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """STRATEGY_VALIDATION_DURATION_S=120 등 커스텀 값."""
        monkeypatch.setenv("STRATEGY_VALIDATION_DURATION_S", "120")
        monkeypatch.setenv("STRATEGY_VALIDATION_MIN_TRADES", "10")
        shadow = make_mock_shadow()

        orch = StrategyValidationOrchestrator(shadow_mode=shadow)

        assert orch._duration_s == 120
        assert orch._min_trades == 10

    def test_orchestrator_output_path(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """STRATEGY_ACTIVATION_PATH 커스텀 경로."""
        custom_path = str(tmp_path / "custom_output.json")
        monkeypatch.setenv("STRATEGY_ACTIVATION_PATH", custom_path)
        shadow = make_mock_shadow()

        orch = StrategyValidationOrchestrator(shadow_mode=shadow)

        assert orch._output_path == Path(custom_path)

    def test_orchestrator_min_trades_threshold(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """최소 거래 수 설정 확인."""
        monkeypatch.setenv("STRATEGY_VALIDATION_MIN_TRADES", "20")
        shadow = make_mock_shadow()

        orch = StrategyValidationOrchestrator(shadow_mode=shadow)

        assert orch._min_trades == 20


# ---------------------------------------------------------------------------
# Strategy isolation tests (2)
# ---------------------------------------------------------------------------


class TestStrategyIsolation:
    @pytest.mark.asyncio
    async def test_cross_exchange_keeps_shadow_arb(self) -> None:
        """cross_exchange_v1 격리 시 shadow_arb_v1 미차단 확인."""
        shadow = make_mock_shadow()
        disabled_sets: list[set[str]] = []
        shadow.set_disabled_strategies = MagicMock(
            side_effect=lambda s: disabled_sets.append(set(s))
        )
        shadow.get_strategy_report = MagicMock(
            return_value={
                "shadow_arb_v1": {
                    "trades": 10,
                    "wins": 7,
                    "losses": 3,
                    "pnl": 5.0,
                    "win_rate": 0.7,
                }
            }
        )

        with patch("asyncio.sleep", new_callable=AsyncMock):
            orch = StrategyValidationOrchestrator(shadow_mode=shadow)
            orch._duration_s = 0
            await orch._validate_single_strategy("cross_exchange_v1")

        assert disabled_sets, "set_disabled_strategies must be called"
        isolation_call = disabled_sets[0]
        assert "shadow_arb_v1" not in isolation_call, (
            f"shadow_arb_v1 should NOT be disabled when validating cross_exchange_v1, "
            f"got disabled set: {isolation_call}"
        )

    @pytest.mark.asyncio
    async def test_other_strategy_blocks_shadow_arb(self) -> None:
        """다른 전략 격리 시 shadow_arb_v1 차단 확인."""
        shadow = make_mock_shadow()
        disabled_sets: list[set[str]] = []
        shadow.set_disabled_strategies = MagicMock(
            side_effect=lambda s: disabled_sets.append(set(s))
        )
        shadow.get_strategy_report = MagicMock(
            return_value={
                "spot_futures_basis": {
                    "trades": 10,
                    "wins": 4,
                    "losses": 6,
                    "pnl": -2.0,
                    "win_rate": 0.4,
                }
            }
        )

        with patch("asyncio.sleep", new_callable=AsyncMock):
            orch = StrategyValidationOrchestrator(shadow_mode=shadow)
            orch._duration_s = 0
            await orch._validate_single_strategy("spot_futures_v1")

        assert disabled_sets, "set_disabled_strategies must be called"
        isolation_call = disabled_sets[0]
        assert "shadow_arb_v1" in isolation_call, (
            f"shadow_arb_v1 SHOULD be disabled when validating spot_futures_v1, "
            f"got disabled set: {isolation_call}"
        )


# ---------------------------------------------------------------------------
# Profitability classification tests (3)
# — Classification is inline in _validate_single_strategy; tested via that method
# ---------------------------------------------------------------------------


class TestProfitabilityClassification:
    @pytest.mark.asyncio
    async def test_classify_profitable(self) -> None:
        """PnL > 0 + trades >= min → profitable."""
        shadow = make_mock_shadow()
        shadow.get_strategy_report = MagicMock(
            return_value={
                "shadow_arb_v1": {"trades": 10, "wins": 7, "losses": 3, "pnl": 5.0, "win_rate": 0.7}
            }
        )

        with patch("asyncio.sleep", new_callable=AsyncMock):
            orch = StrategyValidationOrchestrator(shadow_mode=shadow)
            orch._duration_s = 0
            result = await orch._validate_single_strategy("cross_exchange_v1")

        assert result.profitable is True
        assert "profitable" in result.reason

    @pytest.mark.asyncio
    async def test_classify_unprofitable(self) -> None:
        """PnL <= 0 + trades >= min → unprofitable."""
        shadow = make_mock_shadow()
        # _validate_single_strategy looks up report.get(strategy_id, {}),
        # so the key must match the registration ID "spot_futures_v1"
        shadow.get_strategy_report = MagicMock(
            return_value={
                "spot_futures_basis": {"trades": 10, "wins": 3, "losses": 7, "pnl": -3.0, "win_rate": 0.3}
            }
        )

        with patch("asyncio.sleep", new_callable=AsyncMock):
            orch = StrategyValidationOrchestrator(shadow_mode=shadow)
            orch._duration_s = 0
            result = await orch._validate_single_strategy("spot_futures_v1")

        assert result.profitable is False
        assert "unprofitable" in result.reason

    @pytest.mark.asyncio
    async def test_classify_insufficient_data(self) -> None:
        """trades < min → insufficient_data."""
        shadow = make_mock_shadow()
        # 2 trades < default min_trades=5
        shadow.get_strategy_report = MagicMock(
            return_value={
                "triangular": {"trades": 2, "wins": 2, "losses": 0, "pnl": 1.0, "win_rate": 1.0}
            }
        )

        with patch("asyncio.sleep", new_callable=AsyncMock):
            orch = StrategyValidationOrchestrator(shadow_mode=shadow)
            orch._duration_s = 0
            result = await orch._validate_single_strategy("triangular_v1")

        assert result.profitable is False
        assert "insufficient" in result.reason


# ---------------------------------------------------------------------------
# Output file tests (2)
# — _write_activation_config() is synchronous and uses self._report internally
# ---------------------------------------------------------------------------


class TestOutputFile:
    def test_write_activation_config(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """config/strategy_activation.json 정상 기록."""
        output_path = tmp_path / "strategy_activation.json"
        monkeypatch.setenv("STRATEGY_ACTIVATION_PATH", str(output_path))
        shadow = make_mock_shadow()
        orch = StrategyValidationOrchestrator(shadow_mode=shadow)

        # Populate internal report directly
        orch._report.strategies["cross_exchange_v1"] = StrategyResult(
            strategy_id="cross_exchange_v1",
            trades=86,
            pnl=14.21,
            win_rate=0.556,
            profitable=True,
            reason="profitable (PnL=$+14.2100, WR=55.6%)",
            elapsed_s=600.0,
        )
        orch._report.profitable = ["cross_exchange_v1"]
        orch._report.unprofitable = []
        orch._report.combined_result = {"total_trades": 86, "total_pnl": 14.21}

        orch._write_activation_config()

        assert output_path.exists()
        data = json.loads(output_path.read_text())
        assert "active_strategies" in data
        assert "cross_exchange_v1" in data["active_strategies"]
        assert "_meta" in data

    def test_write_config_no_profitable(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """수익 전략 0개 시 결과 파일 형식."""
        output_path = tmp_path / "strategy_activation.json"
        monkeypatch.setenv("STRATEGY_ACTIVATION_PATH", str(output_path))
        shadow = make_mock_shadow()
        orch = StrategyValidationOrchestrator(shadow_mode=shadow)

        orch._report.strategies = {}
        orch._report.profitable = []
        orch._report.unprofitable = ["spot_futures_v1", "triangular_v1"]
        orch._report.combined_result = {"error": "no_profitable_strategies"}

        orch._write_activation_config()

        assert output_path.exists()
        data = json.loads(output_path.read_text())
        assert "active_strategies" in data
        assert data["active_strategies"] == []
        assert "disabled_strategies" in data


# ---------------------------------------------------------------------------
# ShadowMode new methods tests (3)
# ---------------------------------------------------------------------------


class TestShadowModeNewMethods:
    def test_reset_stats(self) -> None:
        """reset_stats() 호출 후 stats 초기화 + VirtualBalanceTracker.reset() 호출 확인."""
        shadow = make_real_shadow()

        # Pollute stats with non-zero values
        shadow._stats.signals_detected = 100
        shadow._stats.trades_executed = 50
        shadow._stats.total_pnl = 999.0
        shadow._stats.by_strategy["dummy"] = StrategyStats(wins=5, losses=2)

        with patch.object(shadow._balance_tracker, "reset") as mock_reset:
            shadow.reset_stats()
            mock_reset.assert_called_once()

        assert shadow._stats.signals_detected == 0
        assert shadow._stats.trades_executed == 0
        assert shadow._stats.total_pnl == 0.0
        assert shadow._stats.by_strategy == {}

    def test_set_disabled_strategies(self) -> None:
        """set_disabled_strategies() 동작 확인."""
        shadow = make_real_shadow()

        new_disabled = {"spot_futures_v1", "triangular_v1", "shadow_arb_v1"}
        shadow.set_disabled_strategies(new_disabled)

        assert shadow._disabled_strategies == new_disabled

    def test_get_strategy_report(self) -> None:
        """get_strategy_report() 반환 형식 확인.

        Note: implementation accesses stats.total_pnl; StrategyStats currently
        defines 'pnl'. We use SimpleNamespace to provide the attributes the
        implementation expects, avoiding float(MagicMock()) = 1.0 side-effect.
        """
        from types import SimpleNamespace

        shadow = make_real_shadow()

        mock_stats = SimpleNamespace(wins=7, losses=3, trades=10, pnl=5.0, pnl_history=[1.0, -0.5, 2.0, 0.5, 1.0, -0.2, 0.8, 0.3, 0.6, 0.5])
        shadow._stats.by_strategy["cross_exchange_v1"] = mock_stats

        report = shadow.get_strategy_report()

        assert isinstance(report, dict)
        assert "cross_exchange_v1" in report
        entry = report["cross_exchange_v1"]
        assert entry["trades"] == 10
        assert entry["wins"] == 7
        assert entry["losses"] == 3
        assert entry["pnl"] == pytest.approx(5.0)
        assert entry["win_rate"] == pytest.approx(0.7)


# ---------------------------------------------------------------------------
# Telegram report tests (1)
# — _send_telegram_report() is async, uses self._telegram and self._report
# ---------------------------------------------------------------------------


class TestTelegramReport:
    @pytest.mark.asyncio
    async def test_send_telegram_report(self) -> None:
        """mock telegram sender로 report 전송 확인."""
        shadow = make_mock_shadow()
        telegram = MagicMock()
        telegram.send_alert = AsyncMock()

        # telegram_sender kwarg per actual __init__ signature
        orch = StrategyValidationOrchestrator(shadow_mode=shadow, telegram_sender=telegram)

        # Populate report so Telegram message has content
        orch._report.strategies["cross_exchange_v1"] = StrategyResult(
            strategy_id="cross_exchange_v1",
            trades=86,
            pnl=14.21,
            win_rate=0.556,
            profitable=True,
            reason="profitable",
            elapsed_s=600.0,
        )
        orch._report.profitable = ["cross_exchange_v1"]
        orch._report.unprofitable = ["spot_futures_v1"]
        orch._report.strategies["spot_futures_v1"] = StrategyResult(
            strategy_id="spot_futures_v1",
            trades=10,
            pnl=-3.0,
            win_rate=0.3,
            profitable=False,
            reason="unprofitable",
            elapsed_s=600.0,
        )

        await orch._send_telegram_report()

        telegram.send_alert.assert_called_once()
        args, _ = telegram.send_alert.call_args
        message = args[0]
        assert "cross_exchange_v1" in message
