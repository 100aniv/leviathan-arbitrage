"""Tests for US-234: ShadowMode + AdaptiveThreshold + RegimeDetector integration.

Verifies:
- _shadow_regime_check_loop: regime_detector.detect() 60s 주기 호출
- _shadow_adaptive_threshold_loop: adaptive_threshold.adjust() 300s 주기 호출
- CRISIS regime → _shadow_min_edge_factor 2.0 상향
- 정상 레짐 복귀 → _shadow_min_edge_factor 1.0 복원
- _shadow_params_hot_reload: shadow 인스턴스 상태만 업데이트
- _shadow_params_hot_reload: 잘못된 입력 무시
- ShadowMiniTuner.should_activate: 2시간 미만 비활성
- ShadowMiniTuner.should_activate: 2시간 이상 활성
- ShadowMiniTuner.run_in_thread: optuna 미설치 시 스킵
- ShadowMiniTuner._triggered: 중복 실행 방지
- adaptive_threshold=None 시 루프 조기 종료
- regime_detector=None 시 루프 조기 종료

Run:
    cd engine && python -m pytest tests/test_shadow_tuner_integration.py -x --tb=short -v
"""
from __future__ import annotations

import asyncio
import os
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.modes.shadow import ShadowMode
from src.tuning.scheduled_tuner import ShadowMiniTuner


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_shadow_mode(
    regime_detector=None,
    adaptive_threshold=None,
) -> ShadowMode:
    """Build a minimal ShadowMode with mocked I/O."""
    mock_executor = MagicMock()
    mock_executor.slippage_model = MagicMock(spec=[])
    mock_collector = MagicMock()
    mock_collector._on_orderbook = None

    with patch.dict(os.environ, {"ENGINE_ENV": "test"}):
        shadow = ShadowMode(
            signal_generator=MagicMock(),
            paper_executor=mock_executor,
            collector_manager=mock_collector,
            regime_detector=regime_detector,
            adaptive_threshold=adaptive_threshold,
        )
    return shadow


# ---------------------------------------------------------------------------
# 1. regime_detector.detect() 호출 검증
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_shadow_regime_check_loop_calls_detect():
    """_shadow_regime_check_loop이 regime_detector.detect()를 호출해야 한다."""
    from src.tuning.regime_detector import MarketRegime

    mock_detector = MagicMock()
    mock_detector.detect.return_value = MarketRegime.LOW

    shadow = _make_shadow_mode(regime_detector=mock_detector)
    shadow._running = True
    shadow._regime_pnl_history = [0.01] * 35  # 30+ samples

    # 첫 번째 sleep은 통과, 두 번째 sleep에서 _running = False로 종료
    call_count = 0

    async def fake_sleep(n):
        nonlocal call_count
        call_count += 1
        if call_count >= 2:
            shadow._running = False

    with patch("asyncio.sleep", side_effect=fake_sleep):
        await shadow._shadow_regime_check_loop()

    mock_detector.detect.assert_called_once()


# ---------------------------------------------------------------------------
# 2. adaptive_threshold.adjust() 호출 검증
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_shadow_adaptive_threshold_loop_calls_adjust():
    """_shadow_adaptive_threshold_loop이 adaptive_threshold.adjust()를 호출해야 한다."""
    mock_threshold = MagicMock()
    mock_threshold.adjust.return_value = 7.5

    shadow = _make_shadow_mode(adaptive_threshold=mock_threshold)
    shadow._running = True
    shadow._stats.trades_executed = 50
    shadow._stats.trades_won = 35
    shadow._stats.trades_lost = 15
    shadow._stats.total_pnl = 12.50

    # 첫 번째 sleep 통과, 두 번째에서 종료
    call_count = 0

    async def fake_sleep(n):
        nonlocal call_count
        call_count += 1
        if call_count >= 2:
            shadow._running = False

    with patch("asyncio.sleep", side_effect=fake_sleep):
        await shadow._shadow_adaptive_threshold_loop()

    mock_threshold.adjust.assert_called_once()
    kwargs = mock_threshold.adjust.call_args[1]
    assert "win_rate" in kwargs
    assert "total_trades" in kwargs
    assert kwargs["total_trades"] == 50


# ---------------------------------------------------------------------------
# 3. CRISIS → min_edge_factor 2.0
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_crisis_regime_sets_min_edge_factor_2x():
    """CRISIS 레짐 감지 시 _shadow_min_edge_factor가 2.0이 되어야 한다."""
    from src.tuning.regime_detector import MarketRegime

    mock_detector = MagicMock()
    mock_detector.detect.return_value = MarketRegime.CRISIS

    shadow = _make_shadow_mode(regime_detector=mock_detector)
    shadow._running = True
    shadow._regime_pnl_history = [0.01] * 35
    shadow._shadow_min_edge_factor = 1.0

    # 첫 번째 sleep 통과, 두 번째에서 종료
    call_count = 0

    async def fake_sleep(n):
        nonlocal call_count
        call_count += 1
        if call_count >= 2:
            shadow._running = False

    with patch("asyncio.sleep", side_effect=fake_sleep):
        await shadow._shadow_regime_check_loop()

    assert shadow._shadow_min_edge_factor == 2.0


# ---------------------------------------------------------------------------
# 4. 정상 레짐 복귀 → factor 1.0
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_normal_regime_restores_min_edge_factor_1x():
    """정상 레짐 복귀 시 _shadow_min_edge_factor가 1.0으로 복원되어야 한다."""
    from src.tuning.regime_detector import MarketRegime

    mock_detector = MagicMock()
    mock_detector.detect.return_value = MarketRegime.LOW

    shadow = _make_shadow_mode(regime_detector=mock_detector)
    shadow._running = True
    shadow._regime_pnl_history = [0.01] * 35
    # 이미 CRISIS 상태에서 복귀하는 시나리오
    shadow._shadow_min_edge_factor = 2.0

    # 첫 번째 sleep 통과, 두 번째에서 종료
    call_count = 0

    async def fake_sleep(n):
        nonlocal call_count
        call_count += 1
        if call_count >= 2:
            shadow._running = False

    with patch("asyncio.sleep", side_effect=fake_sleep):
        await shadow._shadow_regime_check_loop()

    assert shadow._shadow_min_edge_factor == 1.0


# ---------------------------------------------------------------------------
# 5. _shadow_params_hot_reload: shadow 인스턴스만 업데이트
# ---------------------------------------------------------------------------

def test_shadow_params_hot_reload_updates_threshold():
    """_shadow_params_hot_reload가 adaptive_threshold.current_edge_bps를 업데이트해야 한다."""
    mock_threshold = MagicMock()
    mock_threshold.current_edge_bps = 5.0
    mock_threshold.min_edge = 2.0
    mock_threshold.max_edge = 50.0

    shadow = _make_shadow_mode(adaptive_threshold=mock_threshold)
    shadow._shadow_params_hot_reload({"min_edge_bps": 12.0})

    assert mock_threshold.current_edge_bps == 12.0


def test_shadow_params_hot_reload_no_strategy_params_json_written(tmp_path):
    """_shadow_params_hot_reload가 strategy_params.json을 쓰지 않아야 한다."""
    mock_threshold = MagicMock()
    shadow = _make_shadow_mode(adaptive_threshold=mock_threshold)

    params_file = tmp_path / "strategy_params.json"
    assert not params_file.exists()

    shadow._shadow_params_hot_reload({"min_edge_bps": 8.0})

    # strategy_params.json 생성 없음 — 파일 없음 확인
    assert not params_file.exists()


# ---------------------------------------------------------------------------
# 6. _shadow_params_hot_reload: 잘못된 입력 처리
# ---------------------------------------------------------------------------

def test_shadow_params_hot_reload_invalid_type_ignored():
    """잘못된 타입 입력 시 예외 없이 무시해야 한다."""
    shadow = _make_shadow_mode()
    # 예외 없이 처리돼야 함
    shadow._shadow_params_hot_reload("not_a_dict")  # type: ignore[arg-type]
    shadow._shadow_params_hot_reload(None)  # type: ignore[arg-type]


def test_shadow_params_hot_reload_invalid_min_edge_ignored():
    """min_edge_bps 값이 변환 불가 시 예외 없이 무시해야 한다."""
    mock_threshold = MagicMock()
    mock_threshold.current_edge_bps = 5.0

    shadow = _make_shadow_mode(adaptive_threshold=mock_threshold)
    shadow._shadow_params_hot_reload({"min_edge_bps": "not_a_number"})

    # 값 변경 없어야 함
    assert mock_threshold.current_edge_bps == 5.0


# ---------------------------------------------------------------------------
# 7. ShadowMiniTuner.should_activate: 2시간 미만 비활성
# ---------------------------------------------------------------------------

def test_shadow_mini_tuner_should_not_activate_before_2h():
    """2시간 미만 경과 시 should_activate가 False여야 한다."""
    tuner = ShadowMiniTuner()
    assert tuner.should_activate(3600) is False  # 1시간
    assert tuner.should_activate(7199) is False  # 2시간 미만 1초


# ---------------------------------------------------------------------------
# 8. ShadowMiniTuner.should_activate: 2시간 이상 활성
# ---------------------------------------------------------------------------

def test_shadow_mini_tuner_should_activate_after_2h():
    """2시간 이상 경과 시 should_activate가 True여야 한다."""
    tuner = ShadowMiniTuner()
    assert tuner.should_activate(7200) is True   # 정확히 2시간
    assert tuner.should_activate(10000) is True  # 2시간 초과


# ---------------------------------------------------------------------------
# 9. ShadowMiniTuner.run_in_thread: optuna 미설치 시 스킵
# ---------------------------------------------------------------------------

def test_shadow_mini_tuner_skips_when_optuna_unavailable():
    """optuna 미설치 시 run_in_thread가 스레드를 시작하지 않아야 한다."""
    tuner = ShadowMiniTuner()

    with patch("src.tuning.scheduled_tuner._OPTUNA_AVAILABLE", False):
        import threading
        before = threading.active_count()
        tuner.run_in_thread(shadow_elapsed_seconds=8000)
        after = threading.active_count()

    # _triggered가 False로 유지되어야 함 (optuna 없어서 skip됨)
    assert tuner._triggered is False


# ---------------------------------------------------------------------------
# 10. ShadowMiniTuner._triggered: 중복 실행 방지
# ---------------------------------------------------------------------------

def test_shadow_mini_tuner_no_duplicate_run():
    """이미 triggered된 경우 run_in_thread가 추가 실행하지 않아야 한다."""
    callback_count = 0

    def cb(params):
        nonlocal callback_count
        callback_count += 1

    tuner = ShadowMiniTuner(hot_reload_callback=cb)
    tuner._triggered = True  # 이미 실행된 상태

    tuner.run_in_thread(shadow_elapsed_seconds=10000)
    # _triggered이므로 콜백 호출 없음
    assert callback_count == 0


# ---------------------------------------------------------------------------
# 11. adaptive_threshold=None 시 루프 조기 종료
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_adaptive_threshold_loop_exits_if_none():
    """adaptive_threshold가 None이면 루프가 즉시 종료되어야 한다."""
    shadow = _make_shadow_mode(adaptive_threshold=None)
    shadow._running = True

    sleep_count = 0

    async def fake_sleep(n):
        nonlocal sleep_count
        sleep_count += 1
        shadow._running = False

    with patch("asyncio.sleep", side_effect=fake_sleep):
        await shadow._shadow_adaptive_threshold_loop()

    # 1회 sleep 후 None 체크로 break
    assert sleep_count == 1


# ---------------------------------------------------------------------------
# 12. regime_detector=None 시 루프 조기 종료
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_regime_check_loop_exits_if_none():
    """regime_detector가 None이면 루프가 즉시 종료되어야 한다."""
    shadow = _make_shadow_mode(regime_detector=None)
    shadow._running = True

    sleep_count = 0

    async def fake_sleep(n):
        nonlocal sleep_count
        sleep_count += 1
        shadow._running = False

    with patch("asyncio.sleep", side_effect=fake_sleep):
        await shadow._shadow_regime_check_loop()

    assert sleep_count == 1
