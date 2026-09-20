"""Structured logging configuration for FTMO MT5 trading system.

Provides centralized logging setup using structlog with JSON output for
production and console output for development, including context binding
for correlation tracking and caller information.
"""

__all__ = [
    "setup_logging",
    "get_logger",
    "bind_correlation_id",
    "bind_agent_name",
    "bind_symbol",
    "unbind_all",
]

import logging
import sys
from typing import Optional
from datetime import datetime, timezone
import uuid

import structlog


# Module-level logger instance
_logger: Optional[structlog.BoundLogger] = None


def setup_logging(
    log_level: str = "INFO",
    json_output: bool = True,
    log_file: Optional[str] = None,
) -> structlog.BoundLogger:
    """Configure structured logging for the system.

    Sets up structlog with JSON output for production or console output
    for development, including timestamp, log level, and caller information.

    Args:
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        json_output: If True, log as JSON. If False, log as pretty-printed console.
        log_file: Optional file path to log to. If None, logs to stdout.

    Returns:
        Configured structlog logger instance.
    """
    global _logger

    # Convert log level string to logging level
    numeric_level = getattr(logging, log_level.upper(), logging.INFO)

    # Configure standard library logging
    if log_file:
        handler = logging.FileHandler(log_file)
    else:
        handler = logging.StreamHandler(sys.stdout)

    root_logger = logging.getLogger()
    root_logger.addHandler(handler)
    root_logger.setLevel(numeric_level)

    # Configure structlog
    if json_output:
        processors = [
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            structlog.processors.JSONRenderer(),
        ]
    else:
        processors = [
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.dev.ConsoleRenderer(),
        ]

    structlog.configure(
        processors=processors,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    _logger = structlog.get_logger(__name__)
    _logger.info("logging_configured", log_level=log_level, json_output=json_output)

    return _logger


def get_logger(name: str) -> structlog.BoundLogger:
    """Get a configured structlog logger instance.

    Args:
        name: Logger name (typically module __name__).

    Returns:
        Configured structlog logger with context binding support.

    Raises:
        RuntimeError: If setup_logging() has not been called.
    """
    if _logger is None:
        return structlog.get_logger(name)
    return structlog.get_logger(name)


def bind_correlation_id(correlation_id: Optional[str] = None) -> str:
    """Generate or bind a correlation ID for request tracing.

    Stores the correlation ID in structlog's context for automatic
    inclusion in all subsequent logs from this context.

    Args:
        correlation_id: Optional existing correlation ID. If None, generates new UUID.

    Returns:
        The correlation ID (existing or newly generated).
    """
    if correlation_id is None:
        correlation_id = str(uuid.uuid4())

    structlog.contextvars.bind_contextvars(correlation_id=correlation_id)
    return correlation_id


def bind_agent_name(agent_name: str) -> None:
    """Bind agent name to logging context.

    Automatically includes agent name in all subsequent logs from this context.

    Args:
        agent_name: Name of the agent (e.g., "RiskAgent", "ExecutionAgent").
    """
    structlog.contextvars.bind_contextvars(agent_name=agent_name)


def bind_symbol(symbol: str) -> None:
    """Bind trading symbol to logging context.

    Automatically includes symbol in all subsequent logs from this context.

    Args:
        symbol: Trading instrument symbol (e.g., "EURUSD").
    """
    structlog.contextvars.bind_contextvars(symbol=symbol)


def unbind_all() -> None:
    """Clear all bound context variables.

    Useful when cleaning up after handling a request or task.
    """
    structlog.contextvars.clear_contextvars()


class CorrelationIdMiddleware:
    """ASGI middleware for automatic correlation ID injection.

    Injects a unique correlation ID into the request context and
    includes it in all subsequent logs for request tracing.
    """

    def __init__(self, app) -> None:
        """Initialize middleware.

        Args:
            app: ASGI application.
        """
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        """Process incoming request.

        Args:
            scope: ASGI scope.
            receive: ASGI receive channel.
            send: ASGI send channel.
        """
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Extract or create correlation ID
        headers = dict(scope.get("headers", []))
        correlation_id = headers.get(
            b"x-correlation-id",
            str(uuid.uuid4()).encode(),
        ).decode()

        # Bind to structlog context
        bind_correlation_id(correlation_id)

        try:
            await self.app(scope, receive, send)
        finally:
            unbind_all()
