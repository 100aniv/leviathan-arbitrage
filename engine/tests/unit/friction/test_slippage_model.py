"""Tests for SlippageModel — power-law slippage per Amendment 6."""
import math
import pytest
from decimal import Decimal

from src.core.order_book import OrderBook
from src.friction.slippage_model import CEXOrderbookSlippage, SlippagePrediction, SlippageModel


@pytest.fixture
def sample_book():
    book = OrderBook(symbol="BTC/USDT", exchange="binance")
    book.apply_snapshot(
        bids=[("50000.00", "10.0"), ("49990.00", "20.0")],
        asks=[("50010.00", "10.0"), ("50020.00", "20.0")],
    )
    return book


class TestCEXOrderbookSlippage:
    def test_predict_returns_slippage_prediction(self, sample_book):
        model = CEXOrderbookSlippage(k=Decimal("1.0"), cold_start=False)
        pred = model.predict(sample_book, Decimal("1.0"), Decimal("1000"), Decimal("0.01"))
        assert isinstance(pred, SlippagePrediction)
        assert pred.expected >= Decimal("0")

    def test_cold_start_increases_estimate(self, sample_book):
        model_cold = CEXOrderbookSlippage(k=Decimal("1.0"), cold_start=True)
        model_warm = CEXOrderbookSlippage(k=Decimal("1.0"), cold_start=False)
        pred_cold = model_cold.predict(sample_book, Decimal("1.0"), Decimal("1000"), Decimal("0.01"))
        pred_warm = model_warm.predict(sample_book, Decimal("1.0"), Decimal("1000"), Decimal("0.01"))
        assert pred_cold.expected > pred_warm.expected

    def test_cold_start_multiplier_is_1_5x(self, sample_book):
        model_cold = CEXOrderbookSlippage(k=Decimal("1.0"), cold_start=True)
        model_warm = CEXOrderbookSlippage(k=Decimal("1.0"), cold_start=False)
        pred_cold = model_cold.predict(sample_book, Decimal("1.0"), Decimal("1000"), Decimal("0.01"))
        pred_warm = model_warm.predict(sample_book, Decimal("1.0"), Decimal("1000"), Decimal("0.01"))
        ratio = pred_cold.expected / pred_warm.expected
        assert abs(float(ratio) - 1.5) < 0.001

    def test_confidence_interval_within_adv(self, sample_book):
        # size=100, adv=1000 → ratio=0.1 ≤ 1.0 → ±20% CI
        model = CEXOrderbookSlippage(k=Decimal("1.0"), cold_start=False)
        pred = model.predict(sample_book, Decimal("100"), Decimal("1000"), Decimal("0.01"))
        assert pred.extrapolation_distance <= 1.0
        expected_upper = pred.expected * Decimal("1.2")
        assert abs(pred.upper - expected_upper) < Decimal("0.0001")
        expected_lower = pred.expected * Decimal("0.8")
        assert abs(pred.lower - expected_lower) < Decimal("0.0001")

    def test_confidence_interval_1_to_3x_adv(self, sample_book):
        # size=2000, adv=1000 → ratio=2.0 → ±50% CI
        model = CEXOrderbookSlippage(k=Decimal("1.0"), cold_start=False)
        pred = model.predict(sample_book, Decimal("2000"), Decimal("1000"), Decimal("0.01"))
        assert 1.0 < pred.extrapolation_distance <= 3.0
        expected_upper = pred.expected * Decimal("1.5")
        assert abs(pred.upper - expected_upper) < Decimal("0.0001")

    def test_confidence_interval_3_to_10x_adv(self, sample_book):
        # size=5000, adv=1000 → ratio=5.0 → ±100% CI
        model = CEXOrderbookSlippage(k=Decimal("1.0"), cold_start=False)
        pred = model.predict(sample_book, Decimal("5000"), Decimal("1000"), Decimal("0.01"))
        assert 3.0 < pred.extrapolation_distance <= 10.0
        expected_upper = pred.expected * Decimal("2.0")
        assert abs(pred.upper - expected_upper) < Decimal("0.0001")

    def test_extrapolation_beyond_10x_flags_do_not_trade(self, sample_book):
        # size=15000, adv=1000 → ratio=15.0 → very wide CI
        model = CEXOrderbookSlippage(k=Decimal("1.0"), cold_start=False)
        pred = model.predict(sample_book, Decimal("15000"), Decimal("1000"), Decimal("0.01"))
        assert pred.extrapolation_distance > 10.0
        assert pred.upper > pred.expected * Decimal("5")

    def test_lower_bound_non_negative(self, sample_book):
        model = CEXOrderbookSlippage(k=Decimal("1.0"), cold_start=False)
        pred = model.predict(sample_book, Decimal("1.0"), Decimal("1000"), Decimal("0.01"))
        assert pred.lower >= Decimal("0")

    def test_model_type_is_correct(self, sample_book):
        model = CEXOrderbookSlippage()
        pred = model.predict(sample_book, Decimal("1.0"), Decimal("1000"), Decimal("0.01"))
        assert pred.model_type == "cex_sqrt_power_law"

    def test_empty_book_raises(self):
        book = OrderBook(symbol="BTC/USDT", exchange="binance")
        model = CEXOrderbookSlippage()
        with pytest.raises(ValueError, match="Empty"):
            model.predict(book, Decimal("1.0"), Decimal("1000"), Decimal("0.01"))

    def test_zero_adv_raises(self, sample_book):
        model = CEXOrderbookSlippage()
        with pytest.raises(ValueError, match="ADV"):
            model.predict(sample_book, Decimal("1.0"), Decimal("0"), Decimal("0.01"))

    def test_slippage_increases_with_size(self, sample_book):
        model = CEXOrderbookSlippage(k=Decimal("1.0"), cold_start=False)
        pred_small = model.predict(sample_book, Decimal("10"), Decimal("1000"), Decimal("0.01"))
        pred_large = model.predict(sample_book, Decimal("100"), Decimal("1000"), Decimal("0.01"))
        assert pred_large.expected > pred_small.expected


class TestPowerLawDecay:
    def test_impact_decay_at_t_zero_unchanged(self):
        model = CEXOrderbookSlippage()
        decay = model.impact_decay(1.0, t=0.0, t_0=60.0, gamma=0.5)
        assert decay == pytest.approx(1.0)

    def test_impact_decay_at_t0_equals_1_over_sqrt2(self):
        model = CEXOrderbookSlippage()
        # At t=t_0: (1 + 1)^(-0.5) = 2^(-0.5) = 1/sqrt(2)
        decay = model.impact_decay(1.0, t=60.0, t_0=60.0, gamma=0.5)
        assert abs(decay - 1.0 / math.sqrt(2)) < 1e-9

    def test_impact_decay_is_not_exponential(self):
        """Power-law decays slower than exponential at large t."""
        model = CEXOrderbookSlippage()
        t = 600.0  # 10 minutes
        power_law = model.impact_decay(1.0, t=t, t_0=60.0, gamma=0.5)
        exponential = math.exp(-0.5 * t / 60.0)
        # Power law (1+10)^(-0.5) ≈ 0.302; exponential ≈ 0.007
        assert power_law > exponential

    def test_impact_decay_monotone_decreasing(self):
        model = CEXOrderbookSlippage()
        d0 = model.impact_decay(1.0, t=0)
        d1 = model.impact_decay(1.0, t=60)
        d2 = model.impact_decay(1.0, t=300)
        assert d0 > d1 > d2

    def test_impact_decay_scales_with_impact_0(self):
        model = CEXOrderbookSlippage()
        d1 = model.impact_decay(1.0, t=60.0)
        d2 = model.impact_decay(2.0, t=60.0)
        assert d2 == pytest.approx(d1 * 2.0)

    def test_cross_venue_impact_at_t_zero(self):
        model = CEXOrderbookSlippage()
        # alpha_AB=0.5, impact_A=1.0, t=0 → 0.5 * 1.0 * 1^(-0.5) = 0.5
        impact_b = model.cross_venue_impact(1.0, t=0.0, alpha_ab=0.5)
        assert impact_b == pytest.approx(0.5)

    def test_cross_venue_impact_decays_with_time(self):
        model = CEXOrderbookSlippage()
        impact_t0 = model.cross_venue_impact(1.0, t=0.0, alpha_ab=0.5)
        impact_t60 = model.cross_venue_impact(1.0, t=60.0, alpha_ab=0.5)
        assert impact_t60 < impact_t0

    def test_cross_venue_impact_scales_with_alpha(self):
        model = CEXOrderbookSlippage()
        impact_half = model.cross_venue_impact(1.0, t=0.0, alpha_ab=0.5)
        impact_full = model.cross_venue_impact(1.0, t=0.0, alpha_ab=1.0)
        assert impact_full == pytest.approx(impact_half * 2.0)


class TestSlippageModelProtocol:
    def test_cex_slippage_implements_protocol(self):
        model = CEXOrderbookSlippage()
        assert isinstance(model, SlippageModel)
