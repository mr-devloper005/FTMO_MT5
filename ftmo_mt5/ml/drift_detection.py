"""Drift detection and monitoring for deployed ML models.

Monitors data distribution shifts and abrupt regime changes using
Population Stability Index (PSI) and ADWIN detector, enabling
proactive model retraining when drift is detected.
"""

__all__ = [
    "DriftDetectionError",
    "PSIResult",
    "DriftReport",
    "PSIDriftMonitor",
    "ADWINDetector",
    "ComprehensiveDriftMonitor",
]

from collections import deque
from dataclasses import dataclass
from typing import Any

import numpy as np
import structlog

from ftmo_mt5.core.exceptions import FTMOBaseError

logger = structlog.get_logger(__name__)


class DriftDetectionError(FTMOBaseError):
    """Raised when drift detection fails or produces invalid results."""

    def __init__(self, message: str, **context: Any) -> None:
        """Initialize drift detection error.

        Args:
            message: Description of the detection failure.
            **context: Additional context data.
        """
        super().__init__(message, error_type="drift_detection", **context)


@dataclass
class PSIResult:
    """Population Stability Index result.

    Attributes:
        psi_value: PSI value indicating distribution shift magnitude.
        is_stable: Whether distribution is stable (PSI < threshold).
        action: Recommended action ("continue", "investigate", "retrain").
    """

    psi_value: float
    is_stable: bool
    action: str


@dataclass
class DriftReport:
    """Comprehensive drift monitoring report.

    Attributes:
        psi_value: Population Stability Index from PSI monitor.
        adwin_triggered: Whether ADWIN detected abrupt change.
        regime_stable: Overall regime stability assessment.
        recommended_action: Action to take ("continue", "monitor", "retrain").
    """

    psi_value: float
    adwin_triggered: bool
    regime_stable: bool
    recommended_action: str


def compute_psi(
    expected: np.ndarray,
    actual: np.ndarray,
    n_bins: int = 10,
) -> float:
    """Compute Population Stability Index.

    PSI measures the shift in distribution of a variable between two periods:
    - Baseline (expected): training/reference distribution
    - Current (actual): recent distribution

    Formula: PSI = sum((actual_pct - expected_pct) * ln(actual_pct / expected_pct))

    Args:
        expected: Baseline distribution (e.g., training period returns).
                 Shape: (n_samples,)
        actual: Current distribution (e.g., recent returns). Shape: (n_samples,)
        n_bins: Number of bins for discretizing distributions (default 10).

    Returns:
        PSI value in range [0, infinity]:
        - < 0.10: Stable, no action needed
        - 0.10-0.20: Investigate further
        - > 0.20: Significant shift, consider retraining

    Raises:
        DriftDetectionError: If inputs are invalid.

    Example:
        >>> baseline_returns = np.random.randn(252)
        >>> recent_returns = np.random.randn(63) * 1.5  # Shifted distribution
        >>> psi = compute_psi(baseline_returns, recent_returns)
        >>> if psi > 0.20:
        ...     print("Significant drift detected - retrain model")
    """
    if expected.ndim != 1 or actual.ndim != 1:
        raise DriftDetectionError(
            "Input arrays must be 1D",
            expected_shape=expected.shape,
            actual_shape=actual.shape,
        )

    if len(expected) < 2 or len(actual) < 2:
        raise DriftDetectionError(
            "Minimum 2 samples required",
            expected_len=len(expected),
            actual_len=len(actual),
        )

    if n_bins < 2:
        raise DriftDetectionError("n_bins must be >= 2", n_bins=n_bins)

    logger.info(
        "compute_psi_start",
        expected_len=len(expected),
        actual_len=len(actual),
        n_bins=n_bins,
    )

    # Define bins using quantiles of combined data
    combined = np.concatenate([expected, actual])
    bin_edges = np.percentile(combined, np.linspace(0, 100, n_bins + 1))

    # Ensure unique edges
    bin_edges = np.unique(bin_edges)
    if len(bin_edges) < 3:
        # Fall back to equal-width bins if percentile method produces duplicates
        bin_edges = np.linspace(combined.min(), combined.max(), n_bins + 1)

    # Digitize into bins
    expected_binned = np.digitize(expected, bin_edges, right=False)
    actual_binned = np.digitize(actual, bin_edges, right=False)

    # Compute bin counts and percentages.
    # digitize(right=False) yields indices 0..len(bin_edges) inclusive — values
    # beyond the last edge land in that final bucket. bincount only pads UP to
    # minlength, so whichever array happens to hold an outlier came back one
    # element longer, and the subtraction below failed to broadcast. Pin both
    # to the same width.
    n_buckets = len(bin_edges) + 1
    expected_counts = np.bincount(expected_binned, minlength=n_buckets)[:n_buckets]
    actual_counts = np.bincount(actual_binned, minlength=n_buckets)[:n_buckets]

    expected_pct = expected_counts / len(expected)
    actual_pct = actual_counts / len(actual)

    # Avoid log(0) - add small epsilon BEFORE division
    epsilon = 1e-10
    expected_pct = np.clip(expected_pct, epsilon, 1.0)
    actual_pct = np.clip(actual_pct, epsilon, 1.0)

    # Compute PSI, avoiding division by zero
    # Guard against zero denominators in the log ratio
    with np.errstate(divide='ignore', invalid='ignore'):
        psi_terms = (actual_pct - expected_pct) * np.log(actual_pct / expected_pct)
        # Replace any NaN or inf with 0 (from 0*log(0) or similar edge cases)
        psi_terms = np.nan_to_num(psi_terms, nan=0.0, posinf=0.0, neginf=0.0)
    psi = np.sum(psi_terms)

    logger.info("compute_psi_complete", psi=psi)
    return float(psi)


class PSIDriftMonitor:
    """Monitor data distribution drift using Population Stability Index.

    Maintains a rolling window of recent data and compares against the
    training (reference) distribution to detect gradual regime shifts.

    Attributes:
        reference_distribution: Baseline distribution from training data.
        window_size: Size of rolling window in observations.
        alert_threshold: PSI threshold for triggering alerts.
    """

    def __init__(
        self,
        reference_distribution: np.ndarray,
        window_size: int = 5,
        alert_threshold: float = 0.15,
    ) -> None:
        """Initialize PSI drift monitor.

        Args:
            reference_distribution: Training data to use as baseline.
                                   Shape: (n_samples,)
            window_size: Rolling window size in observations (default 5).
            alert_threshold: PSI threshold for investigation alert (default 0.15).

        Raises:
            DriftDetectionError: If reference_distribution is invalid.
        """
        if reference_distribution.ndim != 1:
            raise DriftDetectionError(
                "reference_distribution must be 1D",
                shape=reference_distribution.shape,
            )
        if len(reference_distribution) < 10:
            raise DriftDetectionError(
                "reference_distribution must have >= 10 samples",
                length=len(reference_distribution),
            )
        if window_size < 2:
            raise DriftDetectionError("window_size must be >= 2", window_size=window_size)
        if alert_threshold <= 0:
            raise DriftDetectionError(
                "alert_threshold must be positive",
                alert_threshold=alert_threshold,
            )

        self.reference_distribution = reference_distribution
        self.window_size = window_size
        self.alert_threshold = alert_threshold
        self.data_window: deque = deque(maxlen=window_size)

        logger.info(
            "psi_drift_monitor_initialized",
            ref_len=len(reference_distribution),
            window_size=window_size,
            alert_threshold=alert_threshold,
        )

    def update(self, new_data: np.ndarray) -> PSIResult:
        """Update monitor with new data and compute PSI.

        Args:
            new_data: New observations to add to window.
                     Shape: (n_new,) or scalar.

        Returns:
            PSIResult with current PSI, stability status, and recommended action.

        Raises:
            DriftDetectionError: If new_data is invalid.
        """
        # Handle scalar input
        if np.isscalar(new_data):
            new_data = np.array([new_data])
        elif new_data.ndim != 1:
            raise DriftDetectionError(
                "new_data must be 1D or scalar",
                shape=getattr(new_data, "shape", "scalar"),
            )

        # Add to window
        for val in new_data:
            self.data_window.append(val)

        # Compute PSI if window is full
        if len(self.data_window) < self.window_size:
            return PSIResult(
                psi_value=0.0,
                is_stable=True,
                action="continue",
            )

        window_array = np.array(list(self.data_window))
        psi = compute_psi(self.reference_distribution, window_array)

        # Determine action based on PSI thresholds
        if psi < 0.10:
            is_stable = True
            action = "continue"
        elif psi < 0.20:
            is_stable = False
            action = "investigate"
        else:
            is_stable = False
            action = "retrain"

        logger.info(
            "psi_drift_monitor_update",
            psi=psi,
            is_stable=is_stable,
            action=action,
        )

        return PSIResult(psi_value=psi, is_stable=is_stable, action=action)


class ADWINDetector:
    """ADWIN (Adaptive Windowing) detector for real-time abrupt change detection.

    Simplified implementation detecting sudden shifts in data streams,
    useful for identifying regime changes in market conditions.

    Attributes:
        delta: Significance level for change detection (default 0.002).
        min_window_size: Minimum window size to maintain (default 10).
    """

    def __init__(self, delta: float = 0.002, min_window_size: int = 10) -> None:
        """Initialize ADWIN detector.

        Args:
            delta: Significance level, lower = more sensitive (default 0.002).
            min_window_size: Minimum observations before testing (default 10).

        Raises:
            DriftDetectionError: If parameters are invalid.
        """
        if not (0 < delta < 1):
            raise DriftDetectionError("delta must be in (0, 1)", delta=delta)
        if min_window_size < 2:
            raise DriftDetectionError(
                "min_window_size must be >= 2",
                min_window_size=min_window_size,
            )

        self.delta = delta
        self.min_window_size = min_window_size
        self.window: deque = deque()

        logger.info(
            "adwin_detector_initialized",
            delta=delta,
            min_window_size=min_window_size,
        )

    def update(self, value: float) -> bool:
        """Update detector with new value and check for drift.

        Args:
            value: New data point to process.

        Returns:
            True if drift (change) detected, False otherwise.
        """
        self.window.append(value)

        # Need minimum observations
        if len(self.window) < self.min_window_size:
            return False

        # Use Hoeffding bound for drift detection
        # Simplified: Check if mean of first half differs from second half
        window_array = np.array(list(self.window))
        mid = len(window_array) // 2

        if mid < 2:
            return False

        mean_first = np.mean(window_array[:mid])
        mean_second = np.mean(window_array[mid:])
        std_dev = np.std(window_array)

        if std_dev < 1e-10:
            return False

        # Hoeffding bound
        m = len(window_array)
        epsilon = np.sqrt((1.0 / (2 * m)) * np.log(2.0 / self.delta))
        threshold = epsilon * std_dev

        drift_detected = abs(mean_first - mean_second) > threshold

        if drift_detected:
            logger.warning(
                "adwin_drift_detected",
                mean_first=mean_first,
                mean_second=mean_second,
                threshold=threshold,
                window_size=len(window_array),
            )
            # Reset window on drift
            self.window.clear()

        return drift_detected


class ComprehensiveDriftMonitor:
    """Combines PSI and ADWIN for comprehensive drift monitoring.

    Uses both gradual (PSI) and abrupt (ADWIN) change detection for
    robust regime monitoring in trading systems.

    Attributes:
        psi_monitor: PSI-based drift detector.
        adwin_detector: ADWIN-based change detector.
    """

    def __init__(
        self,
        reference_distribution: np.ndarray,
        psi_window_size: int = 5,
        psi_threshold: float = 0.15,
        adwin_delta: float = 0.002,
    ) -> None:
        """Initialize comprehensive drift monitor.

        Args:
            reference_distribution: Training data baseline.
            psi_window_size: PSI rolling window size.
            psi_threshold: PSI alert threshold.
            adwin_delta: ADWIN significance level.

        Raises:
            DriftDetectionError: If parameters are invalid.
        """
        self.psi_monitor = PSIDriftMonitor(
            reference_distribution,
            window_size=psi_window_size,
            alert_threshold=psi_threshold,
        )
        self.adwin_detector = ADWINDetector(delta=adwin_delta)

        logger.info(
            "comprehensive_drift_monitor_initialized",
            psi_threshold=psi_threshold,
            adwin_delta=adwin_delta,
        )

    def update(self, new_data: np.ndarray) -> DriftReport:
        """Update monitors and generate comprehensive drift report.

        Args:
            new_data: New observations to analyze.

        Returns:
            DriftReport with PSI, ADWIN status, and recommended action.
        """
        # Handle scalar or array input
        if np.isscalar(new_data):
            new_data = np.array([new_data])
        elif new_data.ndim != 1:
            raise DriftDetectionError(
                "new_data must be 1D or scalar",
                shape=getattr(new_data, "shape", "scalar"),
            )

        # Update PSI monitor
        psi_result = self.psi_monitor.update(new_data)

        # Update ADWIN detector for each value
        adwin_triggered = False
        for val in new_data:
            if self.adwin_detector.update(val):
                adwin_triggered = True

        # Determine overall stability
        regime_stable = psi_result.is_stable and not adwin_triggered

        # Recommend action
        if adwin_triggered or psi_result.action == "retrain":
            recommended_action = "retrain"
        elif psi_result.action == "investigate":
            recommended_action = "monitor"
        else:
            recommended_action = "continue"

        logger.info(
            "comprehensive_drift_report",
            psi_value=psi_result.psi_value,
            adwin_triggered=adwin_triggered,
            regime_stable=regime_stable,
            recommended_action=recommended_action,
        )

        return DriftReport(
            psi_value=psi_result.psi_value,
            adwin_triggered=adwin_triggered,
            regime_stable=regime_stable,
            recommended_action=recommended_action,
        )
