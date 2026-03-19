"""TDD tests for Phase S15 critical fixes — new behavior (Yujin/Gaeul 구현 대상).

Covers:
  US-251: HMMTrainer startup-train components (load_model → should_retrain)
  US-252: XGBTrainer startup-train components
  US-255: PerStrategyAdaptiveThreshold per-strategy independence
  US-247: CostCalculator.estimate_cost — intra-exchange network_cost=0
"""
from __future__ import annotations

import time
from decimal import Decimal
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# US-251: HMMTrainer startup-train support
# ---------------------------------------------------------------------------


def test_us251_hmm_load_model_returns_false_when_no_file(tmp_path):
    """HMMTrainer.load_model() returns False when no cached model file exists.

    The fixed _hmm_training_loop should call load_model() at startup;
    False → schedule immediate train before the first 7-day sleep.
    """
    from src.ml.hmm_trainer import HMMTrainer

    trainer = HMMTrainer(cache_dir=str(tmp_path / "hmm"))
    assert not trainer.load_model(), "load_model() must return False when no file on disk"


def test_us251_hmm_should_retrain_true_on_fresh_startup():
    """HMMTrainer.should_retrain() returns True when never trained (last_trained_at is None).

    Used by the fixed loop: should_retrain() True → train immediately, not after 7-day sleep.
    """
    from src.ml.hmm_trainer import HMMTrainer

    trainer = HMMTrainer()
    assert trainer.last_trained_at is None
    assert trainer.should_retrain(), "should_retrain() must be True when no model has been trained"


# ---------------------------------------------------------------------------
# US-252: XGBTrainer startup-train support
# ---------------------------------------------------------------------------


def test_us252_xgb_load_model_returns_false_when_no_file(tmp_path):
    """XGBTrainer.load_model() returns False when no cached model file exists.

    The fixed _xgb_training_loop should call load_model() at startup;
    False → train before the first 24-hour sleep.
    """
    from src.ml.xgb_trainer import XGBTrainer

    trainer = XGBTrainer(cache_dir=str(tmp_path / "xgb"))
    assert not trainer.load_model(), "load_model() must return False when no model file on disk"


def test_us252_xgb_should_retrain_true_on_fresh_startup():
    """XGBTrainer.should_retrain() returns True when model has never been trained."""
    from src.ml.xgb_trainer import XGBTrainer

    trainer = XGBTrainer()
    assert trainer.last_trained_at is None
    assert trainer.should_retrain(), "should_retrain() must be True at first startup"


# ---------------------------------------------------------------------------
# US-255: PerStrategyAdaptiveThreshold — per-strategy independence
# ---------------------------------------------------------------------------


def test_us255_get_or_create_returns_same_instance_for_same_strategy_id():
    """get_or_create() returns the exact same AdaptiveThreshold instance for the same strategy_id."""
    from src.tuning.adaptive_threshold import PerStrategyAdaptiveThreshold

    psat = PerStrategyAdaptiveThreshold(default_edge_bps=5.0)
    inst_a = psat.get_or_create("cross_exchange")
    inst_b = psat.get_or_create("cross_exchange")
    assert inst_a is inst_b, "Same strategy_id must return the same instance"


def test_us255_different_strategies_adjust_independently():
    """Adjusting one strategy's threshold does not affect another strategy."""
    from src.tuning.adaptive_threshold import PerStrategyAdaptiveThreshold

    psat = PerStrategyAdaptiveThreshold(default_edge_bps=5.0)

    # cross_exchange: win_rate > 0.9 and enough trades → edge decreases by step (1.0)
    psat.adjust("cross_exchange", win_rate=0.95, total_trades=50)

    # triangular: never adjusted → still at default
    assert psat.get_edge("triangular") == 5.0, (
        "triangular threshold must remain at default after only cross_exchange was adjusted"
    )
    # cross_exchange: decreased by 1 step
    assert psat.get_edge("cross_exchange") == 4.0, (
        "cross_exchange threshold must have decreased by 1 step (WR > 0.9)"
    )


def test_us255_independent_strategies_have_different_edge_after_divergent_adjustments():
    """Two strategies with opposite win rates should diverge in threshold."""
    from src.tuning.adaptive_threshold import PerStrategyAdaptiveThreshold

    psat = PerStrategyAdaptiveThreshold(default_edge_bps=5.0)

    # "fast" strategy: high WR → lower edge
    psat.adjust("fast", win_rate=0.95, total_trades=50)
    # "slow" strategy: low WR → higher edge
    psat.adjust("slow", win_rate=0.30, total_trades=50)

    assert psat.get_edge("fast") < psat.get_edge("slow"), (
        "High-WR strategy must have lower edge than low-WR strategy after adjustment"
    )


# ---------------------------------------------------------------------------
# US-247: CostCalculator.estimate_cost — intra-exchange no network cost
# ---------------------------------------------------------------------------


def _make_cost_calculator(network_cost: Decimal = Decimal("2.0")) -> object:
    from src.friction.cost_calculator import CostCalculator
    from src.friction.fee_model import FeeModel

    return CostCalculator(fee_model=FeeModel(), network_cost=network_cost)


def test_us247_estimate_cost_same_exchange_no_network_cost():
    """estimate_cost with dest_exchange_id == exchange_id → network_cost component = 0.

    Intra-exchange trades (e.g. triangular) do not require asset transfer.
    """
    from src.core.models import OrderSide

    network_cost_val = Decimal("2.0")
    calc = _make_cost_calculator(network_cost=network_cost_val)

    same_kwargs = dict(
        exchange_id="binance",
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        size=Decimal("0.01"),
        price=Decimal("50000"),
    )
    cost_same = calc.estimate_cost(**same_kwargs, dest_exchange_id="binance")
    cost_diff = calc.estimate_cost(**same_kwargs, dest_exchange_id="bybit")

    # Difference must equal exactly the network_cost_val
    assert cost_diff - cost_same == network_cost_val, (
        f"Expected difference={network_cost_val}, got {cost_diff - cost_same}"
    )


def test_us247_estimate_cost_different_exchange_adds_network_cost():
    """estimate_cost with different dest_exchange_id → positive network_cost is applied."""
    from src.core.models import OrderSide

    calc = _make_cost_calculator(network_cost=Decimal("3.0"))

    cost_cross = calc.estimate_cost(
        exchange_id="binance",
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        size=Decimal("0.01"),
        price=Decimal("50000"),
        dest_exchange_id="bybit",
    )
    cost_intra = calc.estimate_cost(
        exchange_id="binance",
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        size=Decimal("0.01"),
        price=Decimal("50000"),
        dest_exchange_id="binance",
    )

    assert cost_cross > cost_intra, "Cross-exchange cost must exceed intra-exchange cost"
