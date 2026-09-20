"""Position and account reconciliation engine.

Continuously monitors actual vs expected positions, detects discrepancies,
verifies order execution, and identifies ghost orders.
"""

__all__ = [
    "ReconciliationReport",
    "PositionReconciler",
]

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import AsyncGenerator, Optional

import structlog
from opentelemetry import metrics, trace

from nehanth.core.exceptions import ReconciliationError
from nehanth.core.models import ExecutionStatus, PositionInfo
from nehanth.execution.mt5_gateway import MT5Gateway

logger = structlog.get_logger(__name__)
otel_tracer = trace.get_tracer(__name__)

# Get or create meter
meter = metrics.get_meter(__name__)

# Create metrics
reconciliation_latency_histogram = meter.create_histogram(
    name="reconciliation_latency_ms",
    description="Latency of reconciliation cycle in milliseconds",
    unit="ms",
)

position_discrepancy_counter = meter.create_counter(
    name="position_discrepancies_total",
    description="Total position discrepancies detected",
)

ghost_order_counter = meter.create_counter(
    name="ghost_orders_total",
    description="Total ghost orders detected",
)

reconciliation_status_gauge = meter.create_gauge(
    name="reconciliation_healthy",
    description="Whether reconciliation is healthy (1=yes, 0=no)",
)


@dataclass
class ReconciliationReport:
    """Report from a reconciliation cycle."""

    expected_positions: dict[str, PositionInfo] = field(default_factory=dict)
    """Expected positions (from our tracking)."""

    actual_positions: dict[str, PositionInfo] = field(default_factory=dict)
    """Actual positions (from MT5)."""

    discrepancies: list[dict] = field(default_factory=list)
    """List of discrepancies found."""

    ghost_orders: list[dict] = field(default_factory=list)
    """Orders that don't match expected state."""

    timestamp: datetime = field(default_factory=datetime.utcnow)
    """Report timestamp."""

    is_healthy: bool = True
    """Whether reconciliation indicates healthy state."""


class PositionReconciler:
    """Position and account reconciliation engine."""

    def __init__(self, gateway: MT5Gateway):
        """Initialize PositionReconciler.

        Args:
            gateway: MT5Gateway instance for API calls.
        """
        self._gateway = gateway
        self._expected_positions: dict[int, PositionInfo] = {}

    @trace.trace_method
    def reconcile(self, magic: int) -> dict[str, PositionInfo]:
        """Reconcile actual positions from MT5 for a magic number.

        Args:
            magic: Magic number filter.

        Returns:
            Dict mapping symbol -> PositionInfo.

        Raises:
            ReconciliationError: If reconciliation fails.
        """
        try:
            positions = self._gateway.get_positions(magic=magic)
            result = {}

            for pos in positions:
                result[pos.symbol] = pos

            logger.info(
                "reconciliation_complete",
                magic=magic,
                position_count=len(result),
            )

            return result

        except Exception as e:
            logger.error("reconciliation_failed", magic=magic, error=str(e))
            raise ReconciliationError(
                f"Failed to reconcile positions for magic {magic}",
                magic=magic,
                error=str(e),
            )

    @trace.trace_method
    def verify_execution(self, order_ticket: int) -> ExecutionStatus:
        """Verify execution status of an order.

        Logic per PRD:
        - If deals exist: FILLED (order was executed)
        - Else if order history exists with state=1: CANCELLED (order rejected/cancelled)
        - Else if order history exists: PENDING (order still active)
        - Else: UNKNOWN (no record found - escalate)

        Args:
            order_ticket: Order ticket number.

        Returns:
            ExecutionStatus enum value.
        """
        try:
            span = trace.get_current_span()
            span.set_attribute("order_ticket", order_ticket)

            # First check deal history - deals indicate successful execution
            deals = self._gateway.history_deals_get(order_ticket)

            if deals:
                # Analyze deal volume
                total_volume = Decimal("0")
                for deal in deals:
                    total_volume += deal.get('volume', Decimal("0"))

                # Get order for comparison
                orders = self._gateway.history_orders_get(order_ticket)
                if orders:
                    order = orders[0]  # Most recent
                    requested_volume = Decimal(str(order.get('volume', 0)))

                    if total_volume >= requested_volume:
                        logger.info(
                            "reconciliation_order_filled",
                            ticket=order_ticket,
                            volume=str(total_volume),
                        )
                        return ExecutionStatus.EXECUTED

                    elif total_volume > Decimal("0"):
                        logger.info(
                            "reconciliation_order_partial",
                            ticket=order_ticket,
                            filled=str(total_volume),
                            requested=str(requested_volume),
                        )
                        return ExecutionStatus.PARTIAL

                else:
                    # Deals exist but no order record - filled
                    logger.info(
                        "reconciliation_order_filled_no_history",
                        ticket=order_ticket,
                        volume=str(total_volume),
                    )
                    return ExecutionStatus.EXECUTED

            # No deals - check order history
            orders = self._gateway.history_orders_get(order_ticket)

            if not orders:
                # No deals, no order history
                logger.critical(
                    "reconciliation_ghost_order_unknown",
                    ticket=order_ticket,
                )
                return ExecutionStatus.UNKNOWN

            order = orders[0]  # Most recent
            order_state = order.get('type', -1)

            # state=1 indicates cancelled/rejected order (per MT5 API)
            if order_state == 1:
                logger.warning(
                    "reconciliation_order_cancelled",
                    ticket=order_ticket,
                    state=order_state,
                )
                return ExecutionStatus.CANCELLED

            # Order exists but no deals = still pending
            logger.warning(
                "reconciliation_order_pending",
                ticket=order_ticket,
            )
            return ExecutionStatus.PENDING

        except Exception as e:
            logger.error("reconciliation_verify_execution_error", ticket=order_ticket, error=str(e))
            return ExecutionStatus.UNKNOWN

    @trace.trace_method
    def detect_ghost_orders(
        self,
        expected: dict[str, PositionInfo],
        actual: dict[str, PositionInfo],
    ) -> list[dict]:
        """Detect ghost orders (positions not in expected state).

        Args:
            expected: Expected positions dict.
            actual: Actual positions from MT5.

        Returns:
            List of ghost order dicts.
        """
        ghost_orders = []
        span = trace.get_current_span()

        # Check for positions in actual but not in expected
        for symbol, actual_pos in actual.items():
            if symbol not in expected:
                ghost_order = {
                    'type': 'unexpected_position',
                    'symbol': symbol,
                    'ticket': actual_pos.ticket,
                    'volume': actual_pos.volume,
                    'price_open': actual_pos.price_open,
                    'timestamp': datetime.utcnow(),
                }
                ghost_orders.append(ghost_order)

                logger.critical(
                    "reconciliation_ghost_order_detected",
                    symbol=symbol,
                    ticket=actual_pos.ticket,
                    volume=str(actual_pos.volume),
                    type="unexpected_position",
                )

        # Check for volume mismatches
        for symbol, expected_pos in expected.items():
            if symbol in actual:
                actual_pos = actual[symbol]
                if actual_pos.volume != expected_pos.volume:
                    discrepancy = {
                        'type': 'volume_mismatch',
                        'symbol': symbol,
                        'ticket': actual_pos.ticket,
                        'expected_volume': expected_pos.volume,
                        'actual_volume': actual_pos.volume,
                        'timestamp': datetime.utcnow(),
                    }
                    ghost_orders.append(discrepancy)

                    logger.critical(
                        "reconciliation_volume_mismatch",
                        symbol=symbol,
                        ticket=actual_pos.ticket,
                        expected=str(expected_pos.volume),
                        actual=str(actual_pos.volume),
                    )

        if ghost_orders:
            ghost_order_counter.add(len(ghost_orders))
            span.set_attribute("ghost_orders_detected", len(ghost_orders))

        return ghost_orders

    @trace.trace_method
    async def run_continuous_reconciliation(
        self,
        magic: int,
        interval_s: float = 5.0,
    ) -> AsyncGenerator[ReconciliationReport, None]:
        """Run continuous reconciliation at specified interval.

        Args:
            magic: Magic number filter.
            interval_s: Reconciliation interval in seconds.

        Yields:
            ReconciliationReport objects at each cycle.
        """
        logger.info(
            "reconciliation_continuous_start",
            magic=magic,
            interval_s=interval_s,
        )

        cycle_count = 0

        while True:
            # Initialize variables before try block to avoid NameError in except
            actual_by_symbol = {}
            expected_by_symbol = {}

            try:
                start_time = datetime.utcnow()
                cycle_count += 1

                span = trace.get_current_span()
                span.set_attribute("reconciliation_cycle", cycle_count)

                # Get actual positions from MT5
                actual_positions = self.reconcile(magic=magic)

                # Convert to symbol-indexed dict for easier comparison
                actual_by_symbol = {pos.symbol: pos for pos in actual_positions.values()}

                # Get expected positions (would come from position tracker in real system)
                expected_by_symbol = self._expected_positions.copy()

                # Detect discrepancies
                discrepancies = []
                for symbol, actual_pos in actual_by_symbol.items():
                    if symbol not in expected_by_symbol:
                        discrepancies.append({
                            'type': 'unexpected_position',
                            'symbol': symbol,
                            'ticket': actual_pos.ticket,
                        })

                # Detect ghost orders
                ghost_orders = self.detect_ghost_orders(
                    expected_by_symbol,
                    actual_by_symbol,
                )

                # Determine health
                is_healthy = len(discrepancies) == 0 and len(ghost_orders) == 0

                # Record metrics
                latency_ms = (datetime.utcnow() - start_time).total_seconds() * 1000
                reconciliation_latency_histogram.record(latency_ms)
                reconciliation_status_gauge.observe(1 if is_healthy else 0)

                if discrepancies:
                    position_discrepancy_counter.add(len(discrepancies))

                # Create report
                report = ReconciliationReport(
                    expected_positions=expected_by_symbol,
                    actual_positions=actual_by_symbol,
                    discrepancies=discrepancies,
                    ghost_orders=ghost_orders,
                    timestamp=datetime.utcnow(),
                    is_healthy=is_healthy,
                )

                logger.info(
                    "reconciliation_cycle_complete",
                    cycle=cycle_count,
                    actual_positions=len(actual_by_symbol),
                    expected_positions=len(expected_by_symbol),
                    discrepancies=len(discrepancies),
                    ghost_orders=len(ghost_orders),
                    healthy=is_healthy,
                    latency_ms=latency_ms,
                )

                if not is_healthy:
                    logger.critical(
                        "reconciliation_unhealthy",
                        discrepancies=len(discrepancies),
                        ghost_orders=len(ghost_orders),
                    )

                yield report

                # Sleep until next cycle
                await asyncio.sleep(interval_s)

            except Exception as e:
                logger.exception(
                    "reconciliation_cycle_error",
                    cycle=cycle_count,
                    error=str(e),
                )

                # Yield error report with safely initialized variables
                yield ReconciliationReport(
                    expected_positions=expected_by_symbol,
                    actual_positions=actual_by_symbol,
                    discrepancies=[],
                    ghost_orders=[],
                    timestamp=datetime.utcnow(),
                    is_healthy=False,
                )

                # Continue after error
                await asyncio.sleep(interval_s)

    def update_expected_positions(self, positions: dict[str, PositionInfo]) -> None:
        """Update expected position state (from position tracker).

        Args:
            positions: Dict mapping symbol -> PositionInfo.
        """
        self._expected_positions = positions
        logger.info("reconciliation_expected_positions_updated", count=len(positions))

    def add_expected_position(self, symbol: str, position: PositionInfo) -> None:
        """Add a position to expected state.

        Args:
            symbol: Trading symbol.
            position: PositionInfo object.
        """
        self._expected_positions[symbol] = position
        logger.debug("reconciliation_expected_position_added", symbol=symbol)

    def remove_expected_position(self, symbol: str) -> None:
        """Remove a position from expected state.

        Args:
            symbol: Trading symbol.
        """
        if symbol in self._expected_positions:
            del self._expected_positions[symbol]
            logger.debug("reconciliation_expected_position_removed", symbol=symbol)

    def get_expected_positions(self) -> dict[str, PositionInfo]:
        """Get current expected positions.

        Returns:
            Dict mapping symbol -> PositionInfo.
        """
        return self._expected_positions.copy()
