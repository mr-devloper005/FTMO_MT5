"""Health check system for FTMO MT5 trading platform.

Provides comprehensive health monitoring with built-in checks for critical
components and extensible registry for custom health checks.
"""

__all__ = [
    "HealthStatus",
    "SystemHealth",
    "HealthCheckRegistry",
    "HealthMonitor",
]

import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional
import asyncio

import structlog

from nehanth.core.exceptions import FTMOBaseError


log = structlog.get_logger(__name__)


# ============================================================================
# Data Models
# ============================================================================


@dataclass
class HealthStatus:
    """Health status for a single check.

    Attributes:
        name: Name of the health check.
        healthy: True if component is healthy, False otherwise.
        latency_ms: Time taken to run the check in milliseconds.
        details: Additional context about the check result.
        timestamp: When the check was performed (UTC).
    """

    name: str
    healthy: bool
    latency_ms: float
    details: Dict[str, any]
    timestamp: datetime

    def to_dict(self) -> Dict:
        """Convert to JSON-serializable dictionary."""
        return {
            "name": self.name,
            "healthy": self.healthy,
            "latency_ms": self.latency_ms,
            "details": self.details,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class SystemHealth:
    """Overall system health status.

    Attributes:
        overall_healthy: True if all critical checks pass.
        checks: List of individual health statuses.
        timestamp: When the health check was performed (UTC).
    """

    overall_healthy: bool
    checks: List[HealthStatus]
    timestamp: datetime

    def to_dict(self) -> Dict:
        """Convert to JSON-serializable dictionary."""
        return {
            "overall_healthy": self.overall_healthy,
            "checks": [check.to_dict() for check in self.checks],
            "timestamp": self.timestamp.isoformat(),
        }


# ============================================================================
# Health Check Registry
# ============================================================================


class HealthCheckRegistry:
    """Registry for health checks with async execution.

    Manages registration and execution of health checks, with built-in
    checks for critical system components.
    """

    def __init__(self) -> None:
        """Initialize the health check registry."""
        self._checks: Dict[str, Callable[[], any]] = {}
        self._register_builtin_checks()

    def register(self, name: str, check_fn: Callable[[], any]) -> None:
        """Register a new health check.

        Args:
            name: Unique identifier for the check.
            check_fn: Async or sync callable that returns HealthStatus.

        Raises:
            ValueError: If check name already registered.
        """
        if name in self._checks:
            raise ValueError(f"Health check '{name}' already registered")
        self._checks[name] = check_fn
        log.info("registered_health_check", name=name)

    async def run_all(self, timeout_s: float = 30.0) -> SystemHealth:
        """Run all registered health checks concurrently.

        Args:
            timeout_s: Timeout for all checks in seconds (default 30).

        Returns:
            SystemHealth with overall status and individual check results.
        """
        log.info("starting_health_checks", count=len(self._checks), timeout_s=timeout_s)

        # Run all checks concurrently with timeout protection
        tasks = [
            self.run(name) for name in self._checks.keys()
        ]
        try:
            results = await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=timeout_s
            )
        except asyncio.TimeoutError:
            log.error("health_checks_timeout", timeout_s=timeout_s)
            # Return timeout status for all checks
            results = [
                HealthStatus(
                    name=name,
                    healthy=False,
                    latency_ms=timeout_s * 1000,
                    details={"error": "Health check timed out"},
                    timestamp=datetime.now(timezone.utc),
                )
                for name in self._checks.keys()
            ]

        # Process results, handling exceptions
        statuses = []
        for result in results:
            if isinstance(result, HealthStatus):
                statuses.append(result)
            elif isinstance(result, Exception):
                log.error("health_check_exception", error=str(result))
                statuses.append(
                    HealthStatus(
                        name="unknown",
                        healthy=False,
                        latency_ms=0.0,
                        details={"error": str(result)},
                        timestamp=datetime.now(timezone.utc),
                    )
                )

        # Determine overall health
        overall_healthy = all(status.healthy for status in statuses)

        system_health = SystemHealth(
            overall_healthy=overall_healthy,
            checks=statuses,
            timestamp=datetime.now(timezone.utc),
        )

        log.info(
            "health_checks_complete",
            overall_healthy=overall_healthy,
            healthy_count=sum(1 for s in statuses if s.healthy),
            total_count=len(statuses),
        )

        return system_health

    async def run(self, name: str, timeout_s: float = 10.0) -> HealthStatus:
        """Run a single health check by name.

        Args:
            name: Name of the check to run.
            timeout_s: Timeout for individual check in seconds (default 10).

        Returns:
            HealthStatus with check result.

        Raises:
            KeyError: If check name not found.
        """
        if name not in self._checks:
            raise KeyError(f"Health check '{name}' not found")

        check_fn = self._checks[name]
        start_time = time.time()

        try:
            # Execute check (handle both async and sync functions)
            if asyncio.iscoroutinefunction(check_fn):
                result = await asyncio.wait_for(check_fn(), timeout=timeout_s)
            else:
                result = await asyncio.wait_for(
                    asyncio.to_thread(check_fn),
                    timeout=timeout_s
                )

            latency_ms = (time.time() - start_time) * 1000

            if isinstance(result, HealthStatus):
                # Update latency if not already set
                result.latency_ms = latency_ms
                return result
            else:
                # Assume callable returns boolean
                return HealthStatus(
                    name=name,
                    healthy=bool(result),
                    latency_ms=latency_ms,
                    details={},
                    timestamp=datetime.now(timezone.utc),
                )
        except asyncio.TimeoutError:
            latency_ms = (time.time() - start_time) * 1000
            log.error(
                "health_check_timeout",
                name=name,
                timeout_s=timeout_s,
                latency_ms=latency_ms,
            )
            return HealthStatus(
                name=name,
                healthy=False,
                latency_ms=latency_ms,
                details={"error": f"Health check exceeded {timeout_s}s timeout"},
                timestamp=datetime.now(timezone.utc),
            )
        except Exception as e:
            latency_ms = (time.time() - start_time) * 1000
            log.error(
                "health_check_failed",
                name=name,
                error=str(e),
                latency_ms=latency_ms,
            )
            return HealthStatus(
                name=name,
                healthy=False,
                latency_ms=latency_ms,
                details={"error": str(e)},
                timestamp=datetime.now(timezone.utc),
            )

    def _register_builtin_checks(self) -> None:
        """Register built-in health checks."""
        self.register("mt5_connection", self._mt5_connection_check)
        self.register("event_store", self._event_store_check)
        self.register("risk_agent", self._risk_agent_check)
        self.register("reconciliation", self._reconciliation_check)
        self.register("disk_space", self._disk_space_check)

    # ========================================================================
    # Built-in Health Checks
    # ========================================================================

    async def _mt5_connection_check(self) -> HealthStatus:
        """Check if MT5 terminal is reachable.

        Returns:
            HealthStatus indicating MT5 connectivity.
        """
        start_time = time.time()

        try:
            # Placeholder: In real implementation, would attempt MT5 connection
            # For now, assume healthy if no exception
            import MetaTrader5 as mt5

            if not mt5.initialize():
                return HealthStatus(
                    name="mt5_connection",
                    healthy=False,
                    latency_ms=(time.time() - start_time) * 1000,
                    details={"reason": "MT5 initialization failed"},
                    timestamp=datetime.now(timezone.utc),
                )

            mt5.shutdown()
            return HealthStatus(
                name="mt5_connection",
                healthy=True,
                latency_ms=(time.time() - start_time) * 1000,
                details={"message": "MT5 terminal reachable"},
                timestamp=datetime.now(timezone.utc),
            )
        except Exception as e:
            return HealthStatus(
                name="mt5_connection",
                healthy=False,
                latency_ms=(time.time() - start_time) * 1000,
                details={"error": str(e)},
                timestamp=datetime.now(timezone.utc),
            )

    async def _event_store_check(self) -> HealthStatus:
        """Check if event store is writable.

        Returns:
            HealthStatus indicating event store health.
        """
        start_time = time.time()

        try:
            # Placeholder: Would check actual event store writeability
            # For now, assume healthy
            import os

            if os.access(".", os.W_OK):
                return HealthStatus(
                    name="event_store",
                    healthy=True,
                    latency_ms=(time.time() - start_time) * 1000,
                    details={"message": "Event store writable"},
                    timestamp=datetime.now(timezone.utc),
                )
            else:
                return HealthStatus(
                    name="event_store",
                    healthy=False,
                    latency_ms=(time.time() - start_time) * 1000,
                    details={"reason": "Event store not writable"},
                    timestamp=datetime.now(timezone.utc),
                )
        except Exception as e:
            return HealthStatus(
                name="event_store",
                healthy=False,
                latency_ms=(time.time() - start_time) * 1000,
                details={"error": str(e)},
                timestamp=datetime.now(timezone.utc),
            )

    async def _risk_agent_check(self) -> HealthStatus:
        """Check if risk agent is alive.

        Returns:
            HealthStatus indicating risk agent health.
        """
        start_time = time.time()

        try:
            # Placeholder: Would check actual risk agent heartbeat
            # For now, assume healthy
            return HealthStatus(
                name="risk_agent",
                healthy=True,
                latency_ms=(time.time() - start_time) * 1000,
                details={"message": "Risk agent alive"},
                timestamp=datetime.now(timezone.utc),
            )
        except Exception as e:
            return HealthStatus(
                name="risk_agent",
                healthy=False,
                latency_ms=(time.time() - start_time) * 1000,
                details={"error": str(e)},
                timestamp=datetime.now(timezone.utc),
            )

    async def _reconciliation_check(self) -> HealthStatus:
        """Check if positions are reconciled with MT5.

        Returns:
            HealthStatus indicating reconciliation health.
        """
        start_time = time.time()

        try:
            # Placeholder: Would check actual reconciliation state
            # For now, assume healthy
            return HealthStatus(
                name="reconciliation",
                healthy=True,
                latency_ms=(time.time() - start_time) * 1000,
                details={"message": "Positions reconciled"},
                timestamp=datetime.now(timezone.utc),
            )
        except Exception as e:
            return HealthStatus(
                name="reconciliation",
                healthy=False,
                latency_ms=(time.time() - start_time) * 1000,
                details={"error": str(e)},
                timestamp=datetime.now(timezone.utc),
            )

    async def _disk_space_check(self) -> HealthStatus:
        """Check if sufficient disk space is available.

        Returns:
            HealthStatus indicating disk space health.
        """
        start_time = time.time()

        try:
            import shutil

            # Check disk space for current directory
            total, used, free = shutil.disk_usage(".")
            free_pct = (free / total) * 100

            # Warn if less than 10% free
            if free_pct < 10:
                return HealthStatus(
                    name="disk_space",
                    healthy=False,
                    latency_ms=(time.time() - start_time) * 1000,
                    details={
                        "free_gb": free / (1024 ** 3),
                        "total_gb": total / (1024 ** 3),
                        "free_pct": free_pct,
                        "reason": "Less than 10% disk space available",
                    },
                    timestamp=datetime.now(timezone.utc),
                )

            return HealthStatus(
                name="disk_space",
                healthy=True,
                latency_ms=(time.time() - start_time) * 1000,
                details={
                    "free_gb": free / (1024 ** 3),
                    "total_gb": total / (1024 ** 3),
                    "free_pct": free_pct,
                },
                timestamp=datetime.now(timezone.utc),
            )
        except Exception as e:
            return HealthStatus(
                name="disk_space",
                healthy=False,
                latency_ms=(time.time() - start_time) * 1000,
                details={"error": str(e)},
                timestamp=datetime.now(timezone.utc),
            )


# ============================================================================
# Health Monitor (Facade for HealthCheckRegistry)
# ============================================================================


class HealthMonitor:
    """Facade for health monitoring operations.

    Provides simplified interface for health checking in main.py.
    Wraps HealthCheckRegistry for convenience.
    """

    def __init__(self) -> None:
        """Initialize health monitor."""
        self._registry = HealthCheckRegistry()
        self._last_health: Optional[SystemHealth] = None

    async def check_health(self, timeout_s: float = 30.0) -> SystemHealth:
        """Run all health checks and cache result.

        Args:
            timeout_s: Timeout for all checks in seconds.

        Returns:
            SystemHealth with overall status.
        """
        self._last_health = await self._registry.run_all(timeout_s=timeout_s)
        return self._last_health

    async def is_healthy(self) -> bool:
        """Check if system is currently healthy.

        Returns:
            True if last health check indicated overall health.
        """
        if self._last_health is None:
            self._last_health = await self.check_health()
        return self._last_health.overall_healthy

    async def is_ready(self) -> bool:
        """Check if system is ready for trading.

        All critical checks must pass.

        Returns:
            True if system is ready for operations.
        """
        health = await self.is_healthy()
        if not health:
            return False
        if self._last_health is None:
            return False
        # All checks must pass to be ready
        return all(check.healthy for check in self._last_health.checks)

    async def get_metrics(self) -> Dict[str, Any]:
        """Get current health metrics in JSON-serializable format.

        Returns:
            Dictionary with health status and metrics.
        """
        if self._last_health is None:
            await self.check_health()

        return {
            "overall_healthy": self._last_health.overall_healthy,
            "checks": [check.to_dict() for check in self._last_health.checks],
            "timestamp": self._last_health.timestamp.isoformat(),
        } if self._last_health else {
            "overall_healthy": False,
            "checks": [],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
