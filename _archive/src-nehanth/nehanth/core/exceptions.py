"""Custom exception hierarchy for FTMO MT5 trading system.

All exceptions inherit from FTMOBaseError and provide domain-specific
context for error handling and recovery strategies.
"""

__all__ = [
    "FTMOBaseError",
    "MT5ConnectionError",
    "MT5ExecutionError",
    "RiskBreachError",
    "ReconciliationError",
    "CircuitBreakerError",
    "AgentCrashError",
    "ModelPromotionError",
]

from typing import Any, Optional


class FTMOBaseError(Exception):
    """Base exception for all FTMO trading system errors.

    Provides common error context and error tracking.
    """

    def __init__(self, message: str, **context: Any) -> None:
        """Initialize base exception.

        Args:
            message: Human-readable error description.
            **context: Additional context data for debugging.
        """
        self.message = message
        self.context = context
        super().__init__(message)

    def __str__(self) -> str:
        """Return formatted error message with context."""
        if self.context:
            context_str = " | ".join(f"{k}={v}" for k, v in self.context.items())
            return f"{self.message} ({context_str})"
        return self.message


class RiskBreachError(FTMOBaseError):
    """Raised when a risk management limit is breached.

    This exception indicates that an action would violate FTMO challenge rules
    or internal risk management thresholds.
    """

    def __init__(
        self,
        message: str,
        breach_type: str,
        current_value: Any,
        limit: Optional[Any] = None,
        **context: Any,
    ) -> None:
        """Initialize risk breach error.

        Args:
            message: Description of the breach.
            breach_type: Type of limit breached (e.g., "daily_loss", "margin_ratio").
            current_value: Current value that breached the limit.
            limit: The limit threshold that was breached.
            **context: Additional context data.
        """
        self.breach_type = breach_type
        self.current_value = current_value
        self.limit = limit
        super().__init__(message, breach_type=breach_type, current_value=current_value, limit=limit, **context)


class MT5ConnectionError(FTMOBaseError):
    """Raised when MT5 connection fails or is lost.

    Indicates network or authentication issues with the MT5 terminal.
    """

    def __init__(self, message: str, **context: Any) -> None:
        """Initialize MT5 connection error.

        Args:
            message: Description of the connection issue.
            **context: Additional context data (e.g., server, login).
        """
        super().__init__(message, error_type="connection", **context)


class MT5ExecutionError(FTMOBaseError):
    """Raised when MT5 order execution fails.

    Wraps MT5 return codes and provides interpretation of execution failures.
    """

    def __init__(self, message: str, retcode: int, **context: Any) -> None:
        """Initialize MT5 execution error.

        Args:
            message: Description of the execution failure.
            retcode: MT5 return code from the failed operation.
            **context: Additional context data.
        """
        self.retcode = retcode
        super().__init__(message, retcode=retcode, **context)


class AgentCrashError(FTMOBaseError):
    """Raised when the trading agent crashes unexpectedly.

    Indicates a fatal error in the automated trading logic that prevents
    continued operation.
    """

    def __init__(self, message: str, agent_name: str, **context: Any) -> None:
        """Initialize agent crash error.

        Args:
            message: Description of the crash.
            agent_name: Name of the agent that crashed.
            **context: Additional context data (e.g., exception, traceback).
        """
        self.agent_name = agent_name
        super().__init__(message, agent_name=agent_name, **context)


class ReconciliationError(FTMOBaseError):
    """Raised when position/account reconciliation fails.

    Indicates a mismatch between expected and actual account/position state
    in MT5, potentially due to slippage, fills, or manual orders.
    """

    def __init__(self, message: str, **context: Any) -> None:
        """Initialize reconciliation error.

        Args:
            message: Description of the reconciliation failure.
            **context: Additional context data (expected vs actual states).
        """
        super().__init__(message, error_type="reconciliation", **context)


class CircuitBreakerError(FTMOBaseError):
    """Raised when the circuit breaker is triggered.

    Indicates that the system has detected unacceptable risk or performance
    degradation and has halted trading.
    """

    def __init__(self, message: str, agent_name: Optional[str] = None, error: Optional[str] = None, **context: Any) -> None:
        """Initialize circuit breaker error.

        Args:
            message: Description of the circuit breaker trigger.
            agent_name: Name of agent that triggered the circuit breaker.
            error: Error that caused the circuit breaker trigger.
            **context: Additional context data (e.g., trigger reason, metrics).
        """
        if agent_name:
            context['agent_name'] = agent_name
        if error:
            context['error'] = error
        super().__init__(message, error_type="circuit_breaker", **context)


class ModelPromotionError(FTMOBaseError):
    """Raised when Pydantic model promotion or validation fails.

    Indicates issues with converting between model types or validating
    data during promotion.
    """

    def __init__(self, message: str, **context: Any) -> None:
        """Initialize model promotion error.

        Args:
            message: Description of the promotion failure.
            **context: Additional context data (e.g., field, expected_type).
        """
        super().__init__(message, error_type="model_promotion", **context)
