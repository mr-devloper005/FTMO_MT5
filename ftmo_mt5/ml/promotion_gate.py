"""Model promotion gate for ML pipeline governance.

Implements comprehensive gating criteria for promoting trained models to
production based on rigorous statistical testing and performance validation.
Prevents deployment of overfit or statistically insignificant models.
"""

__all__ = [
    "PromotionGateError",
    "ModelMetrics",
    "PromotionResult",
    "ModelPromotionGate",
]

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import structlog

from ftmo_mt5.core.exceptions import FTMOBaseError

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
    promotion_decisions = meter.create_counter(
        "ml.promotion.decisions_total",
        description="Total number of promotion decisions made",
    )
else:
    promotion_decisions = None


class PromotionGateError(FTMOBaseError):
    """Raised when model promotion gating fails."""

    def __init__(self, message: str, **context: Any) -> None:
        """Initialize promotion gate error.

        Args:
            message: Description of the gating failure.
            **context: Additional context data.
        """
        super().__init__(message, error_type="promotion_gate", **context)


@dataclass
class ModelMetrics:
    """Collected metrics for model promotion evaluation.

    Attributes:
        pbo: Probability of Backtest Overfitting (lower is better).
             Threshold: < 0.30
        dsr: Deflated Sharpe Ratio (higher is better).
             Threshold: > 0.95 indicates statistical significance
        wfo_oos_sharpe: Out-of-sample Sharpe ratio from walk-forward analysis.
                       Threshold: > 0.50
        psi: Population Stability Index from drift monitoring.
             For reference, not gating criteria (> 0.20 indicates drift).
        training_date: When the model was trained.
    """

    pbo: float
    dsr: float
    wfo_oos_sharpe: float
    psi: float = 0.0
    training_date: datetime = field(default_factory=datetime.utcnow)


@dataclass
class PromotionResult:
    """Result of model promotion decision.

    Attributes:
        promoted: Whether model passed all gates and should be deployed.
        failures: List of gating criteria that failed (empty if promoted).
        gates_passed: List of gating criteria that passed.
        confidence_score: Overall confidence in promotion (0-1).
    """

    promoted: bool
    failures: list[str] = field(default_factory=list)
    gates_passed: list[str] = field(default_factory=list)
    confidence_score: float = 0.0


class ModelPromotionGate:
    """Gate for approving ML models to production.

    Implements three primary gating criteria from PRD:
    1. PBO < 0.30: Low overfitting risk (CSCV validation)
    2. DSR > 0.95: Statistically significant alpha (multiple testing correction)
    3. WFO OOS Sharpe > 0.50: Positive out-of-sample performance

    Additionally monitors PSI for regime stability.

    All gating decisions are comprehensively logged for audit trail.
    """

    # Gating thresholds (from PRD)
    PBO_THRESHOLD = 0.30
    DSR_THRESHOLD = 0.95
    WFO_OOS_SHARPE_THRESHOLD = 0.50
    PSI_DRIFT_WARNING = 0.20

    def __init__(self) -> None:
        """Initialize model promotion gate."""
        logger.info(
            "model_promotion_gate_initialized",
            pbo_threshold=self.PBO_THRESHOLD,
            dsr_threshold=self.DSR_THRESHOLD,
            wfo_oos_sharpe_threshold=self.WFO_OOS_SHARPE_THRESHOLD,
            psi_drift_warning=self.PSI_DRIFT_WARNING,
        )

    def check_promotion(self, model_metrics: ModelMetrics) -> PromotionResult:
        """Check if model meets all promotion criteria.

        Evaluates model against all gating criteria and provides detailed
        pass/fail report. All decisions are logged for audit and compliance.

        Args:
            model_metrics: Collected metrics for the candidate model.

        Returns:
            PromotionResult indicating pass/fail status and details.

        Raises:
            PromotionGateError: If metrics are invalid.

        Example:
            >>> metrics = ModelMetrics(pbo=0.25, dsr=0.98, wfo_oos_sharpe=0.65)
            >>> gate = ModelPromotionGate()
            >>> result = gate.check_promotion(metrics)
            >>> if result.promoted:
            ...     print("Model approved for deployment")
            ...     print(f"Gates passed: {result.gates_passed}")
        """
        # Validate inputs
        if not isinstance(model_metrics, ModelMetrics):
            raise PromotionGateError(
                "model_metrics must be ModelMetrics instance",
                type=type(model_metrics),
            )

        if model_metrics.pbo < 0 or model_metrics.pbo > 1:
            raise PromotionGateError(
                "PBO must be in [0, 1]",
                pbo=model_metrics.pbo,
            )

        if model_metrics.dsr < 0:
            raise PromotionGateError(
                "DSR must be non-negative",
                dsr=model_metrics.dsr,
            )

        if model_metrics.psi < 0:
            raise PromotionGateError(
                "PSI must be non-negative",
                psi=model_metrics.psi,
            )

        logger.info(
            "model_promotion_check_start",
            pbo=model_metrics.pbo,
            dsr=model_metrics.dsr,
            wfo_oos_sharpe=model_metrics.wfo_oos_sharpe,
            psi=model_metrics.psi,
            training_date=model_metrics.training_date.isoformat(),
        )

        failures = []
        gates_passed = []

        # Gate 1: PBO (Probability of Backtest Overfitting)
        # Lower is better - indicates less overfitting
        if model_metrics.pbo < self.PBO_THRESHOLD:
            gates_passed.append("PBO < 0.30")
            logger.info(
                "promotion_gate_pbo_passed",
                pbo=model_metrics.pbo,
                threshold=self.PBO_THRESHOLD,
            )
        else:
            failures.append(
                f"PBO={model_metrics.pbo:.4f} exceeds threshold {self.PBO_THRESHOLD}"
            )
            logger.warning(
                "promotion_gate_pbo_failed",
                pbo=model_metrics.pbo,
                threshold=self.PBO_THRESHOLD,
            )

        # Gate 2: DSR (Deflated Sharpe Ratio)
        # Higher is better - indicates statistical significance
        if model_metrics.dsr > self.DSR_THRESHOLD:
            gates_passed.append("DSR > 0.95")
            logger.info(
                "promotion_gate_dsr_passed",
                dsr=model_metrics.dsr,
                threshold=self.DSR_THRESHOLD,
            )
        else:
            failures.append(
                f"DSR={model_metrics.dsr:.4f} below threshold {self.DSR_THRESHOLD}"
            )
            logger.warning(
                "promotion_gate_dsr_failed",
                dsr=model_metrics.dsr,
                threshold=self.DSR_THRESHOLD,
            )

        # Gate 3: WFO Out-of-Sample Sharpe
        # Higher is better - indicates positive OOS performance
        if model_metrics.wfo_oos_sharpe > self.WFO_OOS_SHARPE_THRESHOLD:
            gates_passed.append("WFO OOS Sharpe > 0.50")
            logger.info(
                "promotion_gate_wfo_passed",
                wfo_oos_sharpe=model_metrics.wfo_oos_sharpe,
                threshold=self.WFO_OOS_SHARPE_THRESHOLD,
            )
        else:
            failures.append(
                f"WFO OOS Sharpe={model_metrics.wfo_oos_sharpe:.4f} "
                f"below threshold {self.WFO_OOS_SHARPE_THRESHOLD}"
            )
            logger.warning(
                "promotion_gate_wfo_failed",
                wfo_oos_sharpe=model_metrics.wfo_oos_sharpe,
                threshold=self.WFO_OOS_SHARPE_THRESHOLD,
            )

        # Soft gate: PSI (Population Stability Index)
        # Not a hard gate, but flagged for monitoring
        psi_status = "stable"
        if model_metrics.psi > self.PSI_DRIFT_WARNING:
            psi_status = "drifted"
            logger.warning(
                "promotion_gate_psi_drift_warning",
                psi=model_metrics.psi,
                threshold=self.PSI_DRIFT_WARNING,
            )
        else:
            logger.info(
                "promotion_gate_psi_stable",
                psi=model_metrics.psi,
            )

        # Determine promotion status
        promoted = len(failures) == 0

        # Compute confidence score (0-1)
        # Based on how far metrics are from thresholds
        pbo_score = 1.0 - (model_metrics.pbo / self.PBO_THRESHOLD)  # Normalize distance
        dsr_score = (model_metrics.dsr / self.DSR_THRESHOLD) if model_metrics.dsr > 0 else 0.0
        wfo_score = (model_metrics.wfo_oos_sharpe / self.WFO_OOS_SHARPE_THRESHOLD) if model_metrics.wfo_oos_sharpe > 0 else 0.0

        # Average scores
        confidence_score = (pbo_score + dsr_score + wfo_score) / 3.0
        confidence_score = max(0.0, min(1.0, confidence_score))  # Clamp to [0, 1]

        # Record decision
        if promotion_decisions:
            promotion_decisions.add(
                1,
                attributes={
                    "promoted": promoted,
                    "psi_status": psi_status,
                    "gates_passed": len(gates_passed),
                },
            )

        result = PromotionResult(
            promoted=promoted,
            failures=failures,
            gates_passed=gates_passed,
            confidence_score=confidence_score,
        )

        log_level = "info" if promoted else "warning"
        log_fn = logger.info if promoted else logger.warning

        log_fn(
            "model_promotion_decision",
            promoted=promoted,
            gates_passed=result.gates_passed,
            gates_failed=result.failures,
            confidence_score=confidence_score,
            psi_status=psi_status,
            training_date=model_metrics.training_date.isoformat(),
        )

        if promoted:
            logger.info(
                "model_promotion_approved",
                confidence_score=confidence_score,
                ready_for_deployment=True,
            )
        else:
            logger.warning(
                "model_promotion_rejected",
                failure_count=len(failures),
                failures=failures,
            )

        return result

    def check_promotion_batch(
        self,
        metrics_list: list[ModelMetrics],
    ) -> list[PromotionResult]:
        """Check promotion for multiple models.

        Args:
            metrics_list: List of ModelMetrics for candidate models.

        Returns:
            List of PromotionResult, one per input model.

        Example:
            >>> models_metrics = [
            ...     ModelMetrics(pbo=0.25, dsr=0.98, wfo_oos_sharpe=0.65),
            ...     ModelMetrics(pbo=0.35, dsr=0.92, wfo_oos_sharpe=0.45),
            ... ]
            >>> gate = ModelPromotionGate()
            >>> results = gate.check_promotion_batch(models_metrics)
            >>> approved = [r for r in results if r.promoted]
        """
        logger.info(
            "model_promotion_batch_start",
            batch_size=len(metrics_list),
        )

        results = [self.check_promotion(m) for m in metrics_list]

        approved_count = sum(1 for r in results if r.promoted)
        logger.info(
            "model_promotion_batch_complete",
            batch_size=len(metrics_list),
            approved_count=approved_count,
            approval_rate=approved_count / len(metrics_list) if metrics_list else 0.0,
        )

        return results

    @staticmethod
    def format_decision_report(result: PromotionResult) -> str:
        """Format promotion decision as human-readable report.

        Args:
            result: PromotionResult to format.

        Returns:
            Formatted report string.

        Example:
            >>> result = PromotionResult(promoted=True, gates_passed=["PBO < 0.30"])
            >>> print(ModelPromotionGate.format_decision_report(result))
        """
        status = "APPROVED" if result.promoted else "REJECTED"
        lines = [
            f"Model Promotion Decision: {status}",
            f"Confidence Score: {result.confidence_score:.2%}",
            f"Gates Passed ({len(result.gates_passed)}): {', '.join(result.gates_passed)}",
        ]

        if result.failures:
            lines.append(f"Gates Failed ({len(result.failures)}):")
            for failure in result.failures:
                lines.append(f"  - {failure}")

        return "\n".join(lines)
