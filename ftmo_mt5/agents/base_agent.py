"""Base agent class for multi-agent orchestration system.

Provides abstract base class for all trading agents with:
- Heartbeat monitoring for health tracking
- Restart counting and max restart limits
- Lifecycle management (start/stop)
- OpenTelemetry integration
- Structured logging
"""

__all__ = [
    "BaseAgent",
]

import time
from abc import ABC, abstractmethod
from typing import Optional

import structlog
from opentelemetry import trace

from ftmo_mt5.core.exceptions import AgentCrashError

logger = structlog.get_logger(__name__)
tracer = trace.get_tracer(__name__)


class BaseAgent(ABC):
    """Abstract base agent for the multi-agent orchestration system.

    All agents inherit from this class and implement the async process() method.
    Agents track their own health via heartbeat mechanism and support graceful
    restart with configurable limits.

    Attributes:
        name: Agent identifier (e.g., "signal_agent", "risk_agent").
        heartbeat_interval_s: Interval for heartbeat updates in seconds.
        max_restarts: Maximum allowed consecutive restarts.
    """

    def __init__(self, name: str, heartbeat_interval_s: int = 5, max_restarts: int = 3):
        """Initialize base agent.

        Args:
            name: Agent identifier.
            heartbeat_interval_s: Heartbeat interval in seconds (default: 5).
            max_restarts: Maximum consecutive restarts allowed (default: 3).

        Note:
            Uses time.monotonic() for all heartbeat timing to ensure immune
            to system clock adjustments. NEVER mix with time.time() in comparisons.
        """
        self.name = name
        self.agent_type = name.replace("_agent", "").upper()
        self.heartbeat_interval_s = heartbeat_interval_s
        self.max_restarts = max_restarts

        # Health tracking - using monotonic clock for immunity to system clock adjustments
        # CRITICAL: All heartbeat comparisons MUST use monotonic clock only
        self._last_heartbeat = time.monotonic()
        self._running = False
        self._restart_count = 0

        logger.info(
            "agent_initialized",
            agent_name=self.name,
            heartbeat_interval_s=heartbeat_interval_s,
            max_restarts=max_restarts,
        )

    @abstractmethod
    async def process(self, event):
        """Process an event through this agent.

        Subclasses must implement this method to define agent-specific logic.

        Args:
            event: Event to process.

        Returns:
            Result of processing (type depends on agent implementation).

        Raises:
            Exception: Agent-specific exceptions for failures.
        """
        pass

    async def start(self) -> None:
        """Start the agent."""
        with tracer.start_as_current_span(f"{self.name}_start") as span:
            try:
                self._running = True
                self.heartbeat()
                logger.info("agent_started", agent_name=self.name)
                span.set_attribute("agent_name", self.name)
            except Exception as e:
                logger.error("agent_start_failed", agent_name=self.name, error=str(e))
                span.record_exception(e)
                raise

    async def stop(self) -> None:
        """Stop the agent gracefully."""
        with tracer.start_as_current_span(f"{self.name}_stop") as span:
            try:
                self._running = False
                logger.info("agent_stopped", agent_name=self.name)
                span.set_attribute("agent_name", self.name)
            except Exception as e:
                logger.error("agent_stop_failed", agent_name=self.name, error=str(e))
                span.record_exception(e)

    def is_healthy(self, timeout_seconds: int = 5) -> bool:
        """Check if agent is healthy (alias for is_alive)."""
        return self.is_alive(timeout_s=float(timeout_seconds))

    def heartbeat(self) -> None:
        """Update heartbeat timestamp using monotonic clock.

        Called periodically to indicate the agent is alive and responsive.
        Uses time.monotonic() for precision and immunity to system clock adjustments.
        """
        self._last_heartbeat = time.monotonic()
        logger.debug("heartbeat_updated", agent_name=self.name)

    def is_alive(self, timeout_s: float = 5.0) -> bool:
        """Check if agent is alive based on heartbeat freshness.

        An agent is considered alive if its last heartbeat is within
        the specified timeout.

        Args:
            timeout_s: Maximum age of heartbeat in seconds (default: 5).

        Returns:
            True if agent's last heartbeat is fresher than timeout.
        """
        time_since_heartbeat = time.monotonic() - self._last_heartbeat
        is_alive = time_since_heartbeat <= timeout_s

        if not is_alive:
            logger.warning(
                "agent_not_responding",
                agent_name=self.name,
                time_since_heartbeat=time_since_heartbeat,
                timeout_s=timeout_s,
            )

        return is_alive

    def increment_restart_count(self) -> None:
        """Increment the restart counter."""
        self._restart_count += 1
        logger.warning(
            "agent_restart_incremented",
            agent_name=self.name,
            restart_count=self._restart_count,
            max_restarts=self.max_restarts,
        )

    def reset_restart_count(self) -> None:
        """Reset the restart counter after successful operation."""
        self._restart_count = 0
        logger.info("agent_restart_count_reset", agent_name=self.name)

    def should_restart(self) -> bool:
        """Check if agent can restart based on restart limit.

        Returns:
            True if restart_count < max_restarts, False otherwise.
            When False is returned, the agent MUST NOT attempt to restart again.
        """
        can_restart = self._restart_count < self.max_restarts
        if not can_restart:
            logger.critical(
                "agent_restart_limit_exceeded_fatal",
                agent_name=self.name,
                restart_count=self._restart_count,
                max_restarts=self.max_restarts,
                message="Agent exceeded max restarts - this is a fatal condition that requires system shutdown",
            )
        return can_restart

    @property
    def is_running(self) -> bool:
        """Return whether agent is currently running."""
        return self._running

    @property
    def restart_count(self) -> int:
        """Return current restart count."""
        return self._restart_count
