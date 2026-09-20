"""Model validation framework for ML pipeline governance.

Implements advanced model validation techniques including Combinatorially
Symmetric Cross-Validation (CSCV), Deflated Sharpe Ratio (DSR), and
Walk-Forward Optimization (WFO) to ensure robust model selection and
prevent overfitting in the ML-driven trading system.
"""

__all__ = [
    "ValidationError",
    "WFOResult",
]

from dataclasses import dataclass
from typing import Any, Callable

import numpy as np
import structlog
from scipy import stats

from nehanth.core.exceptions import FTMOBaseError

try:
    from opentelemetry import metrics
    meter = metrics.get_meter(__name__)
    OTEL_AVAILABLE = True
except (ImportError, RuntimeError):
    OTEL_AVAILABLE = False
    meter = None

logger = structlog.get_logger(__name__)

# OTel metrics - only create if available
if OTEL_AVAILABLE and meter:
    validation_duration_histogram = meter.create_histogram(
        "ml.validation.duration_ms",
        description="Duration of model validation operations in milliseconds",
        unit="ms",
    )
else:
    validation_duration_histogram = None


class ValidationError(FTMOBaseError):
    """Raised when model validation fails or produces invalid results."""

    def __init__(self, message: str, **context: Any) -> None:
        """Initialize validation error.

        Args:
            message: Description of the validation failure.
            **context: Additional context data.
        """
        super().__init__(message, error_type="validation", **context)


@dataclass
class WFOResult:
    """Walk-Forward Optimization result.

    Attributes:
        windows: List of (train_start, train_end, test_start, test_end) tuples.
        oos_sharpe_ratios: Out-of-sample Sharpe ratio for each test window.
        avg_oos_sharpe: Average out-of-sample Sharpe ratio across all windows.
        is_promoted: Whether model meets promotion threshold (> 0.50).
    """

    windows: list[tuple[int, int, int, int]]
    oos_sharpe_ratios: list[float]
    avg_oos_sharpe: float
    is_promoted: bool


def compute_cscv_pbo(performance_matrix: np.ndarray, n_subsets: int = 16) -> float:
    """Compute Probability of Backtest Overfitting via CSCV.

    Combinatorially Symmetric Cross-Validation partitions the performance
    matrix into S subsets and evaluates all C(S, S/2) combinations. A model
    is considered overfit when the in-sample optimal configuration
    underperforms the out-of-sample median.

    Args:
        performance_matrix: 2D array where rows are configurations,
                          columns are performance metrics or time periods.
                          Shape: (n_configs, n_periods)
        n_subsets: Number of subsets for partitioning (default 16).

    Returns:
        PBO value in range [0, 1]. Threshold: PBO < 0.30 indicates
        acceptable overfitting levels.

    Raises:
        ValidationError: If performance_matrix is invalid.

    Example:
        >>> perf = np.random.randn(100, 252)  # 100 configs, 252 daily returns
        >>> pbo = compute_cscv_pbo(perf, n_subsets=8)
        >>> if pbo < 0.30:
        ...     print("Model acceptable - low overfitting risk")
    """
    if performance_matrix.ndim != 2:
        raise ValidationError(
            "performance_matrix must be 2D",
            shape=performance_matrix.shape,
        )

    n_configs, n_periods = performance_matrix.shape
    if n_subsets > n_periods:
        raise ValidationError(
            "n_subsets cannot exceed number of periods",
            n_subsets=n_subsets,
            n_periods=n_periods,
        )

    logger.info(
        "compute_cscv_pbo_start",
        n_configs=n_configs,
        n_periods=n_periods,
        n_subsets=n_subsets,
    )

    # Partition into subsets
    subset_size = n_periods // n_subsets
    remainder = n_periods % n_subsets

    subsets = []
    start_idx = 0
    for i in range(n_subsets):
        end_idx = start_idx + subset_size + (1 if i < remainder else 0)
        subsets.append(performance_matrix[:, start_idx:end_idx])
        start_idx = end_idx

    # Compute all C(n_subsets, n_subsets/2) combinations
    from itertools import combinations

    subset_indices = list(range(n_subsets))
    half_size = n_subsets // 2
    combinations_list = list(combinations(subset_indices, half_size))

    if not combinations_list:
        logger.warning("compute_cscv_pbo_no_combinations", n_subsets=n_subsets)
        return 0.0

    n_combinations = len(combinations_list)
    overfit_count = 0

    for combo in combinations_list:
        # In-sample: selected subsets
        is_indices = list(combo)
        is_perf = np.hstack([subsets[i] for i in is_indices])

        # Out-of-sample: remaining subsets
        oos_indices = [i for i in subset_indices if i not in is_indices]
        oos_perf = np.hstack([subsets[i] for i in oos_indices])

        # Find config optimal in-sample
        is_means = np.mean(is_perf, axis=1)
        is_optimal_config = np.argmax(is_means)

        # Evaluate optimal config out-of-sample
        oos_means = np.mean(oos_perf, axis=1)
        oos_optimal_mean = np.median(oos_means)

        if oos_means[is_optimal_config] < oos_optimal_mean:
            overfit_count += 1

    pbo = overfit_count / n_combinations
    logger.info("compute_cscv_pbo_complete", pbo=pbo, combinations_evaluated=n_combinations)
    return pbo


def compute_deflated_sharpe(
    observed_sr: float,
    sr_std: float,
    num_trials: int,
    skewness: float,
    kurtosis: float,
    sample_size: int,
) -> float:
    """Compute Deflated Sharpe Ratio correcting for multiple testing.

    The DSR adjusts the observed Sharpe ratio for:
    - Multiple testing bias (many strategy trials)
    - Non-normality (skewness and kurtosis)
    - Sample length effects

    Formula: DSR = Phi((SR_hat - SR_0) / sigma_SR)

    Where SR_0 is the false discovery rate adjusted threshold.

    Args:
        observed_sr: Observed Sharpe ratio from backtest.
        sr_std: Standard deviation of Sharpe ratio estimate.
        num_trials: Number of independent trials/configurations tested.
        skewness: Return distribution skewness.
        kurtosis: Return distribution kurtosis (excess kurtosis).
        sample_size: Number of observations (e.g., trading days).

    Returns:
        DSR value. Threshold: DSR > 0.95 indicates statistically
        significant alpha (p-value < 0.05 after multiple testing correction).

    Raises:
        ValidationError: If inputs are invalid.

    Example:
        >>> sr = 1.2
        >>> sr_std = 0.15
        >>> dsr = compute_deflated_sharpe(sr, sr_std, 100, 0.5, 3.5, 252)
        >>> if dsr > 0.95:
        ...     print("Statistically significant alpha detected")
    """
    if sr_std <= 0:
        raise ValidationError("sr_std must be positive", sr_std=sr_std)
    if num_trials < 1:
        raise ValidationError("num_trials must be >= 1", num_trials=num_trials)
    if sample_size < 2:
        raise ValidationError("sample_size must be >= 2", sample_size=sample_size)

    logger.info(
        "compute_deflated_sharpe_start",
        observed_sr=observed_sr,
        num_trials=num_trials,
        sample_size=sample_size,
    )

    # Adjust Sharpe for non-normality
    # M[SR] = SR_hat - skewness/sample_size * Phi^-1(1 - 1/(2*num_trials))
    z_val = stats.norm.ppf(1.0 - 1.0 / (2.0 * num_trials))
    sr_adjustment = (skewness / np.sqrt(sample_size)) * z_val

    # Adjust standard error for kurtosis
    # sigma_SR = sqrt((1 - skew*SR_0 + (kurt-1)/4 * SR_0^2) / (T-1))
    # Simplified: sigma_SR = sr_std * sqrt(1 + (kurtosis-3)/4 * sr_std^2 / sample_size)
    kurtosis_adj = (kurtosis - 3.0) / 4.0  # Normalize for excess kurtosis
    variance_adjustment = 1.0 + (kurtosis_adj * sr_std**2) / sample_size

    # Guard against negative variance (numerical stability)
    if variance_adjustment <= 0:
        variance_adjustment = 1.0

    se_adjustment = np.sqrt(variance_adjustment)
    adjusted_sr_std = sr_std * se_adjustment

    # Guard against zero division in DSR calculation
    if adjusted_sr_std <= 0:
        logger.warning("adjusted_sr_std is zero or negative, clamping to prevent division by zero")
        adjusted_sr_std = 1e-10

    # Deflated Sharpe
    adjusted_sr = observed_sr - sr_adjustment
    dsr = stats.norm.cdf(adjusted_sr / adjusted_sr_std)

    logger.info(
        "compute_deflated_sharpe_complete",
        dsr=dsr,
        adjusted_sr=adjusted_sr,
        adjusted_sr_std=adjusted_sr_std,
    )
    return dsr


def walk_forward_validation(
    model: Any,
    data: np.ndarray,
    train_window: int = 252,
    test_window: int = 63,
    step: int | None = None,
) -> WFOResult:
    """Perform Walk-Forward Optimization with rolling windows.

    Implements a rolling window approach preferred for FX markets where
    regimes change frequently. Unlike expanding windows, rolling windows
    maintain recent data relevance and avoid dilution of signal.

    Args:
        model: Fitted model with predict method returning returns/scores.
        data: 1D array of returns or price data. Shape: (n_periods,)
        train_window: Size of training window in periods (default 252 days).
        test_window: Size of test window in periods (default 63 days).
        step: Step size for rolling window (default: test_window).

    Returns:
        WFOResult containing windows, OOS Sharpe ratios, and promotion status.

    Raises:
        ValidationError: If parameters are invalid or model fails.

    Example:
        >>> returns = np.random.randn(1000) * 0.01
        >>> result = walk_forward_validation(model, returns, train_window=252)
        >>> print(f"Average OOS Sharpe: {result.avg_oos_sharpe}")
        >>> if result.is_promoted:
        ...     print("Model passed WFO promotion gate")
    """
    if data.ndim != 1:
        raise ValidationError("data must be 1D array", shape=data.shape)
    if len(data) < train_window + test_window:
        raise ValidationError(
            "Insufficient data for WFO",
            data_length=len(data),
            required=train_window + test_window,
        )
    if train_window < 1 or test_window < 1:
        raise ValidationError(
            "train_window and test_window must be positive",
            train_window=train_window,
            test_window=test_window,
        )

    if step is None:
        step = test_window

    logger.info(
        "walk_forward_validation_start",
        data_length=len(data),
        train_window=train_window,
        test_window=test_window,
        step=step,
    )

    windows = []
    oos_sharpe_ratios = []

    train_start = 0
    while train_start + train_window + test_window <= len(data):
        train_end = train_start + train_window
        test_start = train_end
        test_end = test_start + test_window

        train_data = data[train_start:train_end]
        test_data = data[test_start:test_end]

        # Train model (assumed to have fit/predict interface)
        try:
            if hasattr(model, "fit"):
                model.fit(train_data)
            predictions = model.predict(test_data)
        except Exception as e:
            raise ValidationError(
                "Model prediction failed",
                window=(test_start, test_end),
                error=str(e),
            )

        # Compute out-of-sample Sharpe ratio
        if isinstance(predictions, np.ndarray) and predictions.size > 0:
            mean_ret = np.mean(predictions)
            std_ret = np.std(predictions)
            oos_sharpe = (mean_ret / std_ret) if std_ret > 0 else 0.0
        else:
            oos_sharpe = 0.0

        windows.append((train_start, train_end, test_start, test_end))
        oos_sharpe_ratios.append(oos_sharpe)

        train_start += step

    if not oos_sharpe_ratios:
        raise ValidationError(
            "No WFO windows generated",
            data_length=len(data),
            train_window=train_window,
            test_window=test_window,
        )

    avg_oos_sharpe = float(np.mean(oos_sharpe_ratios))
    is_promoted = avg_oos_sharpe > 0.50

    logger.info(
        "walk_forward_validation_complete",
        num_windows=len(windows),
        avg_oos_sharpe=avg_oos_sharpe,
        is_promoted=is_promoted,
    )

    return WFOResult(
        windows=windows,
        oos_sharpe_ratios=oos_sharpe_ratios,
        avg_oos_sharpe=avg_oos_sharpe,
        is_promoted=is_promoted,
    )
