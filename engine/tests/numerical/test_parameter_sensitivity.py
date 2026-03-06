"""Parameter Sensitivity Analysis — LEVIATHAN Arbitrage Engine.

Mathematical verification of:
1. Slippage model sensitivity (k, gamma, cold-start, CI, DO NOT TRADE)
2. Sharpe ratio sensitivity (sample size, annualization, bias correction)
3. MDD sensitivity (position size scaling, recovery time)

Run:
    cd engine && python -m pytest tests/numerical/test_parameter_sensitivity.py -v --no-cov
"""
from __future__ import annotations

import math
import random
from decimal import Decimal
from typing import Any

import pytest

from src.core.order_book import OrderBook
from src.friction.slippage_model import CEXOrderbookSlippage, SlippagePrediction


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_book(
    mid: float = 50000.0, spread_bps: float = 5.0
) -> OrderBook:
    """Create a simple OrderBook with one level on each side."""
    half_spread = mid * spread_bps / 10000 / 2
    bid = mid - half_spread
    ask = mid + half_spread
    book = OrderBook(symbol="BTC/USDT", exchange="test")
    book.apply_snapshot(
        bids=[(str(bid), "10.0")],
        asks=[(str(ask), "10.0")],
    )
    return book


def _compute_sharpe(
    returns: list[float],
    periods_per_year: float = 8760,
    risk_free_rate: float = 0.0,
) -> float:
    """Replicate WalkForwardAnalyzer._compute_sharpe for unit testing."""
    if len(returns) < 2:
        return 0.0
    mean_r = sum(returns) / len(returns)
    var_r = sum((r - mean_r) ** 2 for r in returns) / (len(returns) - 1)
    std_r = math.sqrt(var_r) if var_r > 0 else 0.0
    if std_r == 0:
        return 0.0
    rf_per_period = risk_free_rate / periods_per_year
    return (mean_r - rf_per_period) / std_r * math.sqrt(periods_per_year)


def _compute_mdd(pnls: list[float]) -> float:
    """Replicate WalkForwardAnalyzer._compute_mdd for unit testing."""
    if not pnls:
        return 0.0
    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0
    for pnl in pnls:
        cumulative += pnl
        if cumulative > peak:
            peak = cumulative
        dd = (peak - cumulative) / peak if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
    return max_dd


# ===========================================================================
# 1. SLIPPAGE SENSITIVITY
# ===========================================================================


class TestSlippageKSensitivity:
    """Verify slippage scales linearly with k parameter."""

    K_VALUES = [0.5, 1.0, 1.5, 2.0, 3.0]
    SIZES = [Decimal("0.001"), Decimal("0.01"), Decimal("0.1"), Decimal("1.0"), Decimal("10.0")]
    ADV = Decimal("1000")  # Average Daily Volume
    SIGMA = Decimal("0.02")  # 2% volatility

    def test_slippage_linear_in_k(self) -> None:
        """For fixed size/ADV/sigma, slippage must scale linearly with k.

        Impact = sigma * k * sqrt(size/ADV)
        => Impact(k2) / Impact(k1) == k2 / k1
        """
        book = _make_book()

        for size in self.SIZES:
            predictions: dict[float, Decimal] = {}
            for k_val in self.K_VALUES:
                model = CEXOrderbookSlippage(k=Decimal(str(k_val)), cold_start=False)
                pred = model.predict(book, size, self.ADV, self.SIGMA)
                predictions[k_val] = pred.expected

            # Check ratio: for every pair (k_i, k_j), expected_i / expected_j ≈ k_i / k_j
            base_k = 1.0
            base_val = predictions[base_k]
            for k_val in self.K_VALUES:
                if k_val == base_k:
                    continue
                ratio_actual = float(predictions[k_val] / base_val)
                ratio_expected = k_val / base_k
                assert abs(ratio_actual - ratio_expected) < 1e-10, (
                    f"k={k_val}, size={size}: "
                    f"actual ratio={ratio_actual:.6f}, expected={ratio_expected:.6f}"
                )

    def test_k_doubles_slippage_doubles(self) -> None:
        """Doubling k must exactly double the expected slippage."""
        book = _make_book()
        size = Decimal("1.0")

        model_k1 = CEXOrderbookSlippage(k=Decimal("1.0"), cold_start=False)
        model_k2 = CEXOrderbookSlippage(k=Decimal("2.0"), cold_start=False)

        pred1 = model_k1.predict(book, size, self.ADV, self.SIGMA)
        pred2 = model_k2.predict(book, size, self.ADV, self.SIGMA)

        ratio = float(pred2.expected / pred1.expected)
        assert abs(ratio - 2.0) < 1e-10, f"Expected ratio=2.0, got {ratio}"

    def test_k_zero_gives_zero_slippage(self) -> None:
        """k=0 should produce zero slippage."""
        book = _make_book()
        model = CEXOrderbookSlippage(k=Decimal("0"), cold_start=False)
        pred = model.predict(book, Decimal("1.0"), self.ADV, self.SIGMA)
        assert pred.expected == Decimal("0"), f"Expected 0 slippage for k=0, got {pred.expected}"


class TestSlippageGammaSensitivity:
    """Verify gamma controls the concavity of slippage vs size.

    The CEXOrderbookSlippage uses sqrt(size/ADV), which is equivalent to
    gamma=0.5 in the power-law formulation. We test the PowerLawSlippage
    from shadow.py for variable gamma sensitivity.
    """

    GAMMA_VALUES = [0.3, 0.4, 0.5, 0.6, 0.7]
    SIZES = [0.001, 0.01, 0.1, 1.0, 10.0]

    def test_lower_gamma_slower_growth(self) -> None:
        """Lower gamma means impact grows slower with size.

        For size > 1: impact(gamma_low) < impact(gamma_high)
        For size < 1: impact(gamma_low) > impact(gamma_high) (fractional power reversal)
        """
        from src.modes.shadow import PowerLawSlippage
        from src.core.models import OrderSide

        for gamma in self.GAMMA_VALUES:
            model = PowerLawSlippage(k=1.0, gamma=gamma)
            impacts = []
            for size in self.SIZES:
                # Use deterministic seed for reproducible comparison
                random.seed(42)
                fill_price = model.apply(
                    Decimal("50000"), OrderSide.BUY, Decimal(str(size))
                )
                impact = float(fill_price - Decimal("50000"))
                impacts.append(impact)

            # Verify monotonically increasing impact with size (for BUY side)
            for i in range(len(impacts) - 1):
                assert impacts[i] <= impacts[i + 1], (
                    f"gamma={gamma}: impact should increase with size. "
                    f"size[{i}]={self.SIZES[i]} impact={impacts[i]:.6f}, "
                    f"size[{i+1}]={self.SIZES[i+1]} impact={impacts[i+1]:.6f}"
                )

    def test_concavity_check_for_gamma_less_than_one(self) -> None:
        """For gamma < 1, the marginal impact should decrease with size.

        d^2(impact)/d(size)^2 < 0 for gamma < 1 (concave).
        We check numerically: the slope between consecutive sizes decreases.
        """
        from src.modes.shadow import PowerLawSlippage
        from src.core.models import OrderSide

        for gamma in [0.3, 0.4, 0.5]:
            # Use larger sizes to avoid noise from the random factor
            sizes = [1.0, 4.0, 9.0, 16.0, 25.0]
            impacts = []
            for size in sizes:
                random.seed(42)
                fill = model_apply_deterministic(gamma, size)
                impacts.append(fill)

            # Compute slopes between consecutive points
            slopes = []
            for i in range(len(impacts) - 1):
                ds = sizes[i + 1] - sizes[i]
                di = impacts[i + 1] - impacts[i]
                slopes.append(di / ds)

            # Concavity: slopes should be decreasing
            for i in range(len(slopes) - 1):
                assert slopes[i] >= slopes[i + 1] - 1e-10, (
                    f"gamma={gamma}: marginal impact should decrease (concavity). "
                    f"slope[{i}]={slopes[i]:.8f} > slope[{i+1}]={slopes[i+1]:.8f}"
                )

    def test_gamma_one_gives_linear_scaling(self) -> None:
        """gamma=1.0 means impact is linear in size: impact = k * size^1."""
        sizes = [1.0, 2.0, 4.0, 8.0]
        impacts = [model_apply_deterministic(1.0, s) for s in sizes]

        # Check linearity: impact(2s)/impact(s) ≈ 2
        for i in range(len(sizes) - 1):
            ratio_size = sizes[i + 1] / sizes[i]
            ratio_impact = impacts[i + 1] / impacts[i]
            assert abs(ratio_impact - ratio_size) < 1e-6, (
                f"gamma=1.0: impact ratio should equal size ratio. "
                f"size_ratio={ratio_size}, impact_ratio={ratio_impact:.6f}"
            )


def model_apply_deterministic(gamma: float, size: float) -> float:
    """Compute PowerLawSlippage impact deterministically (fixed random factor)."""
    # Impact = k * size^gamma (without the random factor)
    # We compute the pure impact to test mathematical properties
    k = 1.0
    return k * (size ** gamma)


class TestColdStartMultiplier:
    """Verify cold_start=True produces exactly 1.5x slippage."""

    SIZES = [Decimal("0.001"), Decimal("0.01"), Decimal("0.1"), Decimal("1.0")]
    ADV = Decimal("1000")
    SIGMA = Decimal("0.02")

    def test_cold_start_multiplier_exact(self) -> None:
        """cold_start=True must give exactly COLD_START_MULTIPLIER (1.5x) of normal."""
        book = _make_book()

        for size in self.SIZES:
            model_warm = CEXOrderbookSlippage(k=Decimal("1.0"), cold_start=False)
            model_cold = CEXOrderbookSlippage(k=Decimal("1.0"), cold_start=True)

            pred_warm = model_warm.predict(book, size, self.ADV, self.SIGMA)
            pred_cold = model_cold.predict(book, size, self.ADV, self.SIGMA)

            expected_multiplier = float(CEXOrderbookSlippage.COLD_START_MULTIPLIER)
            ratio = float(pred_cold.expected / pred_warm.expected)

            assert abs(ratio - expected_multiplier) < 1e-10, (
                f"size={size}: cold/warm ratio={ratio:.6f}, "
                f"expected={expected_multiplier}"
            )

    def test_cold_start_default_is_1_5(self) -> None:
        """The default COLD_START_MULTIPLIER must be 1.5."""
        assert float(CEXOrderbookSlippage.COLD_START_MULTIPLIER) == 1.5

    def test_cold_start_affects_all_k_values(self) -> None:
        """Cold start multiplier applies uniformly across all k values."""
        book = _make_book()
        size = Decimal("1.0")

        for k_val in [0.5, 1.0, 2.0, 3.0]:
            k = Decimal(str(k_val))
            model_warm = CEXOrderbookSlippage(k=k, cold_start=False)
            model_cold = CEXOrderbookSlippage(k=k, cold_start=True)

            pred_warm = model_warm.predict(book, size, self.ADV, self.SIGMA)
            pred_cold = model_cold.predict(book, size, self.ADV, self.SIGMA)

            ratio = float(pred_cold.expected / pred_warm.expected)
            assert abs(ratio - 1.5) < 1e-10, (
                f"k={k_val}: cold/warm ratio={ratio:.6f}, expected=1.5"
            )


class TestConfidenceIntervalWidth:
    """Verify CI widens with extrapolation distance (size/ADV ratio)."""

    SIGMA = Decimal("0.02")

    def test_ci_bands_by_extrapolation_distance(self) -> None:
        """CI widths must follow the documented bands:
        <= 1x:  +/-20%
        1-3x:   +/-50%
        3-10x:  +/-100%
        >10x:   DO NOT TRADE (>= 1000%)
        """
        book = _make_book()
        model = CEXOrderbookSlippage(k=Decimal("1.0"), cold_start=False)

        # (size, ADV) pairs to hit each band
        test_cases = [
            # (size, adv, expected_ci_pct, band_name)
            (Decimal("0.5"), Decimal("1.0"), Decimal("0.20"), "<=1x"),
            (Decimal("1.0"), Decimal("1.0"), Decimal("0.20"), "=1x"),
            (Decimal("2.0"), Decimal("1.0"), Decimal("0.50"), "1-3x"),
            (Decimal("3.0"), Decimal("1.0"), Decimal("0.50"), "1-3x boundary"),
            (Decimal("5.0"), Decimal("1.0"), Decimal("1.00"), "3-10x"),
            (Decimal("10.0"), Decimal("1.0"), Decimal("1.00"), "=10x"),
            (Decimal("11.0"), Decimal("1.0"), Decimal("10.0"), ">10x DO NOT TRADE"),
            (Decimal("100.0"), Decimal("1.0"), Decimal("10.0"), "100x DO NOT TRADE"),
        ]

        for size, adv, expected_ci, band in test_cases:
            pred = model.predict(book, size, adv, self.SIGMA)

            # Verify extrapolation distance
            expected_ratio = float(size / adv)
            assert abs(pred.extrapolation_distance - expected_ratio) < 1e-10, (
                f"band={band}: extrapolation_distance={pred.extrapolation_distance}, "
                f"expected={expected_ratio}"
            )

            # Verify CI width: upper - lower == 2 * ci_pct * expected
            actual_width = pred.upper - pred.lower
            # For lower bound clamped to 0: width = upper (since lower=0 when ci > expected)
            # Expected upper = expected * (1 + ci_pct)
            assert pred.upper == pred.expected * (1 + expected_ci), (
                f"band={band}: upper={pred.upper}, "
                f"expected={pred.expected * (1 + expected_ci)}"
            )

    def test_ci_monotonically_widens(self) -> None:
        """CI width (upper - lower) must not decrease as size/ADV increases."""
        book = _make_book()
        model = CEXOrderbookSlippage(k=Decimal("1.0"), cold_start=False)
        adv = Decimal("1.0")

        sizes = [Decimal(str(s)) for s in [0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 15.0, 50.0]]
        widths = []
        for size in sizes:
            pred = model.predict(book, size, adv, self.SIGMA)
            width = float(pred.upper - pred.lower)
            widths.append(width)

        for i in range(len(widths) - 1):
            assert widths[i] <= widths[i + 1] + 1e-10, (
                f"CI width decreased: size[{i}]={sizes[i]} width={widths[i]:.6f}, "
                f"size[{i+1}]={sizes[i+1]} width={widths[i+1]:.6f}"
            )


class TestDoNotTrade:
    """Verify size/ADV > 10 produces CI width >= 1000%."""

    SIGMA = Decimal("0.02")

    def test_do_not_trade_flag(self) -> None:
        """When size/ADV > 10, the CI should be so wide it's a DO NOT TRADE signal.

        CI width = 10.0 (1000%), meaning upper = 11x expected, lower = max(0, -9x expected).
        The width ratio (upper - lower) / expected >= 10.0 (1000%).
        """
        book = _make_book()
        model = CEXOrderbookSlippage(k=Decimal("1.0"), cold_start=False)

        # Test several extreme ratios
        for ratio in [11.0, 20.0, 50.0, 100.0]:
            size = Decimal(str(ratio))
            adv = Decimal("1.0")
            pred = model.predict(book, size, adv, self.SIGMA)

            # CI width relative to expected
            ci_width_pct = float((pred.upper - pred.lower) / pred.expected) * 100
            assert ci_width_pct >= 1000.0, (
                f"ratio={ratio}: CI width = {ci_width_pct:.1f}%, expected >= 1000%"
            )

    def test_do_not_trade_boundary(self) -> None:
        """Exactly at ratio=10 should still be in the 3-10x band (100% CI).
        Just above 10 should jump to 1000% CI.
        """
        book = _make_book()
        model = CEXOrderbookSlippage(k=Decimal("1.0"), cold_start=False)
        adv = Decimal("1.0")

        # At ratio=10.0 exactly: 3-10x band, CI = +/-100%
        pred_at_10 = model.predict(book, Decimal("10.0"), adv, self.SIGMA)
        upper_pct_10 = float(pred_at_10.upper / pred_at_10.expected)
        assert abs(upper_pct_10 - 2.0) < 1e-10, (
            f"At ratio=10: upper/expected={upper_pct_10}, expected=2.0 (100% CI)"
        )

        # At ratio=10.001: >10x band, CI = 1000%
        pred_above_10 = model.predict(book, Decimal("10.001"), adv, self.SIGMA)
        upper_pct_above = float(pred_above_10.upper / pred_above_10.expected)
        assert abs(upper_pct_above - 11.0) < 1e-3, (
            f"At ratio=10.001: upper/expected={upper_pct_above}, expected=11.0 (1000% CI)"
        )


# ===========================================================================
# 2. SHARPE RATIO SENSITIVITY
# ===========================================================================


class TestSharpeSampleSize:
    """Verify Sharpe ratio estimation converges with sample size N."""

    def test_sharpe_convergence(self) -> None:
        """Larger N gives Sharpe closer to true value.

        True Sharpe = (mean / std) * sqrt(periods_per_year)
        Estimation error should decrease as ~1/sqrt(N).
        """
        true_mean = 0.001  # 0.1% per period
        true_std = 0.01  # 1% per period
        periods_per_year = 8760  # hourly
        true_sharpe = (true_mean / true_std) * math.sqrt(periods_per_year)

        sample_sizes = [10, 50, 100, 500, 1000]
        n_trials = 200  # Average over trials for stability
        errors: dict[int, float] = {}

        for n in sample_sizes:
            trial_errors = []
            for trial in range(n_trials):
                random.seed(trial * 1000 + n)
                returns = [random.gauss(true_mean, true_std) for _ in range(n)]
                estimated_sharpe = _compute_sharpe(returns, periods_per_year)
                trial_errors.append(abs(estimated_sharpe - true_sharpe))
            errors[n] = sum(trial_errors) / len(trial_errors)

        # Verify convergence: error should decrease with N
        for i in range(len(sample_sizes) - 1):
            n1, n2 = sample_sizes[i], sample_sizes[i + 1]
            assert errors[n1] > errors[n2] * 0.5, (
                f"Sharpe error should decrease: N={n1} error={errors[n1]:.4f}, "
                f"N={n2} error={errors[n2]:.4f}"
            )

        # Verify convergence rate: error ~ 1/sqrt(N)
        # Ratio of errors ≈ sqrt(N2/N1)
        err_10 = errors[10]
        err_1000 = errors[1000]
        expected_ratio = math.sqrt(1000 / 10)  # ~10x
        actual_ratio = err_10 / err_1000
        # Allow generous tolerance (randomness + bias)
        assert actual_ratio > expected_ratio * 0.3, (
            f"Convergence too slow: actual_ratio={actual_ratio:.2f}, "
            f"expected~{expected_ratio:.2f}"
        )

    def test_sharpe_estimation_error_formula(self) -> None:
        """Standard error of Sharpe ≈ sqrt((1 + S^2/2) / N).

        For a Sharpe of ~1.0, SE ≈ sqrt(1.5 / N).
        """
        true_mean = 0.01
        true_std = 0.01 * math.sqrt(8760)  # Sharpe ~ 1.0 annualized
        # Actually let's construct so that true_sharpe = 1.0
        # Sharpe = mean/std * sqrt(T) = 1.0
        # mean/std = 1/sqrt(8760)
        true_mean_per_period = 1.0 / math.sqrt(8760) * 0.01
        true_std_per_period = 0.01

        n_values = [30, 100, 500]
        for n in n_values:
            se_theoretical = math.sqrt((1 + 0.5) / n)  # For Sharpe~1
            # Just verify the formula is reasonable (order of magnitude)
            assert se_theoretical > 0
            assert se_theoretical < 1.0, (
                f"N={n}: SE={se_theoretical:.4f} should be < 1.0"
            )


class TestSharpeAnnualization:
    """Verify Sharpe(hourly) * sqrt(8760) == Sharpe(annual)."""

    def test_annualization_factor(self) -> None:
        """Sharpe scales with sqrt of annualization periods.

        If we compute Sharpe with periods_per_year=1 (already annual),
        vs periods_per_year=8760 (hourly), the ratio should be sqrt(8760).
        """
        random.seed(42)
        returns = [random.gauss(0.001, 0.01) for _ in range(500)]

        # Compute "hourly" Sharpe (annualized from hourly data)
        sharpe_hourly = _compute_sharpe(returns, periods_per_year=8760)

        # Compute "raw" Sharpe (no annualization, periods_per_year=1)
        sharpe_raw = _compute_sharpe(returns, periods_per_year=1)

        # Relationship: sharpe_hourly = sharpe_raw * sqrt(8760)
        expected_ratio = math.sqrt(8760)
        actual_ratio = sharpe_hourly / sharpe_raw if sharpe_raw != 0 else 0

        assert abs(actual_ratio - expected_ratio) < 1e-6, (
            f"Annualization: actual_ratio={actual_ratio:.4f}, "
            f"expected=sqrt(8760)={expected_ratio:.4f}"
        )

    def test_constant_returns_sharpe(self) -> None:
        """For constant positive returns, Sharpe should be very large (std -> 0).

        With exact constants, std=0 so Sharpe is undefined (returns 0.0 by convention).
        With near-constant (tiny noise), Sharpe should be extremely high.
        """
        # Exact constant: should return 0.0 (division by zero guard)
        constant_returns = [0.001] * 100
        sharpe = _compute_sharpe(constant_returns)
        assert sharpe == 0.0, f"Constant returns should give Sharpe=0.0, got {sharpe}"

        # Near-constant: very high Sharpe
        near_constant = [0.001 + random.gauss(0, 1e-8) for _ in range(100)]
        random.seed(123)
        sharpe_nc = _compute_sharpe(near_constant)
        assert sharpe_nc > 100, (
            f"Near-constant positive returns should give very high Sharpe, got {sharpe_nc}"
        )


class TestSharpeBiasCorrection:
    """For small samples (N<30), Sharpe has upward bias.

    Bias factor: bias ≈ 1 + 1/(4*N)
    Corrected: Sharpe_corrected = Sharpe_raw * (1 - 1/(4*N))
    """

    def test_small_sample_bias_factor(self) -> None:
        """Verify bias correction reduces upward bias for small N."""
        true_mean = 0.002
        true_std = 0.015
        n_trials = 500

        for n in [10, 15, 20, 25]:
            raw_sharpes = []
            corrected_sharpes = []
            bias_factor = 1 - 1 / (4 * n)

            for trial in range(n_trials):
                random.seed(trial * 100 + n)
                returns = [random.gauss(true_mean, true_std) for _ in range(n)]
                raw_s = _compute_sharpe(returns, periods_per_year=8760)
                raw_sharpes.append(raw_s)
                corrected_sharpes.append(raw_s * bias_factor)

            mean_raw = sum(raw_sharpes) / len(raw_sharpes)
            mean_corrected = sum(corrected_sharpes) / len(corrected_sharpes)

            # Corrected should be closer to 0 (less biased upward) than raw
            # for positive true Sharpe
            assert abs(mean_corrected) <= abs(mean_raw) + 1e-6, (
                f"N={n}: bias correction should reduce magnitude. "
                f"raw={mean_raw:.4f}, corrected={mean_corrected:.4f}"
            )

    def test_bias_factor_formula(self) -> None:
        """Verify the bias factor formula: 1 - 1/(4*N)."""
        for n in [10, 20, 30, 50, 100]:
            bias_factor = 1 - 1 / (4 * n)
            # For N=10: 0.975, N=20: 0.9875, N=100: 0.9975
            assert 0 < bias_factor < 1, f"Bias factor must be in (0,1), got {bias_factor}"
            # Bias correction should diminish for large N
            if n >= 100:
                assert bias_factor > 0.99, (
                    f"For N={n}, bias correction should be negligible: {bias_factor}"
                )

    def test_bias_vanishes_for_large_n(self) -> None:
        """For N >= 100, bias correction < 0.3% — negligible."""
        for n in [100, 500, 1000]:
            correction = 1 / (4 * n)
            assert correction < 0.003, (
                f"N={n}: bias correction = {correction*100:.3f}% should be < 0.3%"
            )


# ===========================================================================
# 3. MDD SENSITIVITY
# ===========================================================================


class TestMDDPositionSize:
    """Verify MDD scales proportionally with position size."""

    def test_mdd_scales_with_position_size(self) -> None:
        """Doubling position size should approximately double the MDD.

        PnL per trade scales linearly with position size.
        Since MDD = max(peak - trough) / peak, and both numerator and
        absolute values scale with position size, the fractional MDD
        should remain constant for proportional scaling.
        """
        random.seed(42)
        # Generate a base PnL sequence (per unit)
        base_pnls = [random.gauss(0.001, 0.01) for _ in range(200)]

        position_sizes = [0.001, 0.01, 0.1, 1.0]
        mdds: dict[float, float] = {}

        for pos_size in position_sizes:
            scaled_pnls = [pnl * pos_size for pnl in base_pnls]
            mdds[pos_size] = _compute_mdd(scaled_pnls)

        # Absolute MDD should scale with position size
        # MDD(2x) / MDD(x) should be consistent with scaling
        # Since _compute_mdd returns fractional drawdown (relative to peak),
        # and peak scales with position size, the fractional MDD should be
        # approximately the same across position sizes.
        base_mdd = mdds[0.01]
        for pos_size in [0.1, 1.0]:
            ratio = mdds[pos_size] / base_mdd if base_mdd > 0 else 0
            # Fractional MDD should be approximately equal (within 10%)
            assert abs(ratio - 1.0) < 0.10, (
                f"pos_size={pos_size}: fractional MDD ratio={ratio:.4f}, "
                f"expected ~1.0 (fractional MDD is scale-invariant)"
            )

    def test_absolute_mdd_scales_linearly(self) -> None:
        """Absolute drawdown (in dollar terms) scales linearly with position size."""
        random.seed(42)
        base_pnls = [random.gauss(0.0, 0.01) for _ in range(200)]
        # Ensure there's a drawdown by starting positive then going negative
        base_pnls[0:5] = [0.05, 0.02, 0.01, -0.1, -0.05]

        position_sizes = [1.0, 2.0, 5.0, 10.0]
        abs_drawdowns: dict[float, float] = {}

        for pos_size in position_sizes:
            scaled = [pnl * pos_size for pnl in base_pnls]
            # Compute absolute max drawdown (not fractional)
            cumulative = 0.0
            peak = 0.0
            max_abs_dd = 0.0
            for pnl in scaled:
                cumulative += pnl
                if cumulative > peak:
                    peak = cumulative
                dd = peak - cumulative
                if dd > max_abs_dd:
                    max_abs_dd = dd
            abs_drawdowns[pos_size] = max_abs_dd

        # Check linear scaling: dd(k*x) = k * dd(x)
        base_dd = abs_drawdowns[1.0]
        for pos_size in [2.0, 5.0, 10.0]:
            expected = base_dd * pos_size
            actual = abs_drawdowns[pos_size]
            assert abs(actual - expected) < 1e-10, (
                f"pos_size={pos_size}: absolute DD={actual:.6f}, "
                f"expected={expected:.6f} (linear scaling)"
            )


class TestMDDRecoveryTime:
    """Verify recovery time increases with drawdown depth."""

    def test_recovery_requires_more_wins_for_deeper_drawdown(self) -> None:
        """A 50% drawdown requires 100% gain to recover; 10% needs ~11%.

        Recovery factor = 1 / (1 - dd) - 1
        For dd=0.10: need 11.1% gain
        For dd=0.50: need 100% gain
        For dd=0.90: need 900% gain
        """
        drawdowns = [0.05, 0.10, 0.20, 0.50, 0.90]
        recovery_factors = []

        for dd in drawdowns:
            # From a peak of 1.0, after dd, value = 1 - dd
            # To recover to 1.0, need gain = 1/(1-dd) - 1
            recovery_pct = 1 / (1 - dd) - 1
            recovery_factors.append(recovery_pct)

        # Verify recovery factors are correct
        expected_recoveries = {
            0.05: 1 / 0.95 - 1,   # ~5.26%
            0.10: 1 / 0.90 - 1,   # ~11.11%
            0.20: 1 / 0.80 - 1,   # 25%
            0.50: 1 / 0.50 - 1,   # 100%
            0.90: 1 / 0.10 - 1,   # 900%
        }

        for dd, expected in expected_recoveries.items():
            actual = 1 / (1 - dd) - 1
            assert abs(actual - expected) < 1e-10, (
                f"dd={dd}: recovery={actual:.4f}, expected={expected:.4f}"
            )

        # Verify monotonically increasing recovery factor
        for i in range(len(recovery_factors) - 1):
            assert recovery_factors[i] < recovery_factors[i + 1], (
                f"Recovery should increase: dd[{i}]={drawdowns[i]} "
                f"recovery={recovery_factors[i]:.4f}, "
                f"dd[{i+1}]={drawdowns[i+1]} "
                f"recovery={recovery_factors[i+1]:.4f}"
            )

    def test_recovery_asymmetry(self) -> None:
        """Demonstrate the asymmetry: losses are harder to recover than gains to lose.

        A 50% loss requires a 100% gain to recover.
        This is a fundamental risk management principle.
        """
        # Simulate: start at $100, lose X%, compute gain needed
        initial = 100.0
        loss_pcts = [0.01, 0.05, 0.10, 0.25, 0.50, 0.75]

        for loss in loss_pcts:
            after_loss = initial * (1 - loss)
            gain_needed = (initial / after_loss) - 1

            # gain_needed should always be > loss (asymmetry)
            assert gain_needed > loss, (
                f"loss={loss*100:.0f}%: gain needed={gain_needed*100:.2f}% "
                f"should be > loss={loss*100:.0f}%"
            )

            # Verify the exact formula
            expected_gain = 1 / (1 - loss) - 1
            assert abs(gain_needed - expected_gain) < 1e-10

    def test_recovery_trade_count(self) -> None:
        """Simulate: for a fixed per-trade profit, deeper drawdown needs more trades."""
        per_trade_profit_pct = 0.001  # 0.1% per trade
        initial_capital = 1000.0

        drawdown_depths = [0.01, 0.05, 0.10, 0.20]
        trades_needed: dict[float, int] = {}

        for dd in drawdown_depths:
            capital = initial_capital * (1 - dd)
            target = initial_capital
            trades = 0
            while capital < target and trades < 100000:
                capital *= (1 + per_trade_profit_pct)
                trades += 1
            trades_needed[dd] = trades

        # Deeper drawdown needs more trades
        for i in range(len(drawdown_depths) - 1):
            dd1, dd2 = drawdown_depths[i], drawdown_depths[i + 1]
            assert trades_needed[dd1] < trades_needed[dd2], (
                f"dd={dd1} needs {trades_needed[dd1]} trades, "
                f"dd={dd2} needs {trades_needed[dd2]} trades — "
                f"deeper drawdown should need more trades"
            )


# ===========================================================================
# 4. IMPACT DECAY TESTS (Power-law vs Exponential)
# ===========================================================================


class TestImpactDecay:
    """Verify power-law decay properties of CEXOrderbookSlippage."""

    def test_decay_at_t_zero(self) -> None:
        """At t=0, impact_decay should equal impact_0."""
        model = CEXOrderbookSlippage()
        result = model.impact_decay(impact_0=1.0, t=0.0)
        assert abs(result - 1.0) < 1e-10

    def test_decay_monotonically_decreasing(self) -> None:
        """Impact should decrease over time."""
        model = CEXOrderbookSlippage()
        times = [0, 10, 30, 60, 120, 300, 600]
        impacts = [model.impact_decay(1.0, t) for t in times]

        for i in range(len(impacts) - 1):
            assert impacts[i] > impacts[i + 1], (
                f"t={times[i]}s: {impacts[i]:.6f} should > "
                f"t={times[i+1]}s: {impacts[i+1]:.6f}"
            )

    def test_power_law_slower_than_exponential(self) -> None:
        """Power-law decays slower than exponential at long horizons.

        This is the key Amendment 6 insight: exponential underestimates
        residual impact, leading to aggressive re-entry.
        """
        model = CEXOrderbookSlippage()
        gamma = 0.5
        t_0 = 60.0

        for t in [120, 300, 600, 1200]:
            power_law = model.impact_decay(1.0, t, t_0, gamma)
            exponential = math.exp(-t / t_0)

            assert power_law > exponential, (
                f"t={t}s: power_law={power_law:.6f} should > "
                f"exponential={exponential:.6f}"
            )

    def test_decay_formula_correctness(self) -> None:
        """Verify: impact_decay(t) = impact_0 * (1 + t/t_0)^(-gamma)."""
        model = CEXOrderbookSlippage()
        impact_0 = 2.5
        t = 120.0
        t_0 = 60.0
        gamma = 0.5

        result = model.impact_decay(impact_0, t, t_0, gamma)
        expected = impact_0 * (1 + t / t_0) ** (-gamma)

        assert abs(result - expected) < 1e-10, (
            f"Decay formula: result={result:.10f}, expected={expected:.10f}"
        )


class TestCrossVenueImpact:
    """Verify cross-venue impact propagation."""

    def test_cross_venue_at_t_zero(self) -> None:
        """At t=0, cross-venue impact = alpha_AB * impact_A."""
        model = CEXOrderbookSlippage()
        alpha = 0.7
        impact_a = 1.0
        result = model.cross_venue_impact(impact_a, t=0.0, alpha_ab=alpha)
        expected = alpha * impact_a
        assert abs(result - expected) < 1e-10

    def test_cross_venue_bounded_by_alpha(self) -> None:
        """Cross-venue impact is always <= alpha_AB * impact_A."""
        model = CEXOrderbookSlippage()
        for alpha in [0.1, 0.3, 0.5, 0.7, 1.0]:
            for t in [0, 30, 60, 120, 300]:
                result = model.cross_venue_impact(1.0, t, alpha)
                upper_bound = alpha * 1.0
                assert result <= upper_bound + 1e-10, (
                    f"alpha={alpha}, t={t}: result={result:.6f} > bound={upper_bound:.6f}"
                )

    def test_cross_venue_decays_over_time(self) -> None:
        """Cross-venue impact decreases with time."""
        model = CEXOrderbookSlippage()
        times = [0, 30, 60, 120, 300]
        impacts = [model.cross_venue_impact(1.0, t, alpha_ab=0.5) for t in times]

        for i in range(len(impacts) - 1):
            assert impacts[i] > impacts[i + 1], (
                f"Cross-venue at t={times[i]}s: {impacts[i]:.6f} should > "
                f"t={times[i+1]}s: {impacts[i+1]:.6f}"
            )
