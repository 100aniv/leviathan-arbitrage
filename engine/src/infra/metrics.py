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

SIGNALS_REJECTED_SYMBOL_UNSUPPORTED = Counter(
    "leviathan_signals_rejected_symbol_unsupported_total",
    "Signals rejected because the target exchange does not list the symbol (BUG-225)",
    ["strategy", "exchange"],
)

# WS-A1/A5: track which branch of _compute_pnl_from_result produced each PnL.
# Exposes drift between exchange-reported realized PnL and engine recomputes.
PNL_SOURCE_TOTAL = Counter(
    "leviathan_pnl_source_total",
    "PnL source distribution per strategy (which branch of _compute_pnl_from_result fired)",
    ["strategy", "source"],
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


# ---------------------------------------------------------------------------
# BUG-197: Post-trade TCA (expected vs actual PnL, signal→fill latency)
# ---------------------------------------------------------------------------

TCA_PNL_DELTA_BPS = Histogram(
    "leviathan_tca_pnl_delta_bps",
    "TCA expected vs actual PnL delta in bps (positive = leakage)",
    ["strategy", "expected_type"],  # expected_type: immediate_fill / funding_cycle_8h
    buckets=(
        -100, -50, -20, -10, -5, -2, -1, 0, 1, 2, 5, 10, 20, 50, 100, 500,
        float("inf"),
    ),
)

TCA_LATENCY_MS = Histogram(
    "leviathan_tca_latency_ms",
    "Signal-to-fill latency (ms) for TCA analysis",
    ["strategy"],
    buckets=(5, 10, 20, 30, 50, 75, 100, 150, 200, 300, 500, 1000, 2000, float("inf")),
)


# ---------------------------------------------------------------------------
# BUG-198a: Ghost positions (exchange confirms no position, engine cleared)
# ---------------------------------------------------------------------------

GHOST_POSITIONS_TOTAL = Counter(
    "leviathan_ghost_positions_total",
    "Ghost position clears (engine state wiped when exchange had none)",
    ["strategy", "exchange"],
)

GHOST_POSITIONS_CURRENT = Gauge(
    "leviathan_ghost_positions_current",
    "Current stranded/ghost positions awaiting recovery",
    ["exchange"],
)


# ---------------------------------------------------------------------------
# BUG-198b: Reconciler discrepancies (engine vs exchange position state)
# ---------------------------------------------------------------------------

RECONCILER_DISCREPANCY_TOTAL = Counter(
    "leviathan_reconciler_discrepancy_total",
    "Position reconciler discrepancies by type",
    # type: orphan (engine has, exchange doesn't)
    #       unrecorded (exchange has, engine doesn't)
    #       size_mismatch
    #       stranded (rollback failed)
    ["exchange", "type"],
)


# ---------------------------------------------------------------------------
# WS-A2: Exchange-reported income (Binance /fapi/v1/income + Bitget /account/bill)
# ---------------------------------------------------------------------------

EXCHANGE_INCOME_TOTAL = Counter(
    "leviathan_exchange_income_total_usdt",
    "Exchange-reported income by type (REALIZED_PNL / COMMISSION / FUNDING_FEE / TRANSFER)",
    ["exchange", "income_type"],
)

EXCHANGE_INCOME_FETCH_LATENCY = Histogram(
    "leviathan_exchange_income_fetch_latency_seconds",
    "Latency of exchange income endpoint polls",
    ["exchange"],
    buckets=[0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0],
)

EXCHANGE_INCOME_POLLS_TOTAL = Counter(
    "leviathan_exchange_income_polls_total",
    "Exchange income polling cycles (success/error)",
    ["exchange", "result"],  # result: ok / error
)

PNL_RECONCILIATION_VARIANCE_PCT = Gauge(
    "leviathan_pnl_reconciliation_variance_pct",
    "Engine total_pnl vs exchange-reported 24h sum (realized+commission+funding) variance percent",
    ["exchange"],
)


# ---------------------------------------------------------------------------
# WS-A3: Funding accrual (engine-side per-position projected + realized at close)
# ---------------------------------------------------------------------------

FUNDING_ACCRUED_USDT = Gauge(
    "leviathan_funding_accrued_usdt",
    "Projected funding cost accrued on an open position (cumulative, signed USDT)",
    ["strategy", "exchange", "symbol"],
)

FUNDING_REALIZED_USDT_TOTAL = Counter(
    "leviathan_funding_realized_usdt_total",
    "Realized funding cost deducted from total_pnl on position close (signed sum USDT)",
    ["strategy", "exchange"],
)


# ---------------------------------------------------------------------------
# WS-A4: TCA adaptive feedback observation layer (no threshold adjustment yet)
# ---------------------------------------------------------------------------

OBSERVED_SLIPPAGE_P95_BPS = Gauge(
    "leviathan_observed_slippage_p95_bps",
    "Observed slippage p95 (bps) from last N TCA observations per (strategy, exchange)",
    ["strategy", "exchange"],
)


# ---------------------------------------------------------------------------
# WS-B: dynamic min_spread threshold exposure + rejection counter
# ---------------------------------------------------------------------------

DYNAMIC_MIN_SPREAD_BPS = Gauge(
    "leviathan_dynamic_min_spread_bps",
    "Dynamic pre-trade min_spread threshold (bps) = fee + p95_slippage + funding + margin",
    ["strategy", "exchange_pair"],
)

SIGNALS_REJECTED_BY_COST_MODEL = Counter(
    "leviathan_signals_rejected_by_cost_model_total",
    "Signals rejected because expected_spread_bps < dynamic_min_spread_bps",
    ["strategy", "exchange_pair"],
)


# ---------------------------------------------------------------------------
# WS-D1: engine vs exchange PnL divergence HALT counter
# ---------------------------------------------------------------------------

PNL_DIVERGENCE_HALT_TRIGGERED = Counter(
    "leviathan_pnl_divergence_halt_triggered_total",
    "Number of HALT events triggered because engine total_pnl diverged from exchange 24h income by >= threshold",
)

PNL_DIVERGENCE_PCT = Gauge(
    "leviathan_pnl_divergence_pct",
    "Current rolling divergence percentage between engine total_pnl and exchange-reported 24h income sum",
)


# ---------------------------------------------------------------------------
# WS-D2: pre-execution toxicity filter rejections
# ---------------------------------------------------------------------------

SIGNALS_REJECTED_TOXICITY = Counter(
    "leviathan_signals_rejected_toxicity_total",
    "Signals rejected by pre-execution toxicity filter (orderbook imbalance / depth volatility)",
    ["strategy", "exchange", "reason"],  # reason: imbalance / depth_volatility / empty_book
)

# BUG-220: Reject orders below per-exchange minimum notional (e.g. Binance futures $20).
# Incremented when the executor/trade_consumer filters a signal because notional < min.
SIGNALS_REJECTED_NOTIONAL = Counter(
    "leviathan_signals_rejected_notional_total",
    "Signals rejected because leg notional is below the exchange-specific minimum",
    ["exchange", "symbol"],
)


# ---------------------------------------------------------------------------
# WS-D3: Sharpe + Max Drawdown (30-day rolling)
# ---------------------------------------------------------------------------

SHARPE_30D = Gauge(
    "leviathan_sharpe_30d",
    "Annualized Sharpe ratio over the last 30 daily returns",
)

MDD_30D_PCT = Gauge(
    "leviathan_mdd_30d_pct",
    "Maximum drawdown percentage over the last 30-day rolling equity curve",
)


def start_metrics_server(port: int = 8000) -> None:
    """Start Prometheus metrics HTTP server on the given port."""
    start_http_server(port)
