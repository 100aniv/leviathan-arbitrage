"""LEVIATHAN Prometheus Metrics.

All metric definitions for the engine.
Exposed at /metrics by the API server via start_metrics_server().
"""
from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram, start_http_server

# ---------------------------------------------------------------------------
# Histograms — latency measurements
# ---------------------------------------------------------------------------

ORDER_LATENCY = Histogram(
    "leviathan_order_latency_seconds",
    "Time from order submission to fill confirmation",
    ["exchange", "symbol", "side", "order_type"],
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0],
)

SIGNAL_PROCESSING_TIME = Histogram(
    "leviathan_signal_processing_seconds",
    "Time to process a signal from raw spread to emit decision",
    ["strategy"],
    buckets=[0.0001, 0.0005, 0.001, 0.005, 0.01, 0.025, 0.05, 0.1],
)

KILL_SWITCH_LATENCY = Histogram(
    "leviathan_kill_switch_latency_seconds",
    "Kill switch execution latency per tier",
    ["tier"],
    buckets=[0.0001, 0.001, 0.01, 0.05, 0.1, 0.5, 1.0, 2.0, 5.0],
)

# ---------------------------------------------------------------------------
# Counters — monotonically increasing totals
# ---------------------------------------------------------------------------

TRADES_TOTAL = Counter(
    "leviathan_trades_total",
    "Total completed round-trip trades",
    ["strategy", "exchange_pair", "result"],  # result: win/loss/breakeven
)

ORDERS_TOTAL = Counter(
    "leviathan_orders_total",
    "Total orders submitted",
    ["exchange", "side", "order_type", "status"],  # status: filled/cancelled/rejected
)

SIGNALS_TOTAL = Counter(
    "leviathan_signals_total",
    "Total signals evaluated",
    ["strategy", "decision"],  # decision: emit/filtered/rejected
)

ERRORS_TOTAL = Counter(
    "leviathan_errors_total",
    "Total errors by component and type",
    ["component", "error_type"],
)

KILL_SWITCH_TRIGGERS_TOTAL = Counter(
    "leviathan_kill_switch_triggers_total",
    "Total kill switch activations",
    ["trigger_source"],  # manual/automated/mdd/consecutive_loss
)

RISK_REJECTIONS_TOTAL = Counter(
    "leviathan_risk_rejections_total",
    "Total trades rejected by risk guardian",
    ["check_number", "reason"],
)

STRATEGY_OVERLAP_TOTAL = Counter(
    "leviathan_strategy_overlap_total",
    "Total strategy overlap collisions blocked (same symbol+exchange pair within 10s window)",
    ["symbol", "strategy"],
)

# ---------------------------------------------------------------------------
# Gauges — current point-in-time values
# ---------------------------------------------------------------------------

OPEN_POSITIONS = Gauge(
    "leviathan_open_positions",
    "Number of currently open positions",
    ["strategy", "exchange"],
)

PNL_TOTAL = Gauge(
    "leviathan_pnl_total_usd",
    "Total realized PnL in USD",
    ["strategy"],
)

DRAWDOWN_CURRENT = Gauge(
    "leviathan_drawdown_current_pct",
    "Current drawdown as percentage of peak capital (0-1)",
    ["strategy"],
)

EXCHANGE_HEALTH_SCORE = Gauge(
    "leviathan_exchange_health_score",
    "Exchange health score (0=unhealthy, 1=fully healthy)",
    ["exchange"],
)

CIRCUIT_BREAKER_STATE = Gauge(
    "leviathan_circuit_breaker_state",
    "Circuit breaker state (0=CLOSED, 1=OPEN, 2=HALF_OPEN)",
)

CAPITAL_TOTAL = Gauge(
    "leviathan_capital_total_usd",
    "Total capital under management in USD",
)

CAPITAL_AVAILABLE = Gauge(
    "leviathan_capital_available_usd",
    "Available (free) capital in USD",
)

KILL_SWITCH_ACTIVE = Gauge(
    "leviathan_kill_switch_active",
    "Kill switch state (0=inactive, 1=active/halted)",
)

ROLLBACKS_TOTAL = Counter(
    "leviathan_rollbacks_total",
    "Total trade rollbacks (second-leg failures)",
    ["exchange", "reason"],
)

# ---------------------------------------------------------------------------
# Phase 2: Rust hot-path observability
# ---------------------------------------------------------------------------

ORDERBOOK_UPDATE_TIME = Histogram(
    "leviathan_orderbook_update_seconds",
    "Time to apply orderbook snapshot/delta update",
    ["backend"],  # backend: rust/python
    buckets=[0.000001, 0.000005, 0.00001, 0.00005, 0.0001, 0.0005, 0.001, 0.005],
)

SIGNAL_COUNT = Counter(
    "leviathan_signal_count",
    "Total arbitrage signals detected (Phase 1 real data)",
    ["exchange_pair"],
)

SPREAD_BPS = Histogram(
    "leviathan_spread_bps",
    "Observed spread in basis points",
    ["exchange_pair"],
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 25.0, 50.0, 100.0],
)

COLLECTOR_MESSAGES = Counter(
    "leviathan_collector_messages_total",
    "Total WebSocket messages received by collectors",
    ["exchange"],
)

WS_MESSAGE_LATENCY = Histogram(
    "leviathan_ws_message_latency_seconds",
    "WebSocket message latency: exchange timestamp to local receipt time",
    ["exchange"],
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0],
)

# ---------------------------------------------------------------------------
# Phase G: Stale orderbook detection (US-066)
# ---------------------------------------------------------------------------

STALE_ORDERBOOK_REJECTED = Counter(
    "shadow_stale_orderbook_rejected_total",
    "Orderbooks rejected due to stale data detection",
    ["exchange", "reason"],  # reason: cross_validation, blacklisted
)

TRADE_LOSS_CAPPED = Counter(
    "shadow_trade_loss_capped_total",
    "Trades where per-trade loss was capped at max threshold",
    ["exchange"],
)


# ---------------------------------------------------------------------------
# Wave 3: Dynamic sizer, slippage feedback, correlation, IOC order metrics
# ---------------------------------------------------------------------------

SLIPPAGE_ADJUSTMENT = Gauge(
    "leviathan_slippage_adjustment_factor",
    "EMA-adjusted slippage calibration factor",
)

SLIPPAGE_ERROR = Histogram(
    "leviathan_slippage_prediction_error",
    "Slippage prediction error distribution",
    buckets=[0.0001, 0.0005, 0.001, 0.005, 0.01, 0.05],
)

STRATEGY_CORRELATION = Gauge(
    "leviathan_strategy_correlation",
    "Pairwise strategy PnL correlation",
    ["strategy_a", "strategy_b"],
)

IOC_FILL_RATE = Gauge(
    "leviathan_ioc_fill_rate",
    "IOC limit order fill rate vs total",
)

IOC_VS_MARKET = Histogram(
    "leviathan_ioc_vs_market_slippage_bps",
    "IOC vs market order slippage comparison",
    buckets=[0.5, 1, 2, 5, 10, 20, 50],
)


# ---------------------------------------------------------------------------
# Phase S20: Enhanced monitoring metrics
# ---------------------------------------------------------------------------

# Strategy-level metrics
STRATEGY_TRADES_TOTAL = Counter(
    "leviathan_strategy_trades_total",
    "Total trades per strategy",
    ["strategy", "result"],
)

STRATEGY_SIGNALS_TOTAL = Counter(
    "leviathan_strategy_signals_total",
    "Total signals per strategy",
    ["strategy", "decision"],
)

STRATEGY_LATENCY = Histogram(
    "leviathan_strategy_latency_seconds",
    "Per-strategy signal-to-execution latency",
    ["strategy"],
    buckets=[0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0],
)

# Portfolio-level metrics
PORTFOLIO_PNL = Gauge(
    "leviathan_portfolio_pnl_usd",
    "Total portfolio PnL in USD",
)

PORTFOLIO_MDD = Gauge(
    "leviathan_portfolio_mdd_pct",
    "Portfolio maximum drawdown percentage",
)

PORTFOLIO_SHARPE = Gauge(
    "leviathan_portfolio_sharpe_ratio",
    "Portfolio Sharpe ratio (rolling)",
)

# Exchange health metrics
EXCHANGE_LATENCY = Histogram(
    "leviathan_exchange_api_latency_seconds",
    "Exchange API response latency",
    ["exchange", "endpoint"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0],
)

EXCHANGE_WS_RECONNECTS = Counter(
    "leviathan_exchange_ws_reconnects_total",
    "WebSocket reconnection count",
    ["exchange"],
)

# Execution latency (signal → fill)
EXECUTION_LATENCY = Histogram(
    "leviathan_execution_latency_seconds",
    "Signal detection to fill confirmation latency",
    ["strategy", "exchange"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0],
)

# Data quality metrics (Phase S19 integration)
DATA_QUALITY_SCORE = Gauge(
    "leviathan_data_quality_score",
    "Data quality score per exchange (0-1)",
    ["exchange"],
)

STALE_DATA_EVENTS = Counter(
    "leviathan_stale_data_events_total",
    "Stale data detection events",
    ["exchange", "symbol"],
)


def start_metrics_server(port: int = 8000) -> None:
    """Start Prometheus metrics HTTP server on the given port."""
    start_http_server(port)
