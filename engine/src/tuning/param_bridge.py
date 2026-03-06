"""Parameter bridge — converts optimizer output to strategy runtime config.

Maps StrategyParams (Optuna output) to per-strategy configuration dicts,
enabling hot-swap of tuned parameters via StrategyManager.reconfigure().
"""
from __future__ import annotations

import logging
from dataclasses import asdict
from decimal import Decimal
from typing import Any

from src.tuning.backtest import StrategyParams

logger = logging.getLogger(__name__)

# Strategy type constants matching STRATEGY_TYPE attributes
CROSS_EXCHANGE = "cross_exchange"
TRIANGULAR = "triangular"
SPOT_FUTURES = "spot_futures"
FUNDING_RATE = "funding_rate"
STATISTICAL_ARB = "statistical_arb"
LATENCY_ARB = "latency_arb"
FUTURES_FUTURES = "futures_futures"
CEX_DEX = "cex_dex"

# Default parameter mappings per strategy type
_PARAM_MAPPINGS: dict[str, dict[str, str]] = {
    CROSS_EXCHANGE: {
        "min_spread_bps": "min_spread_bps",
        "entry_threshold": "entry_threshold",
        "exit_threshold": "exit_threshold",
        "max_position_size": "max_position_usdt",
        "stop_loss_pct": "stop_loss_pct",
    },
    TRIANGULAR: {
        "min_spread_bps": "min_profit_bps",
        "entry_threshold": "entry_threshold",
        "exit_threshold": "exit_threshold",
        "max_position_size": "max_notional_usdt",
        "stop_loss_pct": "stop_loss_pct",
    },
    SPOT_FUTURES: {
        "min_spread_bps": "min_basis_bps",
        "entry_threshold": "entry_threshold",
        "exit_threshold": "exit_threshold",
        "max_position_size": "max_position_usdt",
        "stop_loss_pct": "stop_loss_pct",
    },
    FUNDING_RATE: {
        "min_spread_bps": "min_funding_rate_bps",
        "entry_threshold": "entry_threshold",
        "exit_threshold": "exit_threshold",
        "max_position_size": "max_position_usdt",
        "stop_loss_pct": "stop_loss_pct",
    },
    STATISTICAL_ARB: {
        "min_spread_bps": "z_score_entry",
        "entry_threshold": "entry_threshold",
        "exit_threshold": "exit_threshold",
        "max_position_size": "max_position_usdt",
        "stop_loss_pct": "stop_loss_pct",
    },
    LATENCY_ARB: {
        "min_spread_bps": "min_edge_bps",
        "entry_threshold": "entry_threshold",
        "exit_threshold": "exit_threshold",
        "max_position_size": "max_position_usdt",
        "stop_loss_pct": "stop_loss_pct",
    },
    FUTURES_FUTURES: {
        "min_spread_bps": "min_spread_bps",
        "entry_threshold": "entry_threshold",
        "exit_threshold": "exit_threshold",
        "max_position_size": "max_position_usdt",
        "stop_loss_pct": "stop_loss_pct",
    },
    CEX_DEX: {
        "min_spread_bps": "min_spread_bps",
        "entry_threshold": "entry_threshold",
        "exit_threshold": "exit_threshold",
        "max_position_size": "max_position_usdt",
        "stop_loss_pct": "stop_loss_pct",
    },
}


def params_to_strategy_config(
    params: StrategyParams,
    strategy_type: str,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Convert StrategyParams to a strategy-specific config dict.

    Args:
        params: Optimized parameters from the tuner.
        strategy_type: One of the STRATEGY_TYPE constants.
        overrides: Additional config values to merge.

    Returns:
        Config dict suitable for StrategyManager.reconfigure().
    """
    mapping = _PARAM_MAPPINGS.get(strategy_type, _PARAM_MAPPINGS[CROSS_EXCHANGE])
    params_dict = asdict(params)

    config: dict[str, Any] = {}
    for src_key, dst_key in mapping.items():
        if src_key in params_dict:
            config[dst_key] = params_dict[src_key]

    if overrides:
        config.update(overrides)

    return config


def strategy_config_to_params(
    config: dict[str, Any],
    strategy_type: str,
) -> StrategyParams:
    """Reverse mapping: strategy config dict → StrategyParams.

    Useful for initializing optimizer from current strategy settings.
    """
    mapping = _PARAM_MAPPINGS.get(strategy_type, _PARAM_MAPPINGS[CROSS_EXCHANGE])
    reverse_map = {v: k for k, v in mapping.items()}

    kwargs: dict[str, float] = {}
    for config_key, param_key in reverse_map.items():
        if config_key in config:
            kwargs[param_key] = float(config[config_key])

    return StrategyParams(**kwargs)


def apply_params_to_strategy(
    strategy_manager: Any,
    strategy_id: str,
    params: StrategyParams,
    strategy_type: str | None = None,
) -> dict[str, Any]:
    """Apply optimized params to a live strategy via StrategyManager.

    Args:
        strategy_manager: StrategyManager instance.
        strategy_id: Target strategy ID.
        params: Optimized parameters.
        strategy_type: Override strategy type (auto-detected if None).

    Returns:
        The config dict that was applied.
    """
    if strategy_type is None:
        strategy = strategy_manager.get_strategy(strategy_id)
        if strategy is not None:
            strategy_type = getattr(strategy, "STRATEGY_TYPE", CROSS_EXCHANGE)
        else:
            strategy_type = CROSS_EXCHANGE

    config = params_to_strategy_config(params, strategy_type)

    logger.info(
        "Applying optimized params to strategy %s: %s",
        strategy_id,
        config,
    )

    return config
