"""Execution agent for order submission and position management.

Handles MT5 order submission, position closure, and execution tracking.
Validates risk decisions before executing trades and manages
order lifecycle with event emission.
"""

__all__ = [
    "ExecutionResult",
    "ExecutionAgent",
]

from datetime import datetime
from decimal import Decimal
from typing import Optional

import structlog
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from nehanth.agents.base_agent import BaseAgent
from nehanth.agents.risk_agent import RiskDecision
from nehanth.core.models import (
    ExecutionStatus,
    OrderFilled,
    OrderRejected,
    OrderSubmitted,
    PositionClosed,
    TradeRequest,
)

logger = structlog.get_logger(__name__)
tracer = trace.get_tracer(__name__)


class ExecutionResult:
    """Result of execution attempt.

    Attributes:
        status: ExecutionStatus (PENDING, EXECUTED, REJECTED, etc).
        trade_request: Original trade request.
        deal_ticket: MT5 deal ticket if executed.
        error_message: Error description if rejected.
    """

    def __init__(
        self,
        status: ExecutionStatus,
        trade_request: TradeRequest,
        deal_ticket: Optional[int] = None,
        error_message: Optional[str] = None,
    ):
        self.status = status
        self.trade_request = trade_request
        self.deal_ticket = deal_ticket
        self.error_message = error_message


class ExecutionAgent(BaseAgent):
    """Agent responsible for order submission and execution.

    Processes trade requests from risk agent, applies risk-adjusted
    position sizing, and manages order lifecycle with MT5.

    Attributes:
        mt5_executor: MT5Executor instance for broker communication.
    """

    def __init__(
        self,
        mt5_executor=None,
        heartbeat_interval_s: int = 5,
        max_restarts: int = 3,
    ):
        """Initialize execution agent.

        Args:
            mt5_executor: MT5Executor instance (optional - may be injected later).
            heartbeat_interval_s: Heartbeat interval in seconds (default: 5).
            max_restarts: Maximum consecutive restarts (default: 3).
        """
        super().__init__(name="execution_agent", heartbeat_interval_s=heartbeat_interval_s, max_restarts=max_restarts)
        self.mt5_executor = mt5_executor

        # Track pending orders for reconciliation
        self._pending_orders: dict[str, TradeRequest] = {}

        logger.info(
            "execution_agent_initialized",
            has_executor=mt5_executor is not None,
        )

    async def process(
        self,
        trade_request: TradeRequest,
        risk_decision: RiskDecision,
    ) -> ExecutionResult:
        """Execute a trade request subject to risk decision.

        Validates risk decision approval, applies position sizing adjustments,
        and submits order to MT5. Also handles position closures per risk agent.

        Args:
            trade_request: Trade request to execute.
            risk_decision: RiskDecision from risk agent evaluation.

        Returns:
            ExecutionResult with status and details.
        """
        span = tracer.start_as_current_span("execution_agent_process")
        self.heartbeat()

        try:
            logger.info(
                "execution_processing",
                trade_request_symbol=trade_request.symbol,
                trade_request_action=trade_request.action.value,
                risk_approved=risk_decision.approved,
            )

            # Validate risk decision approval
            if not risk_decision.approved:
                logger.info(
                    "execution_rejected_by_risk",
                    reason=risk_decision.reason,
                    trade_symbol=trade_request.symbol,
                )
                span.set_attribute("execution_status", ExecutionStatus.REJECTED.value)
                return ExecutionResult(
                    status=ExecutionStatus.REJECTED,
                    trade_request=trade_request,
                    error_message=f"Risk check rejected: {risk_decision.reason}",
                )

            # Handle positions that must be closed immediately
            if risk_decision.positions_to_close:
                logger.warning(
                    "closing_positions_per_risk_decision",
                    positions_count=len(risk_decision.positions_to_close),
                    reason=risk_decision.reason,
                )
                for position in risk_decision.positions_to_close:
                    await self._close_position(position)

            # Apply risk-adjusted volume
            adjusted_volume = trade_request.volume * risk_decision.adjusted_volume

            # If volume reduces to zero, reject
            if adjusted_volume <= Decimal("0"):
                logger.info(
                    "execution_rejected_zero_volume",
                    original_volume=str(trade_request.volume),
                    adjusted_volume=str(adjusted_volume),
                )
                span.set_attribute("execution_status", ExecutionStatus.REJECTED.value)
                return ExecutionResult(
                    status=ExecutionStatus.REJECTED,
                    trade_request=trade_request,
                    error_message="Adjusted volume is zero",
                )

            # Create adjusted trade request
            adjusted_request = trade_request.model_copy(update={"volume": adjusted_volume})

            # Submit to MT5 if executor available
            if not self.mt5_executor:
                logger.warning(
                    "execution_no_executor",
                    trade_symbol=adjusted_request.symbol,
                )
                span.set_attribute("execution_status", ExecutionStatus.PENDING.value)
                return ExecutionResult(
                    status=ExecutionStatus.PENDING,
                    trade_request=adjusted_request,
                    error_message="No MT5 executor available",
                )

            # Execute order
            result = await self._submit_order(adjusted_request)

            logger.info(
                "execution_completed",
                trade_symbol=adjusted_request.symbol,
                status=result.status.value,
                deal_ticket=result.deal_ticket,
            )

            span.set_attribute("execution_status", result.status.value)
            span.set_attribute("deal_ticket", str(result.deal_ticket) if result.deal_ticket else "none")
            span.set_status(Status(StatusCode.OK))

            return result

        except Exception as e:
            logger.error(
                "execution_agent_error",
                error=str(e),
                trade_symbol=trade_request.symbol,
            )
            span.record_exception(e)
            return ExecutionResult(
                status=ExecutionStatus.REJECTED,
                trade_request=trade_request,
                error_message=f"Execution error: {str(e)}",
            )

    async def _submit_order(self, trade_request: TradeRequest) -> ExecutionResult:
        """Submit order to MT5 broker.

        Args:
            trade_request: Trade request to submit.

        Returns:
            ExecutionResult with status and MT5 deal ticket if successful.
        """
        try:
            # Call MT5 executor (stub - actual implementation injected)
            deal_ticket = await self.mt5_executor.execute(trade_request)

            # Track pending order
            self._pending_orders[str(deal_ticket)] = trade_request

            logger.info(
                "order_submitted",
                symbol=trade_request.symbol,
                action=trade_request.action.value,
                volume=str(trade_request.volume),
                deal_ticket=deal_ticket,
            )

            return ExecutionResult(
                status=ExecutionStatus.EXECUTED,
                trade_request=trade_request,
                deal_ticket=deal_ticket,
            )

        except Exception as e:
            logger.error("order_submission_failed", error=str(e), symbol=trade_request.symbol)
            return ExecutionResult(
                status=ExecutionStatus.REJECTED,
                trade_request=trade_request,
                error_message=str(e),
            )

    async def _close_position(self, position) -> bool:
        """Close a specific position.

        This method is called from async context and properly awaits the MT5 executor.
        All position closures are executed sequentially in the order they're added.

        Args:
            position: PositionInfo to close.

        Returns:
            True if closure was successful.
        """
        try:
            logger.info(
                "closing_position",
                position_ticket=position.ticket,
                symbol=position.symbol,
                volume=str(position.volume),
            )

            if self.mt5_executor:
                await self.mt5_executor.close_position(position)
                logger.info("position_closed", position_ticket=position.ticket)
                return True

            return False

        except Exception as e:
            logger.error("position_closure_failed", error=str(e), position_ticket=position.ticket)
            return False

    def emit_order_submitted_event(
        self, trade_request: TradeRequest, event_store
    ) -> None:
        """Emit OrderSubmitted event to event store.

        Synchronous event emission - must be called from async context only.

        Args:
            trade_request: Submitted trade request.
            event_store: EventStore instance.
        """
        try:
            event = OrderSubmitted(
                timestamp=datetime.utcnow(),
                correlation_id=trade_request.correlation_id,
                trade_request=trade_request,
                submission_time=datetime.utcnow(),
            )
            event_store.append(event)
            logger.info("order_submitted_event_emitted", symbol=trade_request.symbol)
        except Exception as e:
            logger.error("order_submitted_event_emission_failed", error=str(e))
            raise

    def emit_order_filled_event(
        self,
        trade_request: TradeRequest,
        deal_ticket: int,
        fill_price: Decimal,
        slippage: Decimal,
        volume_filled: Decimal,
        event_store,
    ) -> None:
        """Emit OrderFilled event to event store.

        Args:
            trade_request: Original trade request.
            deal_ticket: MT5 deal ticket.
            fill_price: Actual fill price.
            slippage: Price difference from request to fill.
            volume_filled: Volume actually filled.
            event_store: EventStore instance.
        """
        try:
            event = OrderFilled(
                timestamp=datetime.utcnow(),
                correlation_id=trade_request.correlation_id,
                deal_ticket=deal_ticket,
                fill_price=fill_price,
                slippage=slippage,
                volume_filled=volume_filled,
            )
            event_store.append(event)
            logger.info("order_filled_event_emitted", deal_ticket=deal_ticket)
        except Exception as e:
            logger.error("order_filled_event_emission_failed", error=str(e))
            raise

    def emit_order_rejected_event(
        self,
        trade_request: TradeRequest,
        retcode: int,
        reason: str,
        event_store,
    ) -> None:
        """Emit OrderRejected event to event store.

        Args:
            trade_request: Original trade request.
            retcode: MT5 return code.
            reason: Human-readable rejection reason.
            event_store: EventStore instance.
        """
        try:
            event = OrderRejected(
                timestamp=datetime.utcnow(),
                correlation_id=trade_request.correlation_id,
                retcode=retcode,
                reason=reason,
            )
            event_store.append(event)
            logger.info("order_rejected_event_emitted", symbol=trade_request.symbol)
        except Exception as e:
            logger.error("order_rejected_event_emission_failed", error=str(e))
            raise

    @property
    def pending_orders_count(self) -> int:
        """Return count of pending orders."""
        return len(self._pending_orders)

    def get_pending_orders(self) -> dict[str, TradeRequest]:
        """Return dictionary of pending orders."""
        return dict(self._pending_orders)

    def mark_order_completed(self, deal_ticket: str) -> None:
        """Mark an order as completed (remove from pending).

        Args:
            deal_ticket: MT5 deal ticket to mark complete.
        """
        if deal_ticket in self._pending_orders:
            del self._pending_orders[deal_ticket]
            logger.info("order_marked_completed", deal_ticket=deal_ticket)
