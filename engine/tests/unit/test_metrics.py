"""Tests for engine/src/infra/metrics.py"""
from __future__ import annotations

import pytest


class TestMetricsRegistered:
    def test_order_latency_histogram(self):
        from src.infra.metrics import ORDER_LATENCY
        assert ORDER_LATENCY is not None

    def test_signal_processing_histogram(self):
        from src.infra.metrics import SIGNAL_PROCESSING_TIME
        assert SIGNAL_PROCESSING_TIME is not None

    def test_kill_switch_latency_histogram(self):
        from src.infra.metrics import KILL_SWITCH_LATENCY
        assert KILL_SWITCH_LATENCY is not None

    def test_trades_counter(self):
        from src.infra.metrics import TRADES_TOTAL
        assert TRADES_TOTAL is not None

    def test_orders_counter(self):
        from src.infra.metrics import ORDERS_TOTAL
        assert ORDERS_TOTAL is not None

    def test_signals_counter(self):
        from src.infra.metrics import SIGNALS_TOTAL
        assert SIGNALS_TOTAL is not None

    def test_errors_counter(self):
        from src.infra.metrics import ERRORS_TOTAL
        assert ERRORS_TOTAL is not None

    def test_kill_switch_triggers_counter(self):
        from src.infra.metrics import KILL_SWITCH_TRIGGERS_TOTAL
        assert KILL_SWITCH_TRIGGERS_TOTAL is not None

    def test_risk_rejections_counter(self):
        from src.infra.metrics import RISK_REJECTIONS_TOTAL
        assert RISK_REJECTIONS_TOTAL is not None

    def test_open_positions_gauge(self):
        from src.infra.metrics import OPEN_POSITIONS
        assert OPEN_POSITIONS is not None

    def test_pnl_gauge(self):
        from src.infra.metrics import PNL_TOTAL
        assert PNL_TOTAL is not None

    def test_drawdown_gauge(self):
        from src.infra.metrics import DRAWDOWN_CURRENT
        assert DRAWDOWN_CURRENT is not None

    def test_exchange_health_gauge(self):
        from src.infra.metrics import EXCHANGE_HEALTH_SCORE
        assert EXCHANGE_HEALTH_SCORE is not None

    def test_circuit_breaker_state_gauge(self):
        from src.infra.metrics import CIRCUIT_BREAKER_STATE
        assert CIRCUIT_BREAKER_STATE is not None

    def test_capital_gauges(self):
        from src.infra.metrics import CAPITAL_AVAILABLE, CAPITAL_TOTAL
        assert CAPITAL_TOTAL is not None
        assert CAPITAL_AVAILABLE is not None


class TestMetricsRecording:
    def test_order_latency_observe(self):
        from src.infra.metrics import ORDER_LATENCY
        ORDER_LATENCY.labels(
            exchange="binance", symbol="BTC/USDT", side="BUY", order_type="market"
        ).observe(0.05)

    def test_signal_processing_observe(self):
        from src.infra.metrics import SIGNAL_PROCESSING_TIME
        SIGNAL_PROCESSING_TIME.labels(strategy="cross_exchange_spot").observe(0.001)

    def test_kill_switch_latency_observe(self):
        from src.infra.metrics import KILL_SWITCH_LATENCY
        KILL_SWITCH_LATENCY.labels(tier="tier1").observe(0.0005)
        KILL_SWITCH_LATENCY.labels(tier="tier2").observe(0.1)
        KILL_SWITCH_LATENCY.labels(tier="tier3").observe(0.8)

    def test_trades_total_inc(self):
        from src.infra.metrics import TRADES_TOTAL
        TRADES_TOTAL.labels(
            strategy="test", exchange_pair="binance_okx", result="win"
        ).inc()

    def test_errors_total_inc(self):
        from src.infra.metrics import ERRORS_TOTAL
        ERRORS_TOTAL.labels(component="kill_switch", error_type="redis_failure").inc()

    def test_kill_switch_triggers_inc(self):
        from src.infra.metrics import KILL_SWITCH_TRIGGERS_TOTAL
        KILL_SWITCH_TRIGGERS_TOTAL.labels(trigger_source="manual").inc()

    def test_risk_rejections_inc(self):
        from src.infra.metrics import RISK_REJECTIONS_TOTAL
        RISK_REJECTIONS_TOTAL.labels(check_number="0", reason="halted").inc()

    def test_exchange_health_set(self):
        from src.infra.metrics import EXCHANGE_HEALTH_SCORE
        EXCHANGE_HEALTH_SCORE.labels(exchange="binance").set(0.95)

    def test_circuit_breaker_state_set(self):
        from src.infra.metrics import CIRCUIT_BREAKER_STATE
        CIRCUIT_BREAKER_STATE.set(0)   # CLOSED
        CIRCUIT_BREAKER_STATE.set(1)   # OPEN
        CIRCUIT_BREAKER_STATE.set(2)   # HALF_OPEN

    def test_drawdown_gauge_set(self):
        from src.infra.metrics import DRAWDOWN_CURRENT
        DRAWDOWN_CURRENT.labels(strategy="cross_exchange_spot").set(0.015)

    def test_capital_gauges_set(self):
        from src.infra.metrics import CAPITAL_AVAILABLE, CAPITAL_TOTAL
        CAPITAL_TOTAL.set(100000)
        CAPITAL_AVAILABLE.set(75000)


class TestObservabilityMetrics:
    """BUG-197/198: TCA + ghost + reconciler observability metrics."""

    def test_tca_pnl_delta_registered_and_observable(self):
        from src.infra.metrics import TCA_PNL_DELTA_BPS
        assert TCA_PNL_DELTA_BPS is not None
        TCA_PNL_DELTA_BPS.labels(
            strategy="cross_exchange_spot", expected_type="immediate_fill",
        ).observe(3.5)
        TCA_PNL_DELTA_BPS.labels(
            strategy="funding_rate", expected_type="funding_cycle_8h",
        ).observe(-2.0)

    def test_tca_latency_registered_and_observable(self):
        from src.infra.metrics import TCA_LATENCY_MS
        assert TCA_LATENCY_MS is not None
        TCA_LATENCY_MS.labels(strategy="futures_futures").observe(45.0)

    def test_ghost_positions_total_counter(self):
        from src.infra.metrics import GHOST_POSITIONS_TOTAL
        assert GHOST_POSITIONS_TOTAL is not None
        GHOST_POSITIONS_TOTAL.labels(strategy="spot_futures", exchange="binance").inc()

    def test_ghost_positions_current_gauge(self):
        from src.infra.metrics import GHOST_POSITIONS_CURRENT
        assert GHOST_POSITIONS_CURRENT is not None
        GHOST_POSITIONS_CURRENT.labels(exchange="binance").set(0)
        GHOST_POSITIONS_CURRENT.labels(exchange="bybit").set(2)

    def test_reconciler_discrepancy_counter(self):
        from src.infra.metrics import RECONCILER_DISCREPANCY_TOTAL
        assert RECONCILER_DISCREPANCY_TOTAL is not None
        for _type in ("orphan", "unrecorded", "size_mismatch", "stranded"):
            RECONCILER_DISCREPANCY_TOTAL.labels(exchange="binance", type=_type).inc()
