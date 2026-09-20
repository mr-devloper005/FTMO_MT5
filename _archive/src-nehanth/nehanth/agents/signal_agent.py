"""Signal generation agent for trading strategy signals.

Generates trading signals from market data with meta-labeling integration.
Only emits signals that pass meta-labeling probability threshold and
generates correlation IDs for trade lifecycle tracking.
"""

__all__ = [
    "SignalAgent",
]

from datetime import datetime
from decimal import Decimal
from typing import Optional
from uuid import uuid4

import structlog
from opentelemetry import trace

from nehanth.agents.base_agent import BaseAgent
from nehanth.core.models import SignalDirection, TradingSignal, SignalGenerated

logger = structlog.get_logger(__name__)
tracer = trace.get_tracer(__name__)


class SignalAgent(BaseAgent):
    """Agent responsible for generating trading signals.

    Processes market tick data and generates signals when:
    1. Strategy identifies a trading opportunity
    2. Meta-labeling probability exceeds configured threshold
    3. Signal passes all validation checks

    Attributes:
        strategy_name: Name of the strategy generating signals.
        symbols: List of symbols this agent monitors.
        meta_label_threshold: Minimum meta-label probability for signal emission (0-1).
    """

    def __init__(
        self,
        strategy_name: str,
        symbols: list[str],
        meta_label_threshold: Decimal = Decimal("0.6"),
        heartbeat_interval_s: int = 5,
        max_restarts: int = 3,
    ):
        """Initialize signal agent.

        Args:
            strategy_name: Name of the trading strategy.
            symbols: List of trading symbols to monitor.
            meta_label_threshold: Minimum probability for signal emission (default: 0.6).
            heartbeat_interval_s: Heartbeat interval in seconds (default: 5).
            max_restarts: Maximum consecutive restarts (default: 3).

        Raises:
            ValueError: If symbols list is empty.
        """
        if not symbols:
            raise ValueError("Signal agent must be initialized with at least one symbol")

        super().__init__(name="signal_agent", heartbeat_interval_s=heartbeat_interval_s, max_restarts=max_restarts)
        self.strategy_name = strategy_name
        self.symbols = symbols
        self.meta_label_threshold = Decimal(str(meta_label_threshold))

        logger.info(
            "signal_agent_initialized",
            strategy_name=strategy_name,
            symbols=symbols,
            meta_label_threshold=str(self.meta_label_threshold),
        )

    async def process(self, tick_data: dict) -> Optional[TradingSignal]:
        """Process market tick data and generate trading signal.

        Analyzes tick data and generates a signal if:
        1. Strategy detects a trading opportunity
        2. Signal passes meta-labeling probability check
        3. Signal data is valid

        Args:
            tick_data: Market tick data dictionary containing:
                - symbol: Trading symbol (str)
                - direction: SignalDirection (LONG/SHORT/FLAT)
                - confidence: Confidence level (Decimal, 0-1)
                - meta_label_prob: Meta-label probability (Optional[Decimal], 0-1)
                - timestamp: Signal generation time (datetime)

        Returns:
            TradingSignal if conditions are met and meta-label passes threshold,
            None otherwise.
        """
        span = tracer.start_as_current_span("signal_agent_process")
        self.heartbeat()

        try:
            symbol = tick_data.get("symbol")
            direction = tick_data.get("direction")
            confidence = Decimal(str(tick_data.get("confidence", Decimal("0.5"))))
            meta_label_prob = tick_data.get("meta_label_prob")
            timestamp = tick_data.get("timestamp", datetime.utcnow())

            logger.debug(
                "signal_agent_processing",
                symbol=symbol,
                direction=direction,
                confidence=str(confidence),
                meta_label_prob=str(meta_label_prob) if meta_label_prob else None,
            )

            # Validate symbol is in monitored list
            if symbol not in self.symbols:
                logger.debug("signal_ignored_unknown_symbol", symbol=symbol)
                span.set_attribute("signal_generated", False)
                span.set_attribute("reason", "unknown_symbol")
                return None

            # Check meta-labeling probability if available
            if meta_label_prob is not None:
                meta_label_prob = Decimal(str(meta_label_prob))
                if meta_label_prob <= self.meta_label_threshold:
                    logger.debug(
                        "signal_rejected_meta_label_threshold",
                        symbol=symbol,
                        meta_label_prob=str(meta_label_prob),
                        threshold=str(self.meta_label_threshold),
                    )
                    span.set_attribute("signal_generated", False)
                    span.set_attribute("reason", "meta_label_threshold")
                    return None

            # Generate correlation ID for trade lifecycle tracking
            correlation_id = uuid4()

            # Create signal
            signal = TradingSignal(
                signal_id=uuid4(),
                symbol=symbol,
                direction=direction,
                confidence=confidence,
                timestamp=timestamp,
                correlation_id=correlation_id,
                meta_label_prob=meta_label_prob,
                strategy_name=self.strategy_name,
            )

            logger.info(
                "signal_generated",
                signal_id=str(signal.signal_id),
                correlation_id=str(correlation_id),
                symbol=symbol,
                direction=direction.value,
                confidence=str(confidence),
                meta_label_prob=str(meta_label_prob) if meta_label_prob else None,
            )

            span.set_attribute("signal_generated", True)
            span.set_attribute("signal_id", str(signal.signal_id))
            span.set_attribute("correlation_id", str(correlation_id))
            span.set_attribute("symbol", symbol)
            span.set_attribute("direction", direction.value)

            return signal

        except Exception as e:
            logger.error("signal_agent_error", error=str(e), tick_data=tick_data)
            span.record_exception(e)
            raise

    def emit_signal_generated_event(
        self, signal: TradingSignal, event_store
    ) -> None:
        """Emit SignalGenerated event to event store.

        Synchronous event emission - must be called from async context only.
        Called after a signal is successfully generated to record
        the event in the append-only event log.

        Args:
            signal: The generated TradingSignal.
            event_store: EventStore instance to append event to.
        """
        try:
            event = SignalGenerated(
                timestamp=signal.timestamp,
                correlation_id=signal.correlation_id,
                signal=signal,
            )
            event_store.append(event)
            logger.info("signal_generated_event_emitted", signal_id=str(signal.signal_id))
        except Exception as e:
            logger.error("signal_generated_event_emission_failed", error=str(e))
            raise
