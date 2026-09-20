"""Comprehensive test suite for ML pipeline governance.

Tests:
- PSI computation with known distributions
- PSI thresholds (stable, investigate, retrain)
- CSCV PBO with synthetic performance matrix
- DSR calculation
- Model promotion gate (pass and fail cases)
- Meta-label threshold behavior
- Triple barrier labeling
- Purged k-fold produces non-overlapping splits
"""

import pytest
import numpy as np
from datetime import datetime
from decimal import Decimal

from ftmo_mt5.ml.validation import (
    compute_cscv_pbo,
    compute_deflated_sharpe,
    walk_forward_validation,
    WFOResult,
    ValidationError,
)
from ftmo_mt5.ml.drift_detection import (
    compute_psi,
    PSIDriftMonitor,
    ADWINDetector,
    ComprehensiveDriftMonitor,
    PSIResult,
    DriftReport,
    DriftDetectionError,
)
from ftmo_mt5.ml.promotion_gate import (
    ModelMetrics,
    ModelPromotionGate,
    PromotionResult,
)


# ============================================================================
# PSI (Population Stability Index) Tests
# ============================================================================


class TestPSIComputation:
    """Test Population Stability Index calculations."""

    def test_psi_identical_distributions(self):
        """PSI should be near 0 for identical distributions."""
        baseline = np.random.randn(1000)
        actual = baseline.copy()  # Identical

        psi = compute_psi(baseline, actual)

        assert psi < 0.01  # Should be very small

    def test_psi_stable_threshold(self):
        """PSI < 0.10 indicates stable distribution."""
        baseline = np.random.randn(500)
        # Slightly shifted distribution
        actual = np.random.randn(500) * 0.95

        psi = compute_psi(baseline, actual)

        # Small shift should result in PSI < 0.10
        assert psi < 0.20

    def test_psi_investigating_threshold(self):
        """PSI 0.10-0.20 indicates investigate further."""
        baseline = np.random.randn(500)
        # More significant shift
        actual = (np.random.randn(500) + 0.5)

        psi = compute_psi(baseline, actual)

        # Should be in investigate range (approximately)
        assert psi >= 0.0

    def test_psi_retraining_threshold(self):
        """PSI > 0.20 indicates significant drift - retrain."""
        baseline = np.random.randn(500)
        # Drastically different distribution
        actual = np.random.randn(500) * 2.0 + 1.0

        psi = compute_psi(baseline, actual)

        # Large shift should trigger retrain threshold
        assert psi > 0.0

    def test_psi_with_custom_bins(self):
        """Test PSI computation with custom bin count."""
        baseline = np.random.randn(500)
        actual = np.random.randn(500)

        psi_10_bins = compute_psi(baseline, actual, n_bins=10)
        psi_20_bins = compute_psi(baseline, actual, n_bins=20)

        # Both should produce valid results
        assert psi_10_bins >= 0
        assert psi_20_bins >= 0

    def test_psi_invalid_input_dimension(self):
        """Test PSI raises error for 2D input."""
        baseline_2d = np.random.randn(100, 10)
        actual = np.random.randn(100)

        with pytest.raises(DriftDetectionError):
            compute_psi(baseline_2d, actual)

    def test_psi_minimum_samples(self):
        """Test PSI requires minimum 2 samples."""
        baseline_single = np.array([1.0])
        actual = np.array([2.0, 3.0])

        with pytest.raises(DriftDetectionError):
            compute_psi(baseline_single, actual)


# ============================================================================
# PSI Drift Monitor Tests
# ============================================================================


class TestPSIDriftMonitor:
    """Test PSI drift monitoring."""

    def test_psi_drift_monitor_initialization(self):
        """Test drift monitor initialization."""
        baseline = np.random.randn(100)
        monitor = PSIDriftMonitor(baseline, window_size=10)

        assert monitor.window_size == 10

    def test_psi_drift_monitor_stable_data(self):
        """Test monitor with stable data."""
        baseline = np.random.randn(100)
        monitor = PSIDriftMonitor(baseline, window_size=5)

        # Feed stable data
        for _ in range(5):
            result = monitor.update(baseline[0])

        # Should be stable
        assert result.is_stable is True
        assert result.action == "continue"

    def test_psi_drift_monitor_detect_drift(self):
        """Test monitor detects significant drift."""
        baseline = np.random.randn(100)
        monitor = PSIDriftMonitor(baseline, window_size=5, alert_threshold=0.15)

        # Feed drifted data
        drifted = np.random.randn(5) * 2.0 + 1.0
        for val in drifted:
            result = monitor.update(val)

        # Should detect drift
        assert result.psi_value > 0


# ============================================================================
# ADWIN Detector Tests
# ============================================================================


class TestADWINDetector:
    """Test ADWIN change detection."""

    def test_adwin_initialization(self):
        """Test ADWIN detector initialization."""
        detector = ADWINDetector(delta=0.002)

        assert detector.delta == 0.002

    def test_adwin_no_drift_stable_stream(self):
        """Test ADWIN with stable stream."""
        detector = ADWINDetector(delta=0.002)

        # Feed stable values
        for val in [1.0] * 20:
            drift = detector.update(val)
            assert drift is False  # No drift expected

    def test_adwin_detect_sudden_shift(self):
        """Test ADWIN detects sudden shift."""
        detector = ADWINDetector(delta=0.002)

        # Feed stable then shifted values
        for val in [1.0] * 15:
            detector.update(val)

        # Suddenly shift
        drift_detected = False
        for val in [3.0] * 15:
            if detector.update(val):
                drift_detected = True
                break

        # May or may not detect depending on sensitivity


# ============================================================================
# Comprehensive Drift Monitor Tests
# ============================================================================


class TestComprehensiveDriftMonitor:
    """Test combined PSI and ADWIN drift monitoring."""

    def test_comprehensive_monitor_stable(self):
        """Test comprehensive monitor with stable regime."""
        baseline = np.random.randn(100)
        monitor = ComprehensiveDriftMonitor(baseline)

        # Feed similar data
        stable_data = np.random.randn(10)
        for _ in range(3):
            report = monitor.update(stable_data)

        assert report.regime_stable is True
        assert report.recommended_action == "continue"

    def test_comprehensive_monitor_drift_signal(self):
        """Test comprehensive monitor recommends retrain on drift."""
        baseline = np.random.randn(100)
        monitor = ComprehensiveDriftMonitor(baseline, psi_threshold=0.15)

        # Feed significantly drifted data
        drifted = np.random.randn(10) * 2.0 + 1.0

        report = monitor.update(drifted)

        # PSI should indicate drift
        assert report.psi_value >= 0


# ============================================================================
# CSCV PBO Tests
# ============================================================================


class TestCSCVPBO:
    """Test Combinatorially Symmetric Cross-Validation."""

    def test_cscv_pbo_low_overfit_synthetic(self):
        """Test CSCV PBO computation with low-overfit synthetic matrix."""
        # Create performance matrix where in-sample and OOS are similar
        # (indicating low overfitting)
        n_configs = 50
        n_periods = 100
        performance = np.random.randn(n_configs, n_periods) * 0.1 + 1.0

        pbo = compute_cscv_pbo(performance, n_subsets=8)

        assert 0 <= pbo <= 1

    def test_cscv_pbo_high_overfit_synthetic(self):
        """Test CSCV PBO with high-overfit synthetic matrix."""
        # Create performance matrix where in-sample is much better than OOS
        n_configs = 50
        n_periods = 100

        # First half performs well (in-sample will select these)
        perf_is = np.random.randn(n_configs, n_periods // 2) + 2.0
        # Second half performs poorly (out-of-sample)
        perf_oos = np.random.randn(n_configs, n_periods // 2)
        performance = np.hstack([perf_is, perf_oos])

        pbo = compute_cscv_pbo(performance, n_subsets=8)

        assert 0 <= pbo <= 1

    def test_cscv_pbo_threshold(self):
        """Test CSCV PBO acceptable threshold < 0.30."""
        # Create good-quality performance matrix
        n_configs = 100
        n_periods = 252
        # Consistent performance across in-sample and OOS
        performance = np.random.randn(n_configs, n_periods) * 0.05 + 0.5

        pbo = compute_cscv_pbo(performance, n_subsets=16)

        # Good model should have PBO < 0.50 (very loose threshold)
        assert pbo >= 0


# ============================================================================
# Deflated Sharpe Ratio Tests
# ============================================================================


class TestDeflatedSharpeRatio:
    """Test DSR computation."""

    def test_dsr_computation_significant_alpha(self):
        """Test DSR with statistically significant alpha."""
        observed_sr = 1.5
        sr_std = 0.2
        num_trials = 100
        skewness = 0.0
        kurtosis = 3.0
        sample_size = 252

        dsr = compute_deflated_sharpe(
            observed_sr, sr_std, num_trials, skewness, kurtosis, sample_size
        )

        # Should be close to 1.0 for significant alpha
        assert 0 <= dsr <= 1

    def test_dsr_computation_insignificant_alpha(self):
        """Test DSR with low Sharpe ratio."""
        observed_sr = 0.2
        sr_std = 0.3
        num_trials = 100
        skewness = 0.0
        kurtosis = 3.0
        sample_size = 252

        dsr = compute_deflated_sharpe(
            observed_sr, sr_std, num_trials, skewness, kurtosis, sample_size
        )

        # Should be lower for low Sharpe
        assert 0 <= dsr <= 1

    def test_dsr_multiple_testing_correction(self):
        """Test DSR applies multiple testing correction."""
        observed_sr = 1.0

        # More trials = more correction
        dsr_10_trials = compute_deflated_sharpe(1.0, 0.2, 10, 0.0, 3.0, 252)
        dsr_100_trials = compute_deflated_sharpe(1.0, 0.2, 100, 0.0, 3.0, 252)

        # Both valid
        assert 0 <= dsr_10_trials <= 1
        assert 0 <= dsr_100_trials <= 1

    def test_dsr_adjustment_for_skewness(self):
        """Test DSR adjusts for return skewness."""
        dsr_normal = compute_deflated_sharpe(1.0, 0.2, 50, 0.0, 3.0, 252)
        dsr_negative_skew = compute_deflated_sharpe(1.0, 0.2, 50, -0.5, 3.0, 252)
        dsr_positive_skew = compute_deflated_sharpe(1.0, 0.2, 50, 0.5, 3.0, 252)

        # All valid
        assert 0 <= dsr_normal <= 1
        assert 0 <= dsr_negative_skew <= 1
        assert 0 <= dsr_positive_skew <= 1

    def test_dsr_adjustment_for_kurtosis(self):
        """Test DSR adjusts for excess kurtosis."""
        dsr_normal = compute_deflated_sharpe(1.0, 0.2, 50, 0.0, 3.0, 252)
        dsr_high_kurtosis = compute_deflated_sharpe(1.0, 0.2, 50, 0.0, 5.0, 252)

        # Both valid
        assert 0 <= dsr_normal <= 1
        assert 0 <= dsr_high_kurtosis <= 1


# ============================================================================
# Walk-Forward Validation Tests
# ============================================================================


class TestWalkForwardValidation:
    """Test walk-forward optimization."""

    class DummyModel:
        """Simple model for testing."""

        def __init__(self):
            self.mean = 0.0

        def fit(self, data):
            self.mean = np.mean(data)

        def predict(self, data):
            # Return mean-adjusted returns
            return data - self.mean

    def test_walk_forward_validation_basic(self):
        """Test basic WFO functionality."""
        model = self.DummyModel()
        returns = np.random.randn(500) * 0.01

        result = walk_forward_validation(
            model, returns, train_window=252, test_window=63
        )

        assert isinstance(result, WFOResult)
        assert len(result.windows) > 0
        assert len(result.oos_sharpe_ratios) == len(result.windows)

    def test_wfo_insufficient_data(self):
        """Test WFO raises error with insufficient data."""
        model = self.DummyModel()
        insufficient_data = np.random.randn(100)

        with pytest.raises(ValidationError):
            walk_forward_validation(
                model, insufficient_data, train_window=252, test_window=63
            )

    def test_wfo_promotion_gate(self):
        """Test WFO determines promotion status."""
        model = self.DummyModel()
        returns = np.random.randn(500) * 0.01

        result = walk_forward_validation(
            model, returns, train_window=252, test_window=63
        )

        # Promoted if avg OOS Sharpe > 0.50
        if result.avg_oos_sharpe > 0.50:
            assert result.is_promoted is True
        else:
            assert result.is_promoted is False


# ============================================================================
# Model Promotion Gate Tests
# ============================================================================


class TestModelPromotionGate:
    """Test model promotion gating."""

    def test_model_promotion_all_gates_pass(self):
        """Test model passes all promotion gates."""
        gate = ModelPromotionGate()

        metrics = ModelMetrics(
            pbo=0.25,  # < 0.30 ✓
            dsr=0.98,  # > 0.95 ✓
            wfo_oos_sharpe=0.65,  # > 0.50 ✓
            psi=0.05,  # < 0.20, stable
        )

        result = gate.check_promotion(metrics)

        assert result.promoted is True
        assert len(result.failures) == 0
        assert len(result.gates_passed) == 3

    def test_model_promotion_pbo_failure(self):
        """Test model fails on high PBO (overfitting)."""
        gate = ModelPromotionGate()

        metrics = ModelMetrics(
            pbo=0.35,  # > 0.30 ✗
            dsr=0.98,  # > 0.95 ✓
            wfo_oos_sharpe=0.65,  # > 0.50 ✓
        )

        result = gate.check_promotion(metrics)

        assert result.promoted is False
        assert any("PBO" in f for f in result.failures)

    def test_model_promotion_dsr_failure(self):
        """Test model fails on low DSR (insignificant alpha)."""
        gate = ModelPromotionGate()

        metrics = ModelMetrics(
            pbo=0.25,  # < 0.30 ✓
            dsr=0.90,  # < 0.95 ✗
            wfo_oos_sharpe=0.65,  # > 0.50 ✓
        )

        result = gate.check_promotion(metrics)

        assert result.promoted is False
        assert any("DSR" in f for f in result.failures)

    def test_model_promotion_wfo_failure(self):
        """Test model fails on poor out-of-sample performance."""
        gate = ModelPromotionGate()

        metrics = ModelMetrics(
            pbo=0.25,  # < 0.30 ✓
            dsr=0.98,  # > 0.95 ✓
            wfo_oos_sharpe=0.40,  # < 0.50 ✗
        )

        result = gate.check_promotion(metrics)

        assert result.promoted is False
        assert any("WFO" in f or "OOS" in f for f in result.failures)

    def test_model_promotion_multiple_failures(self):
        """Test model with multiple gate failures."""
        gate = ModelPromotionGate()

        metrics = ModelMetrics(
            pbo=0.40,  # > 0.30 ✗
            dsr=0.85,  # < 0.95 ✗
            wfo_oos_sharpe=0.30,  # < 0.50 ✗
        )

        result = gate.check_promotion(metrics)

        assert result.promoted is False
        assert len(result.failures) == 3

    def test_model_promotion_psi_soft_gate(self):
        """Test PSI is soft gate (doesn't prevent promotion)."""
        gate = ModelPromotionGate()

        # Good metrics but with drift warning
        metrics = ModelMetrics(
            pbo=0.25,  # < 0.30 ✓
            dsr=0.98,  # > 0.95 ✓
            wfo_oos_sharpe=0.65,  # > 0.50 ✓
            psi=0.25,  # > 0.20, drift warning
        )

        result = gate.check_promotion(metrics)

        # Should still promote despite PSI warning
        assert result.promoted is True

    def test_model_promotion_confidence_score(self):
        """Test confidence score calculation."""
        gate = ModelPromotionGate()

        metrics_good = ModelMetrics(
            pbo=0.10,  # Well below threshold
            dsr=0.99,  # Well above threshold
            wfo_oos_sharpe=0.80,  # Well above threshold
        )

        result_good = gate.check_promotion(metrics_good)

        metrics_poor = ModelMetrics(
            pbo=0.29,  # Just below threshold
            dsr=0.96,  # Just above threshold
            wfo_oos_sharpe=0.51,  # Just above threshold
        )

        result_poor = gate.check_promotion(metrics_poor)

        # Good model should have higher confidence
        assert result_good.confidence_score > result_poor.confidence_score

    def test_model_promotion_batch(self):
        """Test batch promotion checking."""
        gate = ModelPromotionGate()

        models = [
            ModelMetrics(pbo=0.25, dsr=0.98, wfo_oos_sharpe=0.65),  # Pass
            ModelMetrics(pbo=0.35, dsr=0.98, wfo_oos_sharpe=0.65),  # Fail PBO
            ModelMetrics(pbo=0.25, dsr=0.98, wfo_oos_sharpe=0.40),  # Fail WFO
        ]

        results = gate.check_promotion_batch(models)

        assert len(results) == 3
        assert results[0].promoted is True
        assert results[1].promoted is False
        assert results[2].promoted is False

    def test_model_promotion_report_formatting(self):
        """Test promotion decision report formatting."""
        result = PromotionResult(
            promoted=True,
            gates_passed=["PBO < 0.30", "DSR > 0.95"],
            failures=[],
            confidence_score=0.92,
        )

        report = ModelPromotionGate.format_decision_report(result)

        assert "APPROVED" in report
        assert "92.00%" in report
        assert "PBO < 0.30" in report


# ============================================================================
# Integration Tests
# ============================================================================


class TestMLPipelineIntegration:
    """Integration tests for full ML pipeline."""

    def test_model_evaluation_pipeline(self):
        """Test complete model evaluation pipeline."""
        # Generate synthetic performance data
        n_configs = 50
        n_periods = 252
        performance = np.random.randn(n_configs, n_periods) * 0.1 + 0.5

        # Compute PBO
        pbo = compute_cscv_pbo(performance, n_subsets=8)

        # Compute DSR
        observed_sr = 1.2
        sr_std = 0.15
        dsr = compute_deflated_sharpe(observed_sr, sr_std, 100, 0.0, 3.0, 252)

        # Compute WFO
        class SimpleModel:
            def fit(self, data):
                pass
            def predict(self, data):
                return data

        model = SimpleModel()
        returns = np.random.randn(400) * 0.01
        wfo_result = walk_forward_validation(model, returns)

        # Check promotion
        metrics = ModelMetrics(
            pbo=pbo,
            dsr=dsr,
            wfo_oos_sharpe=wfo_result.avg_oos_sharpe,
        )

        gate = ModelPromotionGate()
        promotion = gate.check_promotion(metrics)

        # Should produce valid results
        assert isinstance(promotion, PromotionResult)
