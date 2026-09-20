"""MT5 order execution engine with retry logic and comprehensive metrics.

Implements the retry strategy from the PRD:
- SUCCESS_CODES: {10000, 10001} -> return success
- RETRYABLE_PRICE: {10004, 10020} -> immediate retry with fresh price
- RETRYABLE_BACKOFF: {10012, 10021, 10024, 10028, 10029, 10031} -> exponential backoff retry
- All others -> fatal, return error

Uses MT5Gateway for all API calls and emits OpenTelemetry metrics for monitoring.
"""

__all__ = [
    "ExecutionResult",
    "MT5Executor",
]

import asyncio
import time
from datetime import datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

import structlog
from opentelemetry import metrics, trace

if not hasattr(trace, 'trace_method'):
    def _noop_decorator(func):
        return func
    trace.trace_method = _noop_decorator

from opentelemetry.exporter.prometheus import PrometheusMetricReader
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource

from ftmo_mt5.core.constants import (
    MT5_FATAL_CODES,
    MT5_RETRYABLE_BACKOFF,
    MT5_RETRYABLE_PRICE,
    MT5_SUCCESS_CODES,
)
from ftmo_mt5.core.exceptions import MT5ConnectionError, MT5ExecutionError
from ftmo_mt5.core.models import ExecutionStatus, OrderAction, PositionInfo, TradeRequest
from ftmo_mt5.execution.mt5_gateway import MT5Gateway

logger = structlog.get_logger(__name__)
otel_tracer = trace.get_tracer(__name__)

# Get or create meter
meter = metrics.get_meter(__name__)

# Create metrics
order_latency_histogram = meter.create_histogram(
    name="mt5_order_latency_ms",
    description="Latency of MT5 order submission in milliseconds",
    unit="ms",
)

order_success_counter = meter.create_counter(
    name="mt5_order_success_total",
    description="Total successful order executions",
)

order_failure_counter = meter.create_counter(
    name="mt5_order_failure_total",
    description="Total failed order executions",
)

retry_counter = meter.create_counter(
    name="mt5_order_retries_total",
    description="Total order retry attempts",
)

position_close_latency_histogram = meter.create_histogram(
    name="mt5_position_close_latency_ms",
    description="Latency of position close operation in milliseconds",
    unit="ms",
)


class ExecutionResult:
    """Result of an order execution attempt."""

    def __init__(
        self,
        success: bool,
        retcode: int,
        order_ticket: Optional[int] = None,
        fill_price: Optional[Decimal] = None,
        volume_filled: Optional[Decimal] = None,
        slippage: Optional[Decimal] = None,
        error_message: Optional[str] = None,
        retry_count: int = 0,
        latency_ms: float = 0.0,
    ):
        """Initialize ExecutionResult.

        Args:
            success: Whether execution succeeded.
            retcode: MT5 return code.
            order_ticket: Order ticket if successful.
            fill_price: Price at which order was filled.
            volume_filled: Volume actually filled.
            slippage: Price slippage (requested - actual).
            error_message: Error message if failed.
            retry_count: Number of retries performed.
            latency_ms: Execution latency in milliseconds.
        """
        self.success = success
        self.retcode = retcode
        self.order_ticket = order_ticket
        self.fill_price = fill_price
        self.volume_filled = volume_filled
        self.slippage = slippage
        self.error_message = error_message
        self.retry_count = retry_count
        self.latency_ms = latency_ms

    def __repr__(self) -> str:
        return (
            f"ExecutionResult(success={self.success}, retcode={self.retcode}, "
            f"ticket={self.order_ticket}, retries={self.retry_count}, latency_ms={self.latency_ms})"
        )


class MT5Executor:
    """Order execution engine with retry logic and metrics."""

    def __init__(self, gateway: MT5Gateway):
        """Initialize MT5Executor.

        Args:
            gateway: MT5Gateway instance for API calls.
        """
        self._gateway = gateway
        self._min_backoff_s = 1.0
        self._max_backoff_s = 10.0

    async def send_order(
        self,
        trade_request: TradeRequest,
        max_retries: int = 3,
    ) -> ExecutionResult:
        """Send order to MT5 with retry logic.

        Implements the retry strategy from PRD:
        - SUCCESS_CODES (10000, 10001): return success
        - RETRYABLE_PRICE (10004, 10020): retry with fresh price
        - RETRYABLE_BACKOFF (10012, 10021, 10024, 10028, 10029, 10031): exponential backoff
        - All others: fatal error

        Args:
            trade_request: Trade request to execute.
            max_retries: Maximum retry attempts (must be >= 0).

        Returns:
            ExecutionResult with success/failure details.
        """
        start_time = time.time()
        span = trace.get_current_span()
        attempt = 0

        try:
            while attempt <= max_retries:
                try:
                    span.set_attribute("retry_attempt", attempt)
                    span.set_attribute("symbol", trade_request.symbol)
                    span.set_attribute("action", trade_request.action.value)

                    # Get fresh price for each attempt (blocking MT5 call wrapped in executor)
                    tick = await asyncio.get_event_loop().run_in_executor(None, self._gateway.get_tick, trade_request.symbol)
                    if tick is None:
                        logger.error("mt5_executor_no_tick", symbol=trade_request.symbol, attempt=attempt)
                        if attempt < max_retries:
                            attempt += 1
                            retry_counter.add(1)
                            await asyncio.sleep(0.1)
                            continue
                        raise MT5ExecutionError("No tick data available", retcode=-1)

                    # Build MT5 order request
                    request = self._build_order_request(trade_request, tick)

                    logger.info(
                        "mt5_executor_send_order",
                        symbol=trade_request.symbol,
                        action=trade_request.action.value,
                        volume=str(trade_request.volume),
                        attempt=attempt,
                    )

                    # Send order (blocking MT5 call wrapped in executor)
                    result = await asyncio.get_event_loop().run_in_executor(None, self._gateway.order_send, request)
                    retcode = result.get('retcode', -1)

                    # Check return code
                    if retcode in MT5_SUCCESS_CODES:
                        latency_ms = (time.time() - start_time) * 1000
                        order_latency_histogram.record(latency_ms)
                        order_success_counter.add(1)

                        execution_result = ExecutionResult(
                            success=True,
                            retcode=retcode,
                            order_ticket=result.get('order'),
                            fill_price=result.get('price'),
                            volume_filled=result.get('volume'),
                            slippage=Decimal("0"),  # Would calculate from requested vs actual
                            retry_count=attempt,
                            latency_ms=latency_ms,
                        )

                        logger.info(
                            "mt5_executor_success",
                            ticket=execution_result.order_ticket,
                            retcode=retcode,
                            retries=attempt,
                            latency_ms=latency_ms,
                        )

                        span.set_attribute("execution_status", "success")
                        span.set_attribute("order_ticket", execution_result.order_ticket or 0)
                        return execution_result

                    # Handle retryable errors
                    elif retcode in MT5_RETRYABLE_PRICE:
                        logger.warning(
                            "mt5_executor_retryable_price",
                            retcode=retcode,
                            attempt=attempt,
                            max_retries=max_retries,
                        )

                        if attempt < max_retries:
                            retry_counter.add(1)
                            attempt += 1
                            await asyncio.sleep(0.1)  # Brief pause for fresh price
                            continue
                        else:
                            # Max retries exhausted
                            latency_ms = (time.time() - start_time) * 1000
                            order_latency_histogram.record(latency_ms)
                            order_failure_counter.add(1)

                            execution_result = ExecutionResult(
                                success=False,
                                retcode=retcode,
                                error_message=f"Max retries ({max_retries}) exhausted for price-related error",
                                retry_count=attempt,
                                latency_ms=latency_ms,
                            )

                            span.set_attribute("execution_status", "failed")
                            span.set_attribute("failure_reason", "max_retries_price")
                            return execution_result

                    elif retcode in MT5_RETRYABLE_BACKOFF:
                        logger.warning(
                            "mt5_executor_retryable_backoff",
                            retcode=retcode,
                            attempt=attempt,
                            max_retries=max_retries,
                        )

                        if attempt < max_retries:
                            # Exponential backoff: min(1.0 * 2^attempt, 10.0)
                            backoff_s = min(self._min_backoff_s * (2 ** attempt), self._max_backoff_s)
                            logger.info("mt5_executor_backoff_sleep", backoff_s=backoff_s)
                            retry_counter.add(1)
                            await asyncio.sleep(backoff_s)
                            attempt += 1
                            continue
                        else:
                            # Max retries exhausted
                            latency_ms = (time.time() - start_time) * 1000
                            order_latency_histogram.record(latency_ms)
                            order_failure_counter.add(1)

                            execution_result = ExecutionResult(
                                success=False,
                                retcode=retcode,
                                error_message=f"Max retries ({max_retries}) exhausted for backoff error",
                                retry_count=attempt,
                                latency_ms=latency_ms,
                            )

                            span.set_attribute("execution_status", "failed")
                            span.set_attribute("failure_reason", "max_retries_backoff")
                            return execution_result

                    else:
                        # Fatal error
                        latency_ms = (time.time() - start_time) * 1000
                        order_latency_histogram.record(latency_ms)
                        order_failure_counter.add(1)

                        logger.error(
                            "mt5_executor_fatal_error",
                            retcode=retcode,
                            attempt=attempt,
                        )

                        execution_result = ExecutionResult(
                            success=False,
                            retcode=retcode,
                            error_message=f"Fatal error: MT5 return code {retcode}",
                            retry_count=attempt,
                            latency_ms=latency_ms,
                        )

                        span.set_attribute("execution_status", "failed")
                        span.set_attribute("failure_reason", "fatal_error")
                        return execution_result

                except MT5ConnectionError as e:
                    logger.error("mt5_executor_connection_error", error=str(e), attempt=attempt)
                    if attempt < max_retries:
                        retry_counter.add(1)
                        await asyncio.sleep(min(self._min_backoff_s * (2 ** attempt), self._max_backoff_s))
                        attempt += 1
                        continue

                    latency_ms = (time.time() - start_time) * 1000
                    order_latency_histogram.record(latency_ms)
                    order_failure_counter.add(1)

                    execution_result = ExecutionResult(
                        success=False,
                        retcode=-1,
                        error_message=f"MT5 connection failed: {str(e)}",
                        retry_count=attempt,
                        latency_ms=latency_ms,
                    )

                    span.set_attribute("execution_status", "failed")
                    span.set_attribute("failure_reason", "connection_error")
                    return execution_result

        except Exception as e:
            logger.exception("mt5_executor_unexpected_error", error=str(e))
            latency_ms = (time.time() - start_time) * 1000
            order_latency_histogram.record(latency_ms)
            order_failure_counter.add(1)

            execution_result = ExecutionResult(
                success=False,
                retcode=-1,
                error_message=f"Unexpected error: {str(e)}",
                retry_count=attempt,
                latency_ms=latency_ms,
            )

            span.set_attribute("execution_status", "failed")
            span.set_attribute("failure_reason", "unexpected_error")
            return execution_result

        # Should not reach here
        latency_ms = (time.time() - start_time) * 1000
        return ExecutionResult(
            success=False,
            retcode=-1,
            error_message="Unknown error",
            retry_count=attempt,
            latency_ms=latency_ms,
        )

    @trace.trace_method
    def close_position(
        self,
        position: PositionInfo,
        reason: str = "user_request",
    ) -> ExecutionResult:
        """Close an open position.

        Args:
            position: Position to close.
            reason: Reason for closure.

        Returns:
            ExecutionResult with closure status.
        """
        logger.info(
            "mt5_executor_close_position",
            ticket=position.ticket,
            symbol=position.symbol,
            reason=reason,
        )

        # Get current tick for close price
        tick = self._gateway.get_tick(position.symbol)
        if tick is None:
            logger.error("mt5_executor_close_no_tick", symbol=position.symbol)
            return ExecutionResult(
                success=False,
                retcode=-1,
                error_message="No tick data available for close",
            )

        # Build close order
        close_price = tick['bid'] if position.type == 'LONG' else tick['ask']

        request = {
            'action': 3,  # TRADE_ACTION_DEAL (close)
            'symbol': position.symbol,
            'volume': float(position.volume),
            'type': 1 if position.type == 'LONG' else 0,  # Opposite type to close
            'price': float(close_price),
            'deviation': 20,
            'magic': position.magic or 0,
            'comment': f"close_{reason}",
            'type_filling': 2,  # FOK - Fill or Kill
        }

        result = self.send_order_raw(request, max_retries=3)
        return result

    @trace.trace_method
    def close_all_positions(
        self,
        magic: int,
        reason: str = "user_request",
    ) -> list[ExecutionResult]:
        """Close all open positions for a magic number.

        Args:
            magic: Magic number filter.
            reason: Reason for closure.

        Returns:
            List of ExecutionResults for each position closed.
        """
        logger.info("mt5_executor_close_all_positions", magic=magic, reason=reason)

        positions = self._gateway.get_positions(magic=magic)
        results = []

        for position in positions:
            result = self.close_position(position, reason=reason)
            results.append(result)

        logger.info(
            "mt5_executor_close_all_complete",
            magic=magic,
            closed_count=sum(1 for r in results if r.success),
            failed_count=sum(1 for r in results if not r.success),
        )

        return results

    @trace.trace_method
    def modify_position(
        self,
        ticket: int,
        sl: Optional[Decimal] = None,
        tp: Optional[Decimal] = None,
    ) -> ExecutionResult:
        """Modify stop loss and/or take profit for a position.

        Args:
            ticket: Position ticket.
            sl: New stop loss price.
            tp: New take profit price.

        Returns:
            ExecutionResult with modification status.
        """
        logger.info(
            "mt5_executor_modify_position",
            ticket=ticket,
            sl=str(sl) if sl else None,
            tp=str(tp) if tp else None,
        )

        request = {
            'action': 2,  # TRADE_ACTION_SLTP (modify stops)
            'position': ticket,
            'sl': float(sl) if sl else 0.0,
            'tp': float(tp) if tp else 0.0,
        }

        try:
            result = self._gateway.order_send(request)
            retcode = result.get('retcode', -1)

            if retcode in MT5_SUCCESS_CODES:
                logger.info("mt5_executor_modify_success", ticket=ticket, retcode=retcode)
                return ExecutionResult(
                    success=True,
                    retcode=retcode,
                    order_ticket=ticket,
                )
            else:
                logger.error("mt5_executor_modify_failed", ticket=ticket, retcode=retcode)
                return ExecutionResult(
                    success=False,
                    retcode=retcode,
                    error_message=f"Modify failed with retcode {retcode}",
                )

        except Exception as e:
            logger.exception("mt5_executor_modify_error", ticket=ticket, error=str(e))
            return ExecutionResult(
                success=False,
                retcode=-1,
                error_message=f"Modify error: {str(e)}",
            )

    def send_order_raw(self, request: dict, max_retries: int = 3) -> ExecutionResult:
        """Send raw order request (internal use for close operations).

        Args:
            request: Raw MT5 order request dict.
            max_retries: Maximum retries.

        Returns:
            ExecutionResult.
        """
        start_time = time.time()
        attempt = 0

        while attempt <= max_retries:
            try:
                result = self._gateway.order_send(request)
                retcode = result.get('retcode', -1)

                if retcode in MT5_SUCCESS_CODES:
                    latency_ms = (time.time() - start_time) * 1000
                    return ExecutionResult(
                        success=True,
                        retcode=retcode,
                        order_ticket=result.get('order'),
                        fill_price=result.get('price'),
                        volume_filled=result.get('volume'),
                        retry_count=attempt,
                        latency_ms=latency_ms,
                    )

                elif retcode in MT5_RETRYABLE_PRICE and attempt < max_retries:
                    retry_counter.add(1)
                    attempt += 1
                    time.sleep(0.1)
                    continue

                elif retcode in MT5_RETRYABLE_BACKOFF and attempt < max_retries:
                    backoff_s = min(self._min_backoff_s * (2 ** attempt), self._max_backoff_s)
                    retry_counter.add(1)
                    time.sleep(backoff_s)
                    attempt += 1
                    continue

                else:
                    latency_ms = (time.time() - start_time) * 1000
                    return ExecutionResult(
                        success=False,
                        retcode=retcode,
                        error_message=f"MT5 error: retcode {retcode}",
                        retry_count=attempt,
                        latency_ms=latency_ms,
                    )

            except Exception as e:
                logger.error("mt5_executor_raw_send_error", error=str(e), attempt=attempt)
                if attempt < max_retries:
                    retry_counter.add(1)
                    attempt += 1
                    continue

                latency_ms = (time.time() - start_time) * 1000
                return ExecutionResult(
                    success=False,
                    retcode=-1,
                    error_message=str(e),
                    retry_count=attempt,
                    latency_ms=latency_ms,
                )

        latency_ms = (time.time() - start_time) * 1000
        return ExecutionResult(
            success=False,
            retcode=-1,
            error_message="Unknown error",
            retry_count=attempt,
            latency_ms=latency_ms,
        )

    def _build_order_request(self, trade_request: TradeRequest, tick: dict) -> dict:
        """Build MT5 order request from TradeRequest and current tick.

        Args:
            trade_request: Trade request model.
            tick: Current tick data.

        Returns:
            MT5 order request dict.
        """
        # Determine order type and price
        if trade_request.order_type.value == "MARKET":
            order_type = 1  # ORDER_TYPE_BUY or ORDER_TYPE_SELL
            price = tick['ask'] if trade_request.action == OrderAction.BUY else tick['bid']
        else:
            # Limit/stop orders
            order_type = 2 if trade_request.order_type.value == "LIMIT" else 4
            price = trade_request.price or (tick['ask'] if trade_request.action == OrderAction.BUY else tick['bid'])

        # Determine action type
        action_map = {
            OrderAction.BUY: 1,  # ORDER_TYPE_BUY
            OrderAction.SELL: 0,  # ORDER_TYPE_SELL
            OrderAction.CLOSE_LONG: 0,  # Close long = sell
            OrderAction.CLOSE_SHORT: 1,  # Close short = buy
        }

        request = {
            'action': 1,  # TRADE_ACTION_DEAL
            'symbol': trade_request.symbol,
            'volume': float(trade_request.volume),
            'type': action_map[trade_request.action],
            'price': float(price),
            'sl': float(trade_request.sl) if trade_request.sl else 0.0,
            'tp': float(trade_request.tp) if trade_request.tp else 0.0,
            'deviation': trade_request.deviation or 20,
            'magic': trade_request.magic,
            'comment': trade_request.comment,
            'type_filling': 2,  # FOK - Fill or Kill (required for FTMO)
        }

        return request
