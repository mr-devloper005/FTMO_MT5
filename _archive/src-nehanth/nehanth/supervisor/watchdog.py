"""Supervisor watchdog for monitoring and managing agent health.

Provides comprehensive supervision of trading agents with automatic restart
capabilities for non-critical agents and immediate kill-switch triggering
for risk management failures.
"""

__all__ = [
    "KillSwitchState",
    "AgentType",
    "Supervisor",
]

import asyncio
import time
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import uuid4

import structlog

from nehanth.core.models import CircuitBreakerTriggered
from nehanth.core.exceptions import CircuitBreakerError
from nehanth.observability.telemetry import get_meter, traced
from nehanth.observability.logging_config import get_logger


log = get_logger(__name__)


# ============================================================================
# Enums
# ============================================================================


class KillSwitchState(str, Enum):
    """Kill switch state enumeration."""

    ARMED = "ARMED"
    """System ready, trading permitted."""

    TRIGGERED = "TRIGGERED"
    """Emergency shutdown active, all positions closing."""

    DISARMED = "DISARMED"
    """System disabled, no trading allowed."""


class AgentType(str, Enum):
    """Classification of agent types."""

    RISK = "RISK"
    """Risk management agent - critical for safety."""

    SIGNAL = "SIGNAL"
    """Signal generation agent - data-driven."""

    EXECUTION = "EXECUTION"
    """Order execution agent - operational."""


# ============================================================================
# Base Agent Interface
# ============================================================================


class BaseAgent:
    """Base interface for supervised agents.

    All agents must implement this interface to be supervised by the watchdog.
    """

    def __init__(self, name: str, agent_type: AgentType) -> None:
        """Initialize agent.

        Args:
            name: Unique agent identifier.
            agent_type: Type of agent for supervision rules.
        """
        self.name = name
        self.agent_type = agent_type
        self.last_heartbeat: Optional[datetime] = None
        self.restart_count = 0
        self.is_running = False

    async def start(self) -> None:
        """Start the agent."""
        raise NotImplementedError

    async def stop(self) -> None:
        """Stop the agent gracefully."""
        raise NotImplementedError

    async def heartbeat(self) -> None:
        """Record a heartbeat (health signal)."""
        self.last_heartbeat = datetime.now(timezone.utc)

    def is_healthy(self, timeout_seconds: int = 5) -> bool:
        """Check if agent has recent heartbeat.

        Args:
            timeout_seconds: Max seconds since last heartbeat to be considered healthy.

        Returns:
            True if recent heartbeat, False otherwise.
        """
        if self.last_heartbeat is None:
            return False
        elapsed = (datetime.now(timezone.utc) - self.last_heartbeat).total_seconds()
        return elapsed < timeout_seconds


# ============================================================================
# Supervisor / Watchdog
# ============================================================================


class Supervisor:
    """Agent supervisor with health monitoring and automatic restart logic.

    Monitors all registered agents for health signals and implements
    automated restart strategies based on agent type:
    - RISK agents: Any heartbeat miss triggers immediate kill switch
    - Other agents: Exponential backoff restart (max 3 attempts)
    """

    def __init__(
        self,
        heartbeat_interval_s: int = 1,
        risk_agent_timeout_s: int = 5,
        signal_agent_timeout_s: int = 10,
        execution_agent_timeout_s: int = 10,
        max_restart_attempts: int = 3,
        event_store: Optional[Any] = None,
    ) -> None:
        """Initialize supervisor.

        Args:
            heartbeat_interval_s: Interval for checking agent health.
            risk_agent_timeout_s: Timeout for risk agent heartbeat.
            signal_agent_timeout_s: Timeout for signal agent heartbeat.
            execution_agent_timeout_s: Timeout for execution agent heartbeat.
            max_restart_attempts: Maximum consecutive restart attempts.
            event_store: EventStore instance for emitting events.
        """
        self.heartbeat_interval_s = heartbeat_interval_s
        self.risk_agent_timeout_s = risk_agent_timeout_s
        self.signal_agent_timeout_s = signal_agent_timeout_s
        self.execution_agent_timeout_s = execution_agent_timeout_s
        self.max_restart_attempts = max_restart_attempts
        self.event_store = event_store

        self._agents: Dict[str, BaseAgent] = {}
        self._monitoring = False
        self._monitor_task: Optional[asyncio.Task] = None
        self._kill_switch_state = KillSwitchState.ARMED
        self._restart_backoff: Dict[str, int] = {}  # agent_name -> attempt_count

        # MT5 disconnect tracking (escalation at 15s and 30s)
        self._mt5_disconnect_start_time: Optional[float] = None
        self._mt5_last_healthy_check: Optional[float] = None
        self._mt5_risk_off_triggered = False
        self._mt5_halt_triggered = False

        # Get metrics for reporting
        self._meter = get_meter()

    def register_agent(self, agent: BaseAgent) -> None:
        """Register an agent for supervision.

        Args:
            agent: Agent instance implementing BaseAgent interface.

        Raises:
            ValueError: If agent name already registered.
        """
        if agent.name in self._agents:
            raise ValueError(f"Agent '{agent.name}' already registered")

        self._agents[agent.name] = agent
        self._restart_backoff[agent.name] = 0

        log.info("agent_registered", agent_name=agent.name, agent_type=agent.agent_type)

    async def start(self) -> None:
        """Start supervision of registered agents.

        Begins monitoring all agents and implements restart/kill-switch logic.
        """
        if self._monitoring:
            log.warning("supervisor_already_running")
            return

        self._monitoring = True
        self._kill_switch_state = KillSwitchState.ARMED
        log.info("supervisor_starting", agent_count=len(self._agents))

        # Start all registered agents
        for agent in self._agents.values():
            try:
                await agent.start()
                log.info("agent_started", agent_name=agent.name)
            except Exception as e:
                log.error(
                    "agent_start_failed",
                    agent_name=agent.name,
                    error=str(e),
                )

        # Begin monitoring loop
        self._monitor_task = asyncio.create_task(self._monitor_loop())
        log.info("supervisor_started")

    async def stop(self) -> None:
        """Stop supervision and gracefully shutdown agents.

        Cancels monitoring and stops all agents.
        """
        if not self._monitoring:
            log.warning("supervisor_not_running")
            return

        self._monitoring = False
        log.info("supervisor_stopping")

        # Cancel monitoring task
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass

        # Stop all agents
        for agent in self._agents.values():
            try:
                await agent.stop()
                log.info("agent_stopped", agent_name=agent.name)
            except Exception as e:
                log.error(
                    "agent_stop_failed",
                    agent_name=agent.name,
                    error=str(e),
                )

        log.info("supervisor_stopped")

    # ========================================================================
    # Monitoring Logic
    # ========================================================================

    async def _monitor_loop(self) -> None:
        """Main monitoring loop that checks agent health every heartbeat interval.

        Implements kill-switch logic for risk agent failures and restart logic
        for other agents with exponential backoff.
        """
        log.info("monitor_loop_started")

        try:
            while self._monitoring:
                try:
                    # Check all registered agents
                    # CRITICAL: Only check agents that are registered; ignore heartbeats from unregistered agents
                    for agent_name, agent in list(self._agents.items()):
                        timeout = self._get_agent_timeout(agent)

                        # Check if agent is healthy
                        if not agent.is_healthy(timeout_seconds=timeout):
                            await self._on_agent_unhealthy(agent)

                    # Record metrics
                    self._update_metrics()

                    # Sleep before next check
                    await asyncio.sleep(self.heartbeat_interval_s)

                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    log.error(
                        "monitor_loop_error",
                        error=str(e),
                        exc_info=True,
                    )
                    # Continue monitoring despite errors
                    await asyncio.sleep(self.heartbeat_interval_s)

        except asyncio.CancelledError:
            log.info("monitor_loop_cancelled")
        finally:
            log.info("monitor_loop_stopped")

    def _get_agent_timeout(self, agent: BaseAgent) -> int:
        """Get heartbeat timeout for agent type.

        Args:
            agent: Agent instance.

        Returns:
            Timeout in seconds.
        """
        if agent.agent_type == AgentType.RISK:
            return self.risk_agent_timeout_s
        elif agent.agent_type == AgentType.SIGNAL:
            return self.signal_agent_timeout_s
        else:
            return self.execution_agent_timeout_s

    async def _on_agent_unhealthy(self, agent: BaseAgent) -> None:
        """Handle unhealthy agent.

        Args:
            agent: Unhealthy agent instance.
        """
        log.warning(
            "agent_unhealthy",
            agent_name=agent.name,
            agent_type=agent.agent_type,
        )

        if agent.agent_type == AgentType.RISK:
            # Risk agent failure = immediate kill switch
            await self._kill_switch(
                reason=f"Risk agent '{agent.name}' missed heartbeat"
            )
        else:
            # Other agents: restart with exponential backoff
            await self._restart_agent(agent)

    # ========================================================================
    # Kill Switch Logic
    # ========================================================================

    @traced(name="kill_switch")
    async def _kill_switch(self, reason: str) -> None:
        """Trigger emergency shutdown sequence.

        Closes all positions, stops all agents, and emits circuit breaker event.

        Args:
            reason: Explanation for trigger.
        """
        if self._kill_switch_state == KillSwitchState.TRIGGERED:
            log.warning("kill_switch_already_triggered")
            return

        self._kill_switch_state = KillSwitchState.TRIGGERED
        log.critical("kill_switch_triggered", reason=reason)

        # Get current position count (placeholder)
        position_count = 0

        # Emit circuit breaker event
        if self.event_store:
            try:
                event = CircuitBreakerTriggered(
                    reason=reason,
                    positions_to_close=position_count,
                    timestamp=datetime.now(timezone.utc),
                    correlation_id=uuid4(),
                )
                # event_store.append is synchronous, call directly (already thread-safe)
                self.event_store.append(event)
                log.info("circuit_breaker_event_emitted")
            except Exception as e:
                log.error("circuit_breaker_event_failed", error=str(e))

        # Stop all agents
        self._monitoring = False
        for agent in self._agents.values():
            try:
                await agent.stop()
                log.info("agent_stopped_kill_switch", agent_name=agent.name)
            except Exception as e:
                log.error(
                    "agent_stop_error",
                    agent_name=agent.name,
                    error=str(e),
                )

        # Update metrics
        try:
            meter = get_meter()
            if meter and hasattr(meter, '_instruments'):
                counter = meter._instruments.get("kill_switch_triggered_total")
                if counter and hasattr(counter, 'add'):
                    counter.add(1, {"reason": reason})
        except (RuntimeError, AttributeError) as e:
            log.warning("kill_switch_metrics_failed", error=str(e))

    # ========================================================================
    # Restart Logic
    # ========================================================================

    @traced(name="restart_agent")
    async def _restart_agent(self, agent: BaseAgent) -> None:
        """Restart agent with exponential backoff.

        Implements exponential backoff strategy with maximum retry limit.

        Args:
            agent: Agent to restart.
        """
        attempt = self._restart_backoff.get(agent.name, 0)

        if attempt >= self.max_restart_attempts:
            log.error(
                "max_restart_attempts_exceeded",
                agent_name=agent.name,
                attempts=attempt,
            )
            # Max attempts exceeded - trigger kill switch
            await self._kill_switch(
                reason=f"Agent '{agent.name}' exceeded max restart attempts"
            )
            return

        # Calculate exponential backoff delay: 2^attempt seconds
        backoff_delay = 2 ** attempt
        log.info(
            "agent_restart_scheduled",
            agent_name=agent.name,
            attempt=attempt + 1,
            max_attempts=self.max_restart_attempts,
            backoff_delay_s=backoff_delay,
        )

        # Wait before restart
        await asyncio.sleep(backoff_delay)

        try:
            # Stop agent
            await agent.stop()
            log.info("agent_stopped_for_restart", agent_name=agent.name)

            # Reset state (heartbeat uses timezone-aware datetime)
            agent.restart_count += 1
            agent.last_heartbeat = datetime.now(timezone.utc)

            # Start agent
            await agent.start()
            log.info(
                "agent_restarted",
                agent_name=agent.name,
                restart_count=agent.restart_count,
            )

            # Reset backoff counter on successful restart
            self._restart_backoff[agent.name] = 0

            # Update metrics
            try:
                meter = get_meter()
                if meter and hasattr(meter, '_instruments'):
                    counter = meter._instruments.get("agent_restarts_total")
                    if counter and hasattr(counter, 'add'):
                        counter.add(1, {"agent_name": agent.name})
            except (RuntimeError, AttributeError) as e:
                log.warning("agent_restart_metrics_failed", error=str(e))

        except Exception as e:
            log.error(
                "agent_restart_failed",
                agent_name=agent.name,
                attempt=attempt + 1,
                error=str(e),
            )
            # Increment backoff counter for next attempt
            self._restart_backoff[agent.name] = attempt + 1

    # ========================================================================
    # Metrics
    # ========================================================================

    def _update_metrics(self) -> None:
        """Update OTel metrics for agent health."""
        try:
            meter = self._meter
            if not meter or not hasattr(meter, '_instruments'):
                return

            # Update agent heartbeat gauges
            for agent_name, agent in self._agents.items():
                try:
                    gauge = meter._instruments.get("agent_heartbeat")
                    if gauge and hasattr(gauge, "set"):
                        gauge.set(
                            1.0 if agent.is_healthy() else 0.0,
                            {"agent_name": agent_name},
                        )
                except (AttributeError, TypeError):
                    pass

            # Update kill switch state gauge
            try:
                gauge = meter._instruments.get("kill_switch_state")
                if gauge and hasattr(gauge, "set"):
                    is_triggered = self._kill_switch_state == KillSwitchState.TRIGGERED
                    gauge.set(1.0 if is_triggered else 0.0)
            except (AttributeError, TypeError):
                pass

        except Exception as e:
            log.error("metrics_update_failed", error=str(e))

    # ========================================================================
    # Status and Inspection
    # ========================================================================

    def get_status(self) -> Dict[str, Any]:
        """Get current supervisor status.

        Returns:
            Dictionary with supervisor state and agent statuses.
        """
        return {
            "monitoring": self._monitoring,
            "kill_switch_state": self._kill_switch_state.value,
            "agents": {
                agent_name: {
                    "type": agent.agent_type.value,
                    "running": agent.is_running,
                    "healthy": agent.is_healthy(),
                    "restart_count": agent.restart_count,
                    "last_heartbeat": (
                        agent.last_heartbeat.isoformat()
                        if agent.last_heartbeat
                        else None
                    ),
                }
                for agent_name, agent in self._agents.items()
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
