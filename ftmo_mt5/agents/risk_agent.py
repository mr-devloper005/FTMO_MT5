"""Risk management agent with state machine.

Implements 4-state risk state machine (NORMAL → CAUTION → RISK_OFF → HALT)
with dynamic position sizing based on MDL headroom percentages.

State transitions based on MDL headroom:
  2-Step: CAUTION at 3% used, RISK_OFF at 4%, HALT at 4.5%
  1-Step: CAUTION at 1.8% used, RISK_OFF at 2.4%, HALT at 2.7%

If this agent crashes, ALL TRADING MUST HALT immediately (non-negotiable fail-safe).
"""

__all__ = [
    "RiskDecision",
    "RiskAgent",
]

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

import structlog
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from ftmo_mt5.agents.base_agent import BaseAgent
from ftmo_mt5.core.exceptions import CircuitBreakerError
from ftmo_mt5.core.models import (
    AccountState,
    FTMOConfig,
    FTMOLimits,
    PositionInfo,
    RiskState,
    RiskChecked,
    RiskStateChanged,
    TradingSignal,
)
from ftmo_mt5.engine.rule_engine import FTMORuleEngine

logger = structlog.get_logger(__name__)
tracer = trace.get_tracer(__name__)


@dataclass(frozen=True)
class RiskDecision:
    """Decision output from risk agent processing.

    Contains approval status, risk state, volume adjustments,
    and positions to close per risk agent logic.
    """

    approved: bool
    """Whether the trade is approved."""

    risk_state: RiskState
    """Current system risk state."""

    reason: str
    """Explanation of decision."""

    adjusted_volume: Decimal
    """Adjusted position size (may differ from requested)."""

    positions_to_close: list[PositionInfo]
    """Positions that must be closed immediately."""


class RiskAgent(BaseAgent):
    """Risk management agent with 4-state finite state machine.

    Manages trading risk through state transitions and position sizing.
    Enforces FTMO challenge limits and pre-midnight safety.

    State Machine:
        NORMAL (healthy) -> CAUTION (elevated risk) -> RISK_OFF (high risk)
                 ↓
               HALT (critical risk - all trading halted)

    Attributes:
        config: FTMO configuration with challenge parameters.
        rule_engine: FTMORuleEngine instance for risk calculations.
    """

    def __init__(
        self,
        config: FTMOConfig,
        rule_engine: FTMORuleEngine = None,
        heartbeat_interval_s: int = 5,
        max_restarts: int = 3,
    ):
        """Initialize risk agent.

        Args:
            config: FTMO configuration.
            rule_engine: FTMORuleEngine instance (default: create new).
            heartbeat_interval_s: Heartbeat interval in seconds (default: 5).
            max_restarts: Maximum consecutive restarts (default: 3).
        """
        super().__init__(name="risk_agent", heartbeat_interval_s=heartbeat_interval_s, max_restarts=max_restarts)
        self.config = config
        self.rule_engine = rule_engine or FTMORuleEngine()

        # State tracking - starts in NORMAL state
        self._current_state = RiskState.NORMAL

        logger.info(
            "risk_agent_initialized",
            challenge_type=config.challenge_type.value,
            mdl_pct=str(config.mdl_pct),
            ml_pct=str(config.ml_pct),
            initial_capital=str(config.initial_capital),
        )

    async def process(
        self,
        signal: TradingSignal,
        account_state: AccountState,
        ftmo_limits: FTMOLimits,
        positions: list[PositionInfo],
    ) -> RiskDecision:
        """Evaluate signal against risk limits and produce risk decision.

        Performs comprehensive risk checks including:
        1. MDL and ML breach detection
        2. Risk state determination based on headroom
        3. Position sizing adjustment per state
        4. Pre-midnight safety assessment
        5. Position closure requirements

        Args:
            signal: Trading signal to evaluate.
            account_state: Current account state.
            ftmo_limits: FTMO challenge limits.
            positions: Current open positions.

        Returns:
            RiskDecision with approval status, risk state, and adjustments.

        Raises:
            CircuitBreakerError: If risk limits are breached.
        """
        span = tracer.start_span("risk_agent_process")
        self.heartbeat()

        try:
            # Validate inputs are not None
            if signal is None or account_state is None or ftmo_limits is None:
                raise ValueError("Risk agent received None for critical input: signal, account_state, or ftmo_limits")

            if positions is None:
                positions = []

            logger.info(
                "risk_check_starting",
                signal_id=str(signal.signal_id),
                equity=str(account_state.equity),
                margin_ratio=str(
                    (account_state.margin / account_state.equity * Decimal("100"))
                    if account_state.equity > Decimal("0")
                    else Decimal("0")
                ),
            )

            # Run all FTMO risk checks
            risk_check = self.rule_engine.run_all_checks(
                account_state=account_state,
                ftmo_limits=ftmo_limits,
                config=self.config,
                positions=positions,
            )

            logger.info(
                "risk_check_completed",
                mdl_breached=risk_check.mdl_breached,
                ml_breached=risk_check.ml_breached,
                risk_state=risk_check.risk_state.value,
                mdl_headroom_pct=str(risk_check.mdl_headroom_pct),
            )

            # Detect state changes and emit events
            old_state = self._current_state
            new_state = risk_check.risk_state
            state_changed = old_state != new_state

            if state_changed:
                logger.warning(
                    "risk_state_changed",
                    old_state=old_state.value,
                    new_state=new_state.value,
                    reason=risk_check.breach_message,
                )
                self._current_state = new_state
                span.set_attribute("state_changed", True)
                span.set_attribute("old_state", old_state.value)
                span.set_attribute("new_state", new_state.value)

            # Handle state-specific logic
            approved = True
            adjusted_volume = signal.confidence  # Default: use full signal confidence as volume multiplier
            positions_to_close = []
            decision_reason = ""

            if risk_check.mdl_breached or risk_check.ml_breached:
                # HALT state: reject all trades and close everything
                approved = False
                adjusted_volume = Decimal("0")
                positions_to_close = list(positions) if positions else []
                decision_reason = f"HALT: {risk_check.breach_message}"
                span.set_attribute("decision", "HALT")

            elif new_state == RiskState.HALT:
                # Proactive HALT: close all positions
                approved = False
                adjusted_volume = Decimal("0")
                positions_to_close = list(positions) if positions else []
                decision_reason = "HALT state triggered: closing all positions"
                span.set_attribute("decision", "HALT")

            elif new_state == RiskState.RISK_OFF:
                # RISK_OFF: reject new trades, allow closes
                if self._is_close_action(signal):
                    approved = True
                    adjusted_volume = signal.confidence
                    decision_reason = "RISK_OFF state: closing position allowed"
                else:
                    approved = False
                    adjusted_volume = Decimal("0")
                    decision_reason = "RISK_OFF state: no new trades permitted"
                span.set_attribute("decision", "RISK_OFF")

            elif new_state == RiskState.CAUTION:
                # CAUTION: reduce position size by 50%
                approved = True
                adjusted_volume = signal.confidence * Decimal("0.5")
                decision_reason = f"CAUTION state: position size reduced by 50% ({adjusted_volume})"
                span.set_attribute("decision", "CAUTION")

            else:
                # NORMAL: full position size allowed
                approved = True
                adjusted_volume = signal.confidence
                decision_reason = "NORMAL state: full position size approved"
                span.set_attribute("decision", "NORMAL")

            # Check pre-midnight safety
            # CRITICAL: timezone-AWARE UTC. The rule engine converts to Prague
            # time and subtracts, and a naive value there raises mid-check —
            # which this agent turns into a circuit-breaker halt.
            flatten_decision = self.rule_engine.check_overnight_safety(
                positions=positions,
                equity=account_state.equity,
                config=self.config,
                current_time_utc=datetime.now(timezone.utc),
                mdl_limit=risk_check.mdl_limit,
            )

            if flatten_decision.should_flatten:
                logger.warning(
                    "pre_midnight_flatten_triggered",
                    reason=flatten_decision.reason,
                    positions_to_close=len(flatten_decision.positions_to_close),
                )
                positions_to_close.extend(flatten_decision.positions_to_close)
                span.set_attribute("pre_midnight_flatten", True)

            # Create decision object
            decision = RiskDecision(
                approved=approved,
                risk_state=new_state,
                reason=decision_reason,
                adjusted_volume=adjusted_volume,
                positions_to_close=list(set(positions_to_close)),  # Deduplicate
            )

            logger.info(
                "risk_decision_generated",
                signal_id=str(signal.signal_id),
                approved=approved,
                risk_state=new_state.value,
                adjusted_volume=str(adjusted_volume),
                positions_to_close_count=len(decision.positions_to_close),
            )

            span.set_attribute("approved", approved)
            span.set_attribute("adjusted_volume", str(adjusted_volume))
            span.set_status(Status(StatusCode.OK))

            return decision

        except Exception as e:
            logger.error(
                "risk_agent_error",
                error=str(e),
                signal_id=str(signal.signal_id),
            )
            span.record_exception(e)
            span.set_status(Status(StatusCode.ERROR, f"risk_agent_crash: {str(e)}"))

            # CRITICAL FAIL-SAFE: If risk agent crashes, halt ALL trading immediately
            # This is the non-negotiable safety mechanism to prevent catastrophic losses
            logger.critical(
                "risk_agent_crash_circuit_breaker_triggered",
                error=str(e),
                message="RISK AGENT CRASHED - ALL TRADING HALTED IMMEDIATELY FOR SAFETY",
            )
            raise CircuitBreakerError(
                "Risk agent crashed - all trading halted immediately for safety",
                agent_name="risk_agent",
                error=str(e),
            )

    def _is_close_action(self, signal: TradingSignal) -> bool:
        """Check if signal indicates a position close action.

        Args:
            signal: Trading signal to evaluate.

        Returns:
            True if signal indicates closing an existing position.
        """
        # FLAT direction indicates no position (close existing)
        from ftmo_mt5.core.models import SignalDirection

        return signal.direction == SignalDirection.FLAT

    def emit_risk_checked_event(
        self, decision: RiskDecision, signal: TradingSignal, event_store
    ) -> None:
        """Emit RiskChecked event to event store.

        Synchronous event emission - must be called from async context only.

        Args:
            decision: RiskDecision from processing.
            signal: Original trading signal.
            event_store: EventStore instance to append event to.
        """
        try:
            event = RiskChecked(
                timestamp=datetime.utcnow(),
                correlation_id=signal.correlation_id,
                approved=decision.approved,
                risk_state=decision.risk_state,
                reason=decision.reason,
            )
            event_store.append(event)
            logger.info(
                "risk_checked_event_emitted",
                signal_id=str(signal.signal_id),
                approved=decision.approved,
            )
        except Exception as e:
            logger.error("risk_checked_event_emission_failed", error=str(e))
            raise

    def emit_risk_state_changed_event(
        self, old_state: RiskState, new_state: RiskState, signal: TradingSignal, event_store
    ) -> None:
        """Emit RiskStateChanged event to event store.

        Synchronous event emission - must be called from async context only.

        Args:
            old_state: Previous risk state.
            new_state: New risk state.
            signal: Signal that triggered the change.
            event_store: EventStore instance to append event to.
        """
        try:
            event = RiskStateChanged(
                timestamp=datetime.utcnow(),
                correlation_id=signal.correlation_id,
                old_state=old_state,
                new_state=new_state,
                trigger=f"Signal {signal.signal_id} evaluation",
            )
            event_store.append(event)
            logger.info(
                "risk_state_changed_event_emitted",
                old_state=old_state.value,
                new_state=new_state.value,
            )
        except Exception as e:
            logger.error("risk_state_changed_event_emission_failed", error=str(e))
            raise

    @property
    def current_state(self) -> RiskState:
        """Return current risk state."""
        return self._current_state
