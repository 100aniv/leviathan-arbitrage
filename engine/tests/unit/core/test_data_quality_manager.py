"""Tests for DataQualityManager (US-286~290).

US-286: Central DQM with check(), get_health_scores(), is_blacklisted(), cleanup()
US-287: Differential freshness thresholds per exchange type
US-288: Health score aggregation (min-based), guardian Check #5 via DQM
US-289: Anomaly detection (z-score, isolation, warmup)
US-290: Bithumb deviation + fast blacklist
"""
from __future__ import annotations

import time
from decimal import Decimal
from unittest.mock import patch

import pytest

from src.core.data_quality_manager import (
    AnomalyDetector,
    DataQualityManager,
    DataQualityResult,
    FUTURES_EXCHANGES,
    KOREAN_EXCHANGES,
)


# ======================================================================
# US-286: DataQualityManager core
# ======================================================================


class TestDataQualityManagerCore:
    """US-286: Central data quality gateway."""

    def test_create(self):
        dqm = DataQualityManager()
        assert dqm._check_count == 0
        assert dqm._reject_count == 0

    def test_check_ok(self):
        dqm = DataQualityManager()
        dqm.register_exchange("binance")
        # Record a heartbeat so health checker is connected
        dqm.record_ws_connect("binance")
        result = dqm.check("binance", "BTC/USDT", mid_price=50000.0)
        assert isinstance(result, DataQualityResult)
        assert result.ok is True
        assert result.score > 0
        assert dqm._check_count == 1

    def test_check_blacklisted(self):
        dqm = DataQualityManager()
        dqm.add_blacklist("binance", "BTC/USDT", ttl_s=60)
        result = dqm.check("binance", "BTC/USDT", mid_price=50000.0)
        assert result.ok is False
        assert result.score == 0.0
        assert "blacklisted" in result.reasons

    def test_is_blacklisted_ttl_expiry(self):
        dqm = DataQualityManager()
        dqm.add_blacklist("binance", "BTC/USDT", ttl_s=0.01)
        assert dqm.is_blacklisted("binance", "BTC/USDT") is True
        time.sleep(0.02)
        assert dqm.is_blacklisted("binance", "BTC/USDT") is False

    def test_cleanup_expired(self):
        dqm = DataQualityManager()
        dqm.add_blacklist("binance", "BTC/USDT", ttl_s=0.01)
        time.sleep(0.02)
        removed = dqm.cleanup_expired()
        assert removed == 1
        assert len(dqm._blacklist) == 0

    def test_get_stats(self):
        dqm = DataQualityManager()
        dqm.register_exchange("binance")
        stats = dqm.get_stats()
        assert "check_count" in stats
        assert "registered_exchanges" in stats
        assert "binance" in stats["registered_exchanges"]

    def test_record_update(self):
        dqm = DataQualityManager()
        dqm.record_update("binance", "BTC/USDT")
        assert ("binance", "BTC/USDT") in dqm._last_update

    def test_check_score_range(self):
        """Score always in [0.0, 1.0]."""
        dqm = DataQualityManager()
        dqm.register_exchange("binance")
        dqm.record_ws_connect("binance")
        result = dqm.check("binance", "BTC/USDT", mid_price=50000.0)
        assert 0.0 <= result.score <= 1.0


# ======================================================================
# US-287: Differential freshness thresholds
# ======================================================================


class TestFreshnessThresholds:
    """US-287: Per-exchange/type freshness thresholds."""

    def test_futures_threshold(self):
        dqm = DataQualityManager()
        assert dqm.get_freshness_threshold("binance_futures") == 0.5
        assert dqm.get_freshness_threshold("bybit_futures") == 0.5

    def test_korean_threshold(self):
        dqm = DataQualityManager()
        assert dqm.get_freshness_threshold("upbit") == 2.0
        assert dqm.get_freshness_threshold("coinone") == 2.0

    def test_bithumb_threshold(self):
        dqm = DataQualityManager()
        # Bithumb is tighter than general Korean
        assert dqm.get_freshness_threshold("bithumb") == 1.0
        assert dqm.get_freshness_threshold("bithumb") < dqm.get_freshness_threshold("upbit")

    def test_default_threshold(self):
        dqm = DataQualityManager()
        assert dqm.get_freshness_threshold("binance") == 1.0
        assert dqm.get_freshness_threshold("okx") == 1.0

    def test_freshness_check_fresh(self):
        dqm = DataQualityManager()
        now = time.monotonic()
        assert dqm.check_freshness("binance", "BTC/USDT", now) is True

    def test_freshness_check_stale(self):
        dqm = DataQualityManager()
        old_ts = time.monotonic() - 10.0  # 10s old
        assert dqm.check_freshness("binance", "BTC/USDT", old_ts) is False

    def test_freshness_check_no_data(self):
        """No previous update — optimistic pass."""
        dqm = DataQualityManager()
        assert dqm.check_freshness("binance", "BTC/USDT") is True

    @patch.dict("os.environ", {"FRESHNESS_FUTURES_S": "0.3"})
    def test_env_override(self):
        """Env vars override default thresholds."""
        # Need to reimport to pick up env var
        from importlib import reload
        import src.core.data_quality_manager as dqm_mod
        reload(dqm_mod)
        dqm = dqm_mod.DataQualityManager()
        assert dqm.get_freshness_threshold("binance_futures") == 0.3
        # Cleanup: reload with defaults
        reload(dqm_mod)


# ======================================================================
# US-288: Health score integration
# ======================================================================


class TestHealthScoreIntegration:
    """US-288: Exchange health score aggregation."""

    def test_register_exchange(self):
        dqm = DataQualityManager()
        dqm.register_exchange("binance")
        assert "binance" in dqm._health_checkers

    def test_lazy_init(self):
        dqm = DataQualityManager()
        checker = dqm.get_or_create_health_checker("okx")
        assert checker.exchange_id == "okx"
        # Second call returns same instance
        checker2 = dqm.get_or_create_health_checker("okx")
        assert checker is checker2

    def test_health_score_unregistered(self):
        """Unregistered exchange returns optimistic 1.0."""
        dqm = DataQualityManager()
        assert dqm.get_health_score("unknown") == 1.0

    def test_health_score_connected(self):
        dqm = DataQualityManager()
        dqm.register_exchange("binance")
        dqm.record_ws_connect("binance")
        score = dqm.get_health_score("binance")
        assert score > 0.5  # connected = healthy

    def test_health_score_disconnected(self):
        dqm = DataQualityManager()
        dqm.register_exchange("binance")
        dqm.record_ws_disconnect("binance")
        score = dqm.get_health_score("binance")
        assert score < 0.5  # disconnected = unhealthy

    def test_get_all_health_scores(self):
        dqm = DataQualityManager()
        dqm.register_exchange("binance")
        dqm.register_exchange("okx")
        scores = dqm.get_all_health_scores()
        assert "binance" in scores
        assert "okx" in scores

    def test_aggregate_min_based(self):
        """Aggregate uses min — weakest exchange limits all."""
        dqm = DataQualityManager()
        dqm.register_exchange("binance")
        dqm.register_exchange("okx")
        dqm.record_ws_connect("binance")
        dqm.record_ws_disconnect("okx")
        agg = dqm.aggregate_health_score()
        okx_score = dqm.get_health_score("okx")
        assert agg == okx_score  # min-based

    def test_aggregate_empty(self):
        dqm = DataQualityManager()
        assert dqm.aggregate_health_score() == 1.0

    def test_record_heartbeat(self):
        dqm = DataQualityManager()
        dqm.record_heartbeat("binance")
        assert "binance" in dqm._health_checkers

    def test_record_api_latency(self):
        dqm = DataQualityManager()
        dqm.record_api_latency("binance", 50.0)
        assert "binance" in dqm._health_checkers

    def test_guardian_check5_uses_dqm(self):
        """US-288: RiskGuardian Check #5 queries DQM when available."""
        from src.risk.circuit_breaker import CircuitBreaker
        from src.risk.guardian import PortfolioState, RiskGuardian, TradeProposal
        from src.risk.kill_switch import clear_halt
        clear_halt()  # Reset global halt flag (may be set by prior tests)

        cb = CircuitBreaker()
        guardian = RiskGuardian(circuit_breaker=cb)

        dqm = DataQualityManager()
        dqm.register_exchange("binance")
        dqm.record_ws_connect("binance")
        # Record low latency to push score above 0.9 threshold
        for _ in range(5):
            dqm.record_api_latency("binance", 10.0)
        guardian.data_quality_manager = dqm

        proposal = TradeProposal(
            strategy_id="cross_exchange",
            exchange_id="binance",
            symbol="BTC/USDT",
            side="BUY",
            size=Decimal("0.01"),
            price=Decimal("50000"),
            position_value=Decimal("500"),
        )

        portfolio = PortfolioState(
            total_capital=Decimal("10000"),
            used_capital=Decimal("1000"),
            current_drawdown_pct=Decimal("0.01"),
            total_exposure=Decimal("1000"),
            position_sizes={},
            exchange_health_scores={},  # Empty — DQM should be used instead
            volatility_1min={},
            volatility_24h={},
        )

        result = guardian.check(proposal, portfolio)
        # Should pass — DQM reports healthy binance (connected + low latency)
        assert result.approved is True

    def test_guardian_check5_dqm_unhealthy_rejects(self):
        """DQM unhealthy exchange → Check #5 rejection."""
        from src.risk.circuit_breaker import CircuitBreaker
        from src.risk.guardian import PortfolioState, RiskGuardian, TradeProposal
        from src.risk.kill_switch import clear_halt
        clear_halt()

        cb = CircuitBreaker()
        guardian = RiskGuardian(circuit_breaker=cb, warmup_seconds=0)  # disable warm-up for this test

        dqm = DataQualityManager()
        dqm.register_exchange("binance")
        dqm.record_ws_disconnect("binance")  # disconnected = low score
        guardian.data_quality_manager = dqm

        proposal = TradeProposal(
            strategy_id="cross_exchange",
            exchange_id="binance",
            symbol="BTC/USDT",
            side="BUY",
            size=Decimal("0.01"),
            price=Decimal("50000"),
            position_value=Decimal("500"),
        )

        portfolio = PortfolioState(
            total_capital=Decimal("10000"),
            used_capital=Decimal("1000"),
            current_drawdown_pct=Decimal("0.01"),
            total_exposure=Decimal("1000"),
            position_sizes={},
            exchange_health_scores={},
            volatility_1min={},
            volatility_24h={},
        )

        result = guardian.check(proposal, portfolio)
        assert result.approved is False
        assert result.rejected_at_check == 5


# ======================================================================
# US-289: Anomaly detection
# ======================================================================


class TestAnomalyDetection:
    """US-289: Z-score based price anomaly detection."""

    def test_warmup_passthrough(self):
        detector = AnomalyDetector(warmup=5)
        for _ in range(4):
            ok, reason = detector.update_and_check("binance", "BTC/USDT", 50000.0)
            assert ok is True
            assert reason == "warmup"

    def test_normal_price(self):
        detector = AnomalyDetector(warmup=5, z_threshold=4.0)
        # Fill warmup
        for i in range(10):
            detector.update_and_check("binance", "BTC/USDT", 50000.0 + i)
        # Normal price
        ok, _ = detector.update_and_check("binance", "BTC/USDT", 50005.0)
        assert ok is True

    def test_anomaly_detected(self):
        detector = AnomalyDetector(warmup=5, z_threshold=3.0)
        # Fill with slightly varied prices (need stdev > 0)
        import random
        random.seed(123)
        for _ in range(20):
            detector.update_and_check("binance", "BTC/USDT", 50000.0 + random.uniform(-10, 10))
        # Extreme price — 2x deviation far beyond z=3
        ok, reason = detector.update_and_check("binance", "BTC/USDT", 100000.0)
        assert ok is False
        assert "z-score" in reason

    def test_isolation_period(self):
        detector = AnomalyDetector(warmup=5, z_threshold=3.0)
        import random
        random.seed(456)
        for _ in range(20):
            detector.update_and_check("binance", "BTC/USDT", 50000.0 + random.uniform(-10, 10))
        # Trigger anomaly
        detector.update_and_check("binance", "BTC/USDT", 100000.0)
        # During isolation, even normal price is rejected
        ok, reason = detector.update_and_check("binance", "BTC/USDT", 50000.0)
        assert ok is False
        assert "isolated" in reason

    def test_separate_symbols(self):
        """Different symbols tracked independently."""
        detector = AnomalyDetector(warmup=5, z_threshold=3.0)
        import random
        random.seed(42)
        for i in range(20):
            detector.update_and_check("binance", "BTC/USDT", 50000.0 + random.uniform(-10, 10))
            detector.update_and_check("binance", "ETH/USDT", 3000.0 + random.uniform(-5, 5))
        # Anomaly only for BTC
        ok_btc, _ = detector.update_and_check("binance", "BTC/USDT", 100000.0)
        ok_eth, _ = detector.update_and_check("binance", "ETH/USDT", 3001.0)
        assert ok_btc is False
        assert ok_eth is True

    def test_cleanup(self):
        detector = AnomalyDetector(warmup=5, z_threshold=3.0)
        import random
        random.seed(789)
        for _ in range(20):
            detector.update_and_check("binance", "BTC/USDT", 50000.0 + random.uniform(-10, 10))
        detector.update_and_check("binance", "BTC/USDT", 100000.0)
        assert len(detector._isolated) > 0
        # Set expiry to past
        for k in detector._isolated:
            detector._isolated[k] = time.monotonic() - 1
        detector.cleanup()
        assert len(detector._isolated) == 0

    def test_dqm_anomaly_rejects(self):
        """DQM check() returns ok=False on anomaly."""
        dqm = DataQualityManager()
        dqm.register_exchange("binance")
        dqm.record_ws_connect("binance")
        import random
        random.seed(999)
        # Fill warmup with varied prices
        for _ in range(20):
            dqm.check("binance", "BTC/USDT", mid_price=50000.0 + random.uniform(-10, 10))
        # Extreme price
        result = dqm.check("binance", "BTC/USDT", mid_price=100000.0)
        assert result.ok is False
        assert any("anomaly" in r for r in result.reasons)


# ======================================================================
# US-290: Bithumb stale specialization
# ======================================================================


class TestBithumbStale:
    """US-290: Bithumb-specific deviation + fast blacklist."""

    def test_bithumb_normal(self):
        dqm = DataQualityManager()
        dqm.register_exchange("bithumb")
        dqm.record_ws_connect("bithumb")
        # Fill buffer
        for _ in range(10):
            result = dqm.check("bithumb", "NOM/KRW", mid_price=100.0)
        # Normal price
        result = dqm.check("bithumb", "NOM/KRW", mid_price=101.0)
        assert result.ok is True

    def test_bithumb_5pct_deviation_rejects(self):
        """5% deviation → reject (not blacklist)."""
        dqm = DataQualityManager()
        dqm.register_exchange("bithumb")
        dqm.record_ws_connect("bithumb")
        # Fill buffer with stable price
        for _ in range(10):
            dqm.check("bithumb", "NOM/KRW", mid_price=100.0)
        # 6% deviation
        result = dqm.check("bithumb", "NOM/KRW", mid_price=106.0)
        assert result.ok is False

    def test_bithumb_2x_deviation_blacklists(self):
        """2x+ price (deviation ratio > 1.0) → instant blacklist (TTL 600s)."""
        dqm = DataQualityManager()
        dqm.register_exchange("bithumb")
        dqm.record_ws_connect("bithumb")
        # Fill buffer with slight variation around 100
        for i in range(10):
            dqm.check("bithumb", "SXP/KRW", mid_price=100.0 + i * 0.1)
        # 2.5x price = deviation ratio 1.5 > 1.0 threshold → blacklist
        result = dqm.check("bithumb", "SXP/KRW", mid_price=250.0)
        assert result.ok is False
        assert dqm.is_blacklisted("bithumb", "SXP/KRW") is True

    def test_bithumb_blacklist_ttl_600s(self):
        """Bithumb blacklist uses 600s TTL by default."""
        dqm = DataQualityManager()
        dqm.add_blacklist("bithumb", "NOM/KRW")
        # Check TTL — should be ~600s from now
        key = ("bithumb", "NOM/KRW")
        remaining = dqm._blacklist[key] - time.monotonic()
        assert 590 < remaining < 610

    def test_non_bithumb_not_affected(self):
        """Bithumb-specific logic doesn't apply to other exchanges."""
        dqm = DataQualityManager()
        dqm.register_exchange("upbit")
        dqm.record_ws_connect("upbit")
        # Fill buffer
        for _ in range(10):
            dqm.check("upbit", "NOM/KRW", mid_price=100.0)
        # 6% deviation — should NOT trigger bithumb logic
        result = dqm.check("upbit", "NOM/KRW", mid_price=106.0)
        # upbit uses anomaly detector instead, which has a higher threshold
        assert result.ok is True

    def test_bithumb_freshness_tighter(self):
        """Bithumb freshness (1.0s) is tighter than general Korean (2.0s)."""
        dqm = DataQualityManager()
        assert dqm.get_freshness_threshold("bithumb") == 1.0
        assert dqm.get_freshness_threshold("upbit") == 2.0
        assert dqm.get_freshness_threshold("bithumb") < dqm.get_freshness_threshold("upbit")


# ======================================================================
# Exchange classification
# ======================================================================


class TestExchangeClassification:
    """Verify exchange set constants."""

    def test_korean_exchanges(self):
        assert "upbit" in KOREAN_EXCHANGES
        assert "bithumb" in KOREAN_EXCHANGES
        assert "coinone" in KOREAN_EXCHANGES
        assert "binance" not in KOREAN_EXCHANGES

    def test_futures_exchanges(self):
        assert "binance_futures" in FUTURES_EXCHANGES
        assert "bybit_futures" in FUTURES_EXCHANGES
        assert "okx_futures" in FUTURES_EXCHANGES
        assert "binance" not in FUTURES_EXCHANGES
