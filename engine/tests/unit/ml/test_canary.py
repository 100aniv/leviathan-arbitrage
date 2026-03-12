"""US-096: Production Canary tests."""
import pytest

from src.ml.canary import CanaryStage, MLCanary, CanaryMetrics


def test_canary_creation():
    canary = MLCanary()
    assert canary.stage == CanaryStage.DISABLED
    assert canary.ml_traffic_pct == 0.0
    assert not canary.is_active


def test_canary_start():
    canary = MLCanary()
    canary.start()
    assert canary.stage == CanaryStage.CANARY_10
    assert canary.ml_traffic_pct == 0.1
    assert canary.is_active


def test_should_use_ml_disabled():
    canary = MLCanary()
    assert not canary.should_use_ml()


def test_should_use_ml_full():
    canary = MLCanary(auto_promote=False)
    canary._transition(CanaryStage.FULL_ML)
    assert canary.should_use_ml()


def test_should_use_ml_canary_10():
    """10% traffic → roughly 10% should use ML (statistical)."""
    canary = MLCanary(auto_promote=False)
    canary.start()
    ml_count = sum(1 for _ in range(1000) if canary.should_use_ml())
    assert 50 < ml_count < 200  # ~100 expected, wide tolerance


def test_record_signal_ml():
    canary = MLCanary(auto_promote=False)
    canary.start()
    canary.record_signal(used_ml=True, pnl=1.5)
    assert canary.metrics.ml_signals == 1
    assert canary.metrics.ml_pnl == 1.5
    assert canary.metrics.ml_wins == 1


def test_record_signal_baseline():
    canary = MLCanary(auto_promote=False)
    canary.start()
    canary.record_signal(used_ml=False, pnl=-0.5)
    assert canary.metrics.baseline_signals == 1
    assert canary.metrics.baseline_pnl == -0.5
    assert canary.metrics.baseline_wins == 0


def test_metrics_properties():
    m = CanaryMetrics(stage=CanaryStage.CANARY_10)
    m.ml_signals = 10
    m.ml_wins = 7
    m.ml_pnl = 5.0
    m.baseline_signals = 90
    m.baseline_wins = 50
    m.baseline_pnl = 3.0
    assert m.ml_win_rate == 0.7
    assert m.baseline_win_rate == pytest.approx(50/90, abs=0.01)
    assert m.pnl_delta == 2.0
    assert m.ml_improves is True


def test_auto_promote_10_to_50():
    """Auto promote from 10% → 50% when criteria met."""
    canary = MLCanary(min_signals_to_promote=5, min_pnl_delta=0.0, auto_promote=True)
    canary.start()
    assert canary.stage == CanaryStage.CANARY_10

    # Record enough positive ML signals
    for _ in range(6):
        canary.record_signal(used_ml=True, pnl=1.0)
    assert canary.stage == CanaryStage.CANARY_50


def test_auto_promote_50_to_full():
    """Auto promote from 50% → 100%."""
    canary = MLCanary(min_signals_to_promote=3, min_pnl_delta=0.0, auto_promote=True)
    canary._transition(CanaryStage.CANARY_50)

    for _ in range(4):
        canary.record_signal(used_ml=True, pnl=0.5)
    assert canary.stage == CanaryStage.FULL_ML


def test_no_promote_negative_delta():
    """Don't promote if ML PnL <= baseline."""
    canary = MLCanary(min_signals_to_promote=3, min_pnl_delta=0.01, auto_promote=True)
    canary.start()

    for _ in range(5):
        canary.record_signal(used_ml=True, pnl=-1.0)
    assert canary.stage == CanaryStage.CANARY_10  # not promoted


def test_manual_promote():
    canary = MLCanary(min_signals_to_promote=2, auto_promote=False)
    canary.start()
    canary.record_signal(used_ml=True, pnl=1.0)
    canary.record_signal(used_ml=True, pnl=2.0)

    assert canary.promote()
    assert canary.stage == CanaryStage.CANARY_50


def test_promote_fails_insufficient_signals():
    canary = MLCanary(min_signals_to_promote=100, auto_promote=False)
    canary.start()
    canary.record_signal(used_ml=True, pnl=1.0)
    assert not canary.promote()


def test_promote_at_full_ml():
    canary = MLCanary(auto_promote=False)
    canary._transition(CanaryStage.FULL_ML)
    assert not canary.promote()


def test_rollback():
    canary = MLCanary(auto_promote=False)
    canary.start()
    canary.rollback()
    assert canary.stage == CanaryStage.ROLLBACK
    assert not canary.is_active
    assert canary.ml_traffic_pct == 0.0


def test_reset_metrics():
    canary = MLCanary(auto_promote=False)
    canary.start()
    canary.record_signal(used_ml=True, pnl=5.0)
    assert canary.metrics.ml_signals == 1

    canary.reset_metrics()
    assert canary.metrics.ml_signals == 0
    assert canary.metrics.ml_pnl == 0.0


def test_status():
    canary = MLCanary(auto_promote=False)
    canary.start()
    canary.record_signal(used_ml=True, pnl=1.0)
    canary.record_signal(used_ml=False, pnl=0.5)

    status = canary.status()
    assert status["stage"] == "canary_10"
    assert status["ml_traffic_pct"] == 0.1
    assert status["ml_signals"] == 1
    assert status["baseline_signals"] == 1
    assert isinstance(status["stage_history"], list)
    assert len(status["stage_history"]) >= 1


def test_stage_history():
    canary = MLCanary(min_signals_to_promote=2, auto_promote=False)
    canary.start()
    canary.record_signal(used_ml=True, pnl=1.0)
    canary.record_signal(used_ml=True, pnl=1.0)
    canary.promote()

    history = canary.status()["stage_history"]
    assert len(history) == 2  # start (canary_10) + promote (canary_50)
    assert history[0][1] == "canary_10"
    assert history[1][1] == "canary_50"


def test_full_lifecycle():
    """Complete lifecycle: disabled → 10% → 50% → 100%."""
    canary = MLCanary(min_signals_to_promote=3, min_pnl_delta=0.0, auto_promote=True)
    assert canary.stage == CanaryStage.DISABLED

    canary.start()
    assert canary.stage == CanaryStage.CANARY_10

    for _ in range(4):
        canary.record_signal(used_ml=True, pnl=0.5)
    assert canary.stage == CanaryStage.CANARY_50

    for _ in range(4):
        canary.record_signal(used_ml=True, pnl=0.5)
    assert canary.stage == CanaryStage.FULL_ML
    assert canary.should_use_ml()
