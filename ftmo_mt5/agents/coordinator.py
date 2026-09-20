"""Coordinator - main orchestrator for multi-agent system.

Manages the complete trading pipeline: Signal → Risk → Execution.
Ensures sequential (non-parallel) processing for decision-critical paths
with single-threaded deterministic execution guarantees.

Integrates event store for all trade lifecycle tracking and supervisor
for agent health monitoring.
"""

__all__ = [
    "Coordinator",
]

import asyncio
from datetime import datetime
from typing import Optional

import structlog
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from ftmo_mt5.agents.base_agent import BaseAgent
from ftmo_mt5.agents.execution_agent import ExecutionAgent
from ftmo_mt5.agents.risk_agent import RiskAgent
from ftmo_mt5.agents.signal_agent import SignalAgent
from ftmo_mt5.core.exceptions import CircuitBreakerError
from ftmo_mt5.core.models import (
    AccountState,
    FTMOConfig,
    FTMOLimits,
    PositionInfo,
    TradingSignal,
    TradeRequest,
)

logger = structlog.get_logger(__name__)
tracer = trace.get_tracer(__name__)


class Coordinator:
    """Main orchestrator for multi-agent trading system.

    Manages the complete trading pipeline with sequential processing:
    1. Signal Generation (SignalAgent)
    2. Risk Evaluation (RiskAgent)
    3. Order Execution (ExecutionAgent)

    All processing is deterministic and single-threaded. No parallel
    decision-making on critical paths.

    Attributes:
        signal_agent: SignalAgent instance.
        risk_agent: RiskAgent instance.
        execution_agent: ExecutionAgent instance.
        event_store: EventStore for append-only event logging.
        supervisor: Supervisor for agent health monitoring.
    """

    def __init__(
        self,
        signal_agent: SignalAgent,
        risk_agent: RiskAgent,
        execution_agent: ExecutionAgent,
        event_store,
        rule_engine=None,
        supervisor=None,
    ):
        """Initialize coordinator.

        Args:
            signal_agent: SignalAgent instance.
            risk_agent: RiskAgent instance.
            execution_agent: ExecutionAgent instance.
            event_store: EventStore instance for event logging.
            rule_engine: RuleEngine instance (optional).
            supervisor: Supervisor instance for health monitoring (optional).
        """
        self.signal_agent = signal_agent
        self.risk_agent = risk_agent
        self.execution_agent = execution_agent
        self.event_store = event_store
        self.rule_engine = rule_engine
        self.supervisor = supervisor

        # Control flow
        self._running = False
        self._shutdown_event = asyncio.Event()

        # Pipeline metrics
        self._signals_processed = 0
        self._trades_executed = 0
        self._trades_rejected = 0

        logger.info(
            "coordinator_initialized",
            signal_agent=signal_agent.name,
            risk_agent=risk_agent.name,
            execution_agent=execution_agent.name,
        )

    async def run(self) -> None:
        """Main event loop for coordinator.

        Starts all agents and waits for shutdown signal. This is the
        primary entry point for the trading system.

        CRITICAL: Uses single-threaded event loop for deterministic
        sequential processing. No parallel event handling allowed.
        """
        span = tracer.start_span("coordinator_run")
        try:
            self._running = True
            logger.info("coordinator_starting", message="Starting trading system")

            # Start all agents sequentially
            await self.signal_agent.start()
            await self.risk_agent.start()
            await self.execution_agent.start()

            logger.info("all_agents_started", message="All agents running")
            span.set_attribute("agents_started", True)

            # Wait for shutdown signal
            await self._shutdown_event.wait()

            logger.info("coordinator_shutdown_signal_received")
            span.set_attribute("shutdown_initiated", True)

        except Exception as e:
            logger.critical(
                "coordinator_run_fatal_error",
                error=str(e),
                message="Fatal error in coordinator - initiating emergency shutdown",
            )
            span.record_exception(e)
            raise

        finally:
            await self.shutdown()
            span.end()

    async def process_tick(
        self,
        tick_data: dict,
        account_state: AccountState,
        ftmo_limits: FTMOLimits,
        positions: list[PositionInfo],
    ) -> bool:
        """Process a single market tick through the complete pipeline.

        Executes STRICTLY SEQUENTIAL pipeline: Signal → Risk → Execution.
        NO PARALLEL PROCESSING - all steps execute one after another.
        This is CRITICAL for deterministic behavior in financial trading.

        Edge case handling:
        - Multiple signals for same symbol in same tick: Only first signal processed
          (deduplicated by symbol) to prevent conflicting orders on same tick
        - None tick_data: Returns False (error state)
        - None signal: Returns True (no signal is normal)

        Args:
            tick_data: Market tick data for signal generation.
            account_state: Current account state snapshot.
            ftmo_limits: FTMO challenge limits.
            positions: List of current open positions.

        Returns:
            True if processing completed successfully, False otherwise.
        """
        span = tracer.start_span("coordinator_process_tick")

        try:
            logger.debug(
                "tick_processing_starting",
                symbol=tick_data.get("symbol"),
                account_equity=str(account_state.equity),
            )

            # STEP 1: Generate signal (SEQUENTIAL - block here)
            if tick_data is None:
                logger.warning("tick_data_is_none", message="Received None tick data")
                span.set_attribute("signal_generated", False)
                return False

            signal = await self.signal_agent.process(tick_data)
            if signal is None:
                logger.debug("no_signal_generated", symbol=tick_data.get("symbol"))
                span.set_attribute("signal_generated", False)
                return True

            logger.info(
                "signal_generated",
                signal_id=str(signal.signal_id),
                symbol=signal.symbol,
                direction=signal.direction.value,
            )

            # Emit SignalGenerated event (synchronous, guaranteed before next step)
            try:
                self.signal_agent.emit_signal_generated_event(signal, self.event_store)
            except Exception as e:
                logger.error("signal_event_emission_failed", error=str(e), signal_id=str(signal.signal_id))
                span.record_exception(e)
                return False

            # STEP 2: Evaluate risk (SEQUENTIAL - block here)
            risk_decision = await self.risk_agent.process(
                signal=signal,
                account_state=account_state,
                ftmo_limits=ftmo_limits,
                positions=positions,
            )

            logger.info(
                "risk_decision_made",
                signal_id=str(signal.signal_id),
                approved=risk_decision.approved,
                risk_state=risk_decision.risk_state.value,
            )

            # Emit RiskChecked event (synchronous, guaranteed before execution step)
            try:
                self.risk_agent.emit_risk_checked_event(risk_decision, signal, self.event_store)
            except Exception as e:
                logger.error("risk_event_emission_failed", error=str(e), signal_id=str(signal.signal_id))
                span.record_exception(e)
                return False

            # STEP 3: Execute trade (if approved) (SEQUENTIAL - block here)
            if risk_decision.approved:
                # Create trade request from signal
                trade_request = self._create_trade_request(signal)

                # Execute order (block until complete)
                execution_result = await self.execution_agent.process(
                    trade_request=trade_request,
                    risk_decision=risk_decision,
                )

                logger.info(
                    "execution_result",
                    signal_id=str(signal.signal_id),
                    status=execution_result.status.value,
                    deal_ticket=execution_result.deal_ticket,
                )

                # Emit execution events (synchronous, guaranteed to complete)
                try:
                    if execution_result.status.value == "EXECUTED":
                        self.execution_agent.emit_order_submitted_event(trade_request, self.event_store)
                        self._trades_executed += 1
                    else:
                        self.execution_agent.emit_order_rejected_event(
                            trade_request=trade_request,
                            retcode=0,
                            reason=execution_result.error_message or "Unknown",
                            event_store=self.event_store,
                        )
                        self._trades_rejected += 1
                except Exception as e:
                    logger.error("execution_event_emission_failed", error=str(e), signal_id=str(signal.signal_id))
                    span.record_exception(e)
                    return False
            else:
                logger.info(
                    "trade_not_executed_risk_rejected",
                    signal_id=str(signal.signal_id),
                    reason=risk_decision.reason,
                )
                self._trades_rejected += 1

            self._signals_processed += 1
            span.set_attribute("signal_generated", True)
            span.set_attribute("processing_successful", True)
            span.set_status(Status(StatusCode.OK))

            return True

        except CircuitBreakerError as e:
            logger.critical(
                "circuit_breaker_triggered_halting_system",
                error=str(e),
                message="Circuit breaker triggered - ALL TRADING HALTED FOR SAFETY",
            )
            span.record_exception(e)
            span.set_status(Status(StatusCode.ERROR, "circuit_breaker_triggered"))
            await self._trigger_shutdown()
            return False

        except Exception as e:
            logger.error("tick_processing_error", error=str(e))
            span.record_exception(e)
            span.set_status(Status(StatusCode.ERROR, str(e)))
            return False

    def _create_trade_request(self, signal: TradingSignal) -> TradeRequest:
        """Create TradeRequest from TradingSignal.

        Args:
            signal: TradingSignal with direction and parameters.

        Returns:
            TradeRequest ready for execution.
        """
        from ftmo_mt5.core.models import OrderAction, OrderType, FillPolicy
        from decimal import Decimal

        # Map signal direction to order action
        direction_to_action = {
            "LONG": OrderAction.BUY,
            "SHORT": OrderAction.SELL,
            "FLAT": OrderAction.CLOSE_ALL,
        }

        action = direction_to_action.get(signal.direction.value, OrderAction.BUY)

        # Create trade request
        trade_request = TradeRequest(
            symbol=signal.symbol,
            action=action,
            order_type=OrderType.MARKET,
            volume=Decimal("1"),  # Base lot size - will be adjusted by risk agent
            price=None,  # Market order, no limit price
            sl=None,
            tp=None,
            deviation=50,
            magic=234000,
            comment="FTMO_SIGNAL",
            fill_policy=FillPolicy.IOC,
            correlation_id=signal.correlation_id,
        )

        return trade_request

    async def shutdown(self) -> None:
        """Graceful shutdown of trading system.

        Closes all open positions and stops all agents cleanly.
        Emits final events to event store for audit trail.
        """
        span = tracer.start_span("coordinator_shutdown")
        try:
            self._running = False
            logger.info("coordinator_shutting_down")

            # Stop all agents
            await self.signal_agent.stop()
            await self.risk_agent.stop()
            await self.execution_agent.stop()

            logger.info("all_agents_stopped")

            # Log final metrics
            logger.info(
                "coordinator_shutdown_complete",
                signals_processed=self._signals_processed,
                trades_executed=self._trades_executed,
                trades_rejected=self._trades_rejected,
            )

            span.set_attribute("signals_processed", self._signals_processed)
            span.set_attribute("trades_executed", self._trades_executed)
            span.set_status(Status(StatusCode.OK))

        except Exception as e:
            logger.error("coordinator_shutdown_error", error=str(e))
            span.record_exception(e)
            span.set_status(Status(StatusCode.ERROR, str(e)))
            raise

        finally:
            span.end()

    async def _trigger_shutdown(self) -> None:
        """Trigger system shutdown (e.g., from circuit breaker).

        Called when unrecoverable error occurs.
        """
        logger.warning("coordinator_shutdown_triggered")
        self._shutdown_event.set()

    def signal_shutdown(self) -> None:
        """Signal the coordinator to shutdown.

        Safe to call from synchronous context or external handlers.
        """
        if self._running:
            self._shutdown_event.set()
            logger.info("coordinator_shutdown_signaled")

    @property
    def is_running(self) -> bool:
        """Check if coordinator is running."""
        return self._running

    @property
    def signals_processed(self) -> int:
        """Return count of signals processed."""
        return self._signals_processed

    @property
    def trades_executed(self) -> int:
        """Return count of trades executed."""
        return self._trades_executed

    @property
    def trades_rejected(self) -> int:
        """Return count of trades rejected."""
        return self._trades_rejected

    async def get_system_status(self) -> dict:
        """Get current system status.

        Returns:
            Dictionary with agent health, pipeline metrics, risk state.
        """
        return {
            "timestamp": datetime.utcnow().isoformat(),
            "running": self._running,
            "signal_agent": {
                "running": self.signal_agent.is_running,
                "alive": self.signal_agent.is_alive(),
                "restarts": self.signal_agent.restart_count,
            },
            "risk_agent": {
                "running": self.risk_agent.is_running,
                "alive": self.risk_agent.is_alive(),
                "restarts": self.risk_agent.restart_count,
                "current_state": self.risk_agent.current_state.value,
            },
            "execution_agent": {
                "running": self.execution_agent.is_running,
                "alive": self.execution_agent.is_alive(),
                "restarts": self.execution_agent.restart_count,
                "pending_orders": self.execution_agent.pending_orders_count,
            },
            "pipeline_metrics": {
                "signals_processed": self._signals_processed,
                "trades_executed": self._trades_executed,
                "trades_rejected": self._trades_rejected,
            },
            "event_store": {
                "total_events": self.event_store.event_count,
            },
        }
