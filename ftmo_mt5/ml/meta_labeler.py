"""Meta-labeling and position sizing for ML-driven trading signals.

Implements triple barrier labeling for event-driven strategies, meta-labeling
to validate primary model signals, and position sizing scaled by meta-labeler
confidence. Also provides purged K-fold cross-validation to avoid lookahead bias.
"""

__all__ = [
    "MetaLabelingError",
    "MetaLabelResult",
    "TripleBarrierLabeler",
    "MetaLabeler",
    "PurgedKFold",
]

from dataclasses import dataclass
from typing import Any, Generator, Optional

import numpy as np
import pandas as pd
import structlog
from sklearn.base import BaseEstimator, clone
from sklearn.model_selection import KFold

from ftmo_mt5.core.exceptions import FTMOBaseError

logger = structlog.get_logger(__name__)


class MetaLabelingError(FTMOBaseError):
    """Raised when meta-labeling operations fail."""

    def __init__(self, message: str, **context: Any) -> None:
        """Initialize meta-labeling error.

        Args:
            message: Description of the failure.
            **context: Additional context data.
        """
        super().__init__(message, error_type="meta_labeling", **context)


@dataclass
class MetaLabelResult:
    """Meta-labeling prediction result.

    Attributes:
        probability: Confidence that primary signal is correct (0-1).
        should_trade: Whether to execute the trade based on meta-label.
        position_size_factor: Multiplicative factor for position sizing (0-1).
    """

    probability: float
    should_trade: bool
    position_size_factor: float


class TripleBarrierLabeler:
    """Triple barrier event labeling for signal-based strategies.

    Labels events based on which barrier is hit first:
    - Upper barrier (profit target): Label +1
    - Lower barrier (stop loss): Label -1
    - Vertical barrier (max holding period): Label 0

    Uses daily volatility for dynamic barrier sizing.

    Attributes:
        upper_mult: Multiplier for upper barrier (e.g., 2.0 * daily_vol).
        lower_mult: Multiplier for lower barrier (e.g., 1.0 * daily_vol).
        max_holding: Maximum holding period in bars.
    """

    def __init__(
        self,
        upper_mult: float = 2.0,
        lower_mult: float = 1.0,
        max_holding: int = 252,
    ) -> None:
        """Initialize triple barrier labeler.

        Args:
            upper_mult: Upper barrier multiplier (default 2.0).
            lower_mult: Lower barrier multiplier (default 1.0).
            max_holding: Maximum holding period (default 252 bars).

        Raises:
            MetaLabelingError: If parameters are invalid.
        """
        if upper_mult <= 0 or lower_mult <= 0:
            raise MetaLabelingError(
                "Barrier multipliers must be positive",
                upper_mult=upper_mult,
                lower_mult=lower_mult,
            )
        if max_holding < 1:
            raise MetaLabelingError("max_holding must be >= 1", max_holding=max_holding)

        self.upper_mult = upper_mult
        self.lower_mult = lower_mult
        self.max_holding = max_holding

        logger.info(
            "triple_barrier_labeler_initialized",
            upper_mult=upper_mult,
            lower_mult=lower_mult,
            max_holding=max_holding,
        )

    def label(
        self,
        prices: pd.Series,
        events: pd.DataFrame,
        upper_mult: Optional[float] = None,
        lower_mult: Optional[float] = None,
        max_holding: Optional[int] = None,
    ) -> pd.Series:
        """Label events using triple barrier method.

        Args:
            prices: Price series (close prices). Index must be datetime.
                   Shape: (n_periods,)
            events: Events dataframe with columns:
                   - 'index': Event entry index
                   - Any other event metadata
            upper_mult: Override upper multiplier for this run.
            lower_mult: Override lower multiplier for this run.
            max_holding: Override max_holding for this run.

        Returns:
            Series of labels (+1, -1, 0) indexed by event index.

        Raises:
            MetaLabelingError: If inputs are invalid.

        Example:
            >>> prices = pd.Series([100, 101, 102, 103], index=pd.date_range('2024-01-01', periods=4))
            >>> events = pd.DataFrame({'index': [0, 1]})
            >>> labeler = TripleBarrierLabeler(upper_mult=2.0, lower_mult=1.0)
            >>> labels = labeler.label(prices, events)
            >>> labels
            0    1
            1   -1
            dtype: int64
        """
        if not isinstance(prices, pd.Series):
            raise MetaLabelingError("prices must be pd.Series", type=type(prices))
        if not isinstance(events, pd.DataFrame):
            raise MetaLabelingError("events must be pd.DataFrame", type=type(events))
        if "index" not in events.columns:
            raise MetaLabelingError(
                "events must have 'index' column",
                columns=events.columns.tolist(),
            )

        upper = upper_mult if upper_mult is not None else self.upper_mult
        lower = lower_mult if lower_mult is not None else self.lower_mult
        max_h = max_holding if max_holding is not None else self.max_holding

        logger.info(
            "triple_barrier_labeling_start",
            n_events=len(events),
            prices_len=len(prices),
        )

        # Compute daily volatility using exponential weighted moving std
        # span=100 as per Lopez de Prado recommendation
        prices_array = prices.values
        returns = np.diff(np.log(prices_array))
        daily_vol = pd.Series(returns).ewm(span=100).std().values

        # Pad beginning with first value
        daily_vol = np.pad(daily_vol, (1, 0), mode="edge")

        labels = {}

        for _, event in events.iterrows():
            entry_idx = int(event["index"])

            if entry_idx < 0 or entry_idx >= len(prices):
                logger.warning(
                    "event_index_out_of_range",
                    entry_idx=entry_idx,
                    prices_len=len(prices),
                )
                continue

            entry_price = prices_array[entry_idx]
            vol = daily_vol[entry_idx]

            # Define barriers
            upper_barrier = entry_price + upper * vol
            lower_barrier = entry_price - lower * vol
            vertical_idx = min(entry_idx + max_h, len(prices) - 1)

            # Check which barrier is hit first
            label = 0  # Default: vertical barrier

            # Search forward from entry
            for idx in range(entry_idx + 1, vertical_idx + 1):
                price = prices_array[idx]

                if price >= upper_barrier:
                    label = 1
                    break
                elif price <= lower_barrier:
                    label = -1
                    break

            labels[entry_idx] = label

        logger.info("triple_barrier_labeling_complete", labels_count=len(labels))
        return pd.Series(labels, dtype=np.int32)


class MetaLabeler:
    """Meta-labeler for validating primary model signals.

    Trains a secondary classifier to predict when the primary signal
    is correct, enabling confidence-based position sizing.

    Attributes:
        primary_model: The primary trading signal generator.
        meta_model: Secondary classifier (sklearn-compatible).
        threshold: Confidence threshold for trading.
    """

    def __init__(
        self,
        primary_model: Any,
        meta_model: Optional[BaseEstimator] = None,
        threshold: float = 0.6,
    ) -> None:
        """Initialize meta-labeler.

        Args:
            primary_model: Primary signal generator.
            meta_model: Secondary classifier (default: LogisticRegression).
            threshold: Confidence threshold for execution (default 0.6).

        Raises:
            MetaLabelingError: If parameters are invalid.
        """
        if threshold < 0 or threshold > 1:
            raise MetaLabelingError("threshold must be in [0, 1]", threshold=threshold)

        self.primary_model = primary_model
        self.threshold = threshold

        # Default to LogisticRegression if not provided
        if meta_model is None:
            from sklearn.linear_model import LogisticRegression

            self.meta_model = LogisticRegression(random_state=42)
        else:
            self.meta_model = meta_model

        self.is_trained = False

        logger.info(
            "meta_labeler_initialized",
            threshold=threshold,
            meta_model_type=type(self.meta_model).__name__,
        )

    def train(
        self,
        X: np.ndarray,
        primary_signals: np.ndarray,
        true_labels: np.ndarray,
    ) -> None:
        """Train meta-labeler on primary signal correctness.

        Args:
            X: Feature matrix. Shape: (n_samples, n_features)
            primary_signals: Primary model predictions. Shape: (n_samples,)
            true_labels: Ground truth labels (+1, -1, 0). Shape: (n_samples,)

        Raises:
            MetaLabelingError: If inputs are invalid or training fails.

        Example:
            >>> X = np.random.randn(1000, 10)
            >>> signals = np.random.choice([1, -1, 0], 1000)
            >>> labels = np.random.choice([1, -1, 0], 1000)
            >>> labeler = MetaLabeler(primary_model=None)
            >>> labeler.train(X, signals, labels)
        """
        if X.ndim != 2:
            raise MetaLabelingError("X must be 2D", shape=X.shape)
        if len(X) != len(primary_signals) or len(X) != len(true_labels):
            raise MetaLabelingError(
                "Mismatched lengths",
                X_len=len(X),
                signals_len=len(primary_signals),
                labels_len=len(true_labels),
            )

        logger.info(
            "meta_labeler_training_start",
            n_samples=len(X),
            n_features=X.shape[1],
        )

        # Create meta-labels: 1 if primary signal matches true label, 0 otherwise
        meta_labels = (primary_signals == true_labels).astype(int)

        # Combine features with primary signal as additional feature
        X_meta = np.column_stack([X, primary_signals])

        try:
            self.meta_model.fit(X_meta, meta_labels)
            self.is_trained = True
        except Exception as e:
            raise MetaLabelingError("Meta-model training failed", error=str(e))

        logger.info(
            "meta_labeler_training_complete",
            accuracy=np.mean(meta_labels),
        )

    def predict(
        self,
        X: np.ndarray,
        primary_signal: float,
    ) -> MetaLabelResult:
        """Predict if primary signal is reliable and suggest position size.

        Args:
            X: Feature vector. Shape: (n_features,)
            primary_signal: Primary model signal (+1, -1, 0).

        Returns:
            MetaLabelResult with confidence, trade decision, and position size.

        Raises:
            MetaLabelingError: If model not trained or prediction fails.

        Example:
            >>> X = np.random.randn(10)
            >>> result = labeler.predict(X, primary_signal=1)
            >>> if result.should_trade:
            ...     position_size = base_size * result.position_size_factor
        """
        if not self.is_trained:
            raise MetaLabelingError("Meta-labeler not trained yet")

        if X.ndim != 1:
            raise MetaLabelingError("X must be 1D", shape=X.shape)

        # Prepare input
        X_meta = np.append(X, primary_signal)

        try:
            # Get probability of primary signal being correct
            probability = self.meta_model.predict_proba(X_meta.reshape(1, -1))[0, 1]
        except Exception as e:
            raise MetaLabelingError("Prediction failed", error=str(e))

        # Decision: trade if above threshold
        should_trade = probability >= self.threshold

        # Position sizing: linear scaling from threshold to 1.0
        if probability < self.threshold:
            position_size_factor = 0.0
        else:
            # Scale from 0 at threshold to 1.0 at probability=1.0
            # Guard against threshold=1.0 case
            denom = 1.0 - self.threshold
            if denom <= 0:
                # Threshold is >= 1.0, which shouldn't happen (validated in __init__)
                # But handle it gracefully
                position_size_factor = 0.0
            else:
                position_size_factor = (probability - self.threshold) / denom
                position_size_factor = float(np.clip(position_size_factor, 0.0, 1.0))

        logger.info(
            "meta_labeler_predict",
            probability=probability,
            should_trade=should_trade,
            position_size_factor=position_size_factor,
        )

        return MetaLabelResult(
            probability=probability,
            should_trade=should_trade,
            position_size_factor=position_size_factor,
        )


class PurgedKFold:
    """K-fold cross-validation with embargo and purging for financial data.

    Prevents lookahead bias by:
    - Purging: Removing samples that share information with test set
    - Embargo: Adding temporal gaps around test set boundaries

    This is critical for time-series financial data where future information
    cannot be used to train the model.

    Attributes:
        n_splits: Number of folds.
        embargo_pct: Percentage of data to embargo (default 1%).
    """

    def __init__(self, n_splits: int = 5, embargo_pct: float = 0.01) -> None:
        """Initialize purged K-fold splitter.

        Args:
            n_splits: Number of folds (default 5).
            embargo_pct: Embargo as percentage of total data (default 0.01 = 1%).

        Raises:
            MetaLabelingError: If parameters are invalid.
        """
        if n_splits < 2:
            raise MetaLabelingError("n_splits must be >= 2", n_splits=n_splits)
        if not (0 <= embargo_pct < 1):
            raise MetaLabelingError(
                "embargo_pct must be in [0, 1)",
                embargo_pct=embargo_pct,
            )

        self.n_splits = n_splits
        self.embargo_pct = embargo_pct
        self.kf = KFold(n_splits=n_splits, shuffle=False)

        logger.info(
            "purged_kfold_initialized",
            n_splits=n_splits,
            embargo_pct=embargo_pct,
        )

    def split(
        self,
        X: np.ndarray,
        y: Optional[np.ndarray] = None,
        embargo_pct: Optional[float] = None,
    ) -> Generator[tuple[np.ndarray, np.ndarray], None, None]:
        """Generate train/test indices with embargo and purging.

        Args:
            X: Feature matrix. Shape: (n_samples, n_features)
            y: Target labels (optional, only for compatibility).
            embargo_pct: Override embargo percentage.

        Yields:
            Tuples of (train_indices, test_indices) with purging and embargo
            applied to prevent lookahead bias.

        Example:
            >>> X = np.random.randn(1000, 10)
            >>> splitter = PurgedKFold(n_splits=5)
            >>> for train_idx, test_idx in splitter.split(X):
            ...     X_train, X_test = X[train_idx], X[test_idx]
            ...     # Train and evaluate
        """
        if X.ndim != 2:
            raise MetaLabelingError("X must be 2D", shape=X.shape)

        embargo = embargo_pct if embargo_pct is not None else self.embargo_pct
        embargo_size = max(1, int(len(X) * embargo))

        for train_idx, test_idx in self.kf.split(X):
            # Get test set boundaries
            test_min = test_idx.min()
            test_max = test_idx.max()

            # Define embargo zones
            embargo_lower = max(0, test_min - embargo_size)
            embargo_upper = min(len(X) - 1, test_max + embargo_size)

            # Purge: remove samples that overlap with embargo zones
            purged_train_idx = []
            for idx in train_idx:
                if idx < embargo_lower or idx > embargo_upper:
                    purged_train_idx.append(idx)

            purged_train_idx = np.array(purged_train_idx)

            if len(purged_train_idx) == 0:
                logger.warning(
                    "purged_kfold_empty_train",
                    test_min=test_min,
                    test_max=test_max,
                    embargo_lower=embargo_lower,
                    embargo_upper=embargo_upper,
                )
                continue

            yield purged_train_idx, test_idx

        logger.info(
            "purged_kfold_split_complete",
            n_samples=len(X),
            embargo_size=embargo_size,
        )
