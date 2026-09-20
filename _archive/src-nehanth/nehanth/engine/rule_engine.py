"""FTMO Rule Engine for risk management and compliance checks.

Implements comprehensive risk checking for FTMO trading accounts including:
- Maximum Daily Loss (MDL) calculation and breach detection
- Maximum Overall Loss (ML) calculation and breach detection
- Prague timezone management for daily resets
- Pre-midnight safety analysis for overnight position management
- Headroom calculations for proactive risk monitoring
- Full risk check orchestration with OpenTelemetry observability

All financial calculations use Decimal for precision. The engine is stateless
and thread-safe - all state is passed via parameters.
"""

__all__ = [
    "RiskCheckResult",
    "FlattenDecision",
    "FTMORuleEngine",
]

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional
from zoneinfo import ZoneInfo

import structlog
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from nehanth.core.constants import FTMO_PRAGUE_TZ
from nehanth.core.exceptions import RiskBreachError
from nehanth.core.models import (
    AccountState,
    ChallengeType,
    FTMOConfig,
    FTMOLimits,
    PositionInfo,
    RiskState,
)

logger = structlog.get_logger(__name__)
tracer = trace.get_tracer(__name__)


# ============================================================================
# Data Classes
# ============================================================================


@dataclass(frozen=True)
class FlattenDecision:
    """Decision output from pre-midnight safety module.

    Indicates whether positions should be flattened before Prague midnight
    to avoid carrying floating losses into the next day.
    """

    should_flatten: bool
    """Whether positions should be flattened immediately."""

    reason: str
    """Explanation of the flatten decision."""

    positions_to_close: list[PositionInfo]
    """List of positions recommended for closure."""

    headroom: Decimal
    """Current headroom buffer before MDL breach (in account currency)."""


@dataclass(frozen=True)
class RiskCheckResult:
    """Complete risk check result with all limit information.

    Returned from run_all_checks() and contains all evaluated limits,
    breach status, and relevant metrics for monitoring.
    """

    mdl_breached: bool
    """True if Maximum Daily Loss limit is breached."""

    ml_breached: bool
    """True if Maximum Overall Loss limit is breached."""

    mdl_limit: Decimal
    """Computed MDL limit amount."""

    ml_limit: Decimal
    """Computed ML limit amount."""

    mdl_headroom: Decimal
    """Buffer in account currency before MDL breach."""

    mdl_headroom_pct: Decimal
    """Buffer as percentage of initial capital."""

    ml_headroom: Decimal
    """Buffer in account currency before ML breach."""

    ml_headroom_pct: Decimal
    """Buffer as percentage of initial capital."""

    risk_state: RiskState
    """Current risk state based on limits."""

    breach_message: str
    """Human-readable explanation of breach status."""


# ============================================================================
# FTMO Rule Engine
# ============================================================================


class FTMORuleEngine:
    """FTMO trading rule engine for risk compliance and limit enforcement.

    Stateless rule engine that performs all risk calculations and checks.
    All state is passed via parameters for thread safety and testability.

    Implements Prague timezone management for daily resets and comprehensive
    pre-midnight safety analysis to protect against overnight risk accumulation.
    """

    # ========================================================================
    # Prague Time Management
    # ========================================================================

    @staticmethod
    def get_prague_midnight_utc(dt_utc: datetime) -> datetime:
        """Return the most recent Prague midnight in UTC time.

        Converts the given UTC datetime to Prague timezone, finds the
        midnight of that day, and converts back to UTC.

        Args:
            dt_utc: Datetime in UTC timezone.

        Returns:
            Most recent midnight in Prague timezone, as a UTC datetime.

        Example:
            >>> dt = datetime(2025, 3, 15, 14, 30, tzinfo=timezone.utc)
            >>> midnight = FTMORuleEngine.get_prague_midnight_utc(dt)
            # Returns midnight between March 15-16 Prague time in UTC
        """
        span = tracer.start_as_current_span("get_prague_midnight_utc")
        try:
            # Convert UTC to Prague timezone
            prague_tz = FTMO_PRAGUE_TZ
            dt_prague = dt_utc.astimezone(prague_tz)

            # Get midnight of the Prague day
            midnight_prague = dt_prague.replace(hour=0, minute=0, second=0, microsecond=0)

            # If current time is before midnight, we want the previous midnight
            if dt_prague.time() < midnight_prague.time():
                midnight_prague -= timedelta(days=1)

            # Convert back to UTC
            midnight_utc = midnight_prague.astimezone(ZoneInfo('UTC'))

            logger.debug(
                "prague_midnight_computed",
                dt_utc=dt_utc.isoformat(),
                prague_time=dt_prague.isoformat(),
                midnight_utc=midnight_utc.isoformat(),
            )
            span.set_status(Status(StatusCode.OK))
            return midnight_utc

        except Exception as e:
            logger.error("prague_midnight_error", error=str(e), dt_utc=dt_utc.isoformat())
            span.set_status(Status(StatusCode.ERROR, str(e)))
            raise

    @staticmethod
    def get_next_prague_midnight_utc(dt_utc: datetime) -> datetime:
        """Return the next Prague midnight in UTC time.

        Finds the midnight following the given UTC datetime in Prague
        timezone and converts back to UTC.

        Args:
            dt_utc: Datetime in UTC timezone.

        Returns:
            Next midnight in Prague timezone, as a UTC datetime.
        """
        span = tracer.start_as_current_span("get_next_prague_midnight_utc")
        try:
            prague_tz = FTMO_PRAGUE_TZ
            dt_prague = dt_utc.astimezone(prague_tz)

            # Get midnight of the current Prague day
            midnight_prague = dt_prague.replace(hour=0, minute=0, second=0, microsecond=0)

            # If we're at or past midnight, next midnight is tomorrow
            if dt_prague.time() >= midnight_prague.time():
                midnight_prague += timedelta(days=1)
            else:
                # If before midnight, the next midnight is today
                pass

            # Convert back to UTC
            midnight_utc = midnight_prague.astimezone(ZoneInfo('UTC'))

            logger.debug(
                "next_prague_midnight_computed",
                dt_utc=dt_utc.isoformat(),
                next_midnight_utc=midnight_utc.isoformat(),
            )
            span.set_status(Status(StatusCode.OK))
            return midnight_utc

        except Exception as e:
            logger.error("next_prague_midnight_error", error=str(e), dt_utc=dt_utc.isoformat())
            span.set_status(Status(StatusCode.ERROR, str(e)))
            raise

    @staticmethod
    def minutes_to_midnight(dt_utc: datetime) -> Decimal:
        """Calculate minutes until next Prague midnight from UTC datetime.

        Args:
            dt_utc: Current datetime in UTC timezone.

        Returns:
            Minutes remaining until next Prague midnight as Decimal.
        """
        span = tracer.start_as_current_span("minutes_to_midnight")
        try:
            next_midnight = FTMORuleEngine.get_next_prague_midnight_utc(dt_utc)
            delta = next_midnight - dt_utc
            minutes = Decimal(str(delta.total_seconds())) / Decimal("60")

            logger.debug(
                "minutes_to_midnight_computed",
                dt_utc=dt_utc.isoformat(),
                next_midnight=next_midnight.isoformat(),
                minutes=str(minutes),
            )
            span.set_status(Status(StatusCode.OK))
            return minutes

        except Exception as e:
            logger.error("minutes_to_midnight_error", error=str(e))
            span.set_status(Status(StatusCode.ERROR, str(e)))
            raise

    @staticmethod
    def is_dst_mismatch_window(dt_utc: datetime) -> bool:
        """Detect EU/US DST mismatch window (~3 weeks/year).

        The US and EU change clocks on different dates, creating a brief
        window where their UTC offsets differ. This is important for
        accurate Prague midnight calculations during these periods.

        Args:
            dt_utc: Datetime in UTC timezone.

        Returns:
            True if current date is during EU/US DST mismatch window.
        """
        span = tracer.start_as_current_span("is_dst_mismatch_window")
        try:
            # US DST: 2nd Sunday in March - 1st Sunday in November
            # EU DST: Last Sunday in March - Last Sunday in October
            # Mismatch occurs between these windows

            year = dt_utc.year
            month = dt_utc.month
            day = dt_utc.day

            # Rough check: typically late March or late October
            # More precise check would require calculating exact DST dates
            is_mismatch = (month == 3 and day >= 8) or (month == 10 and day >= 25)

            logger.debug(
                "dst_mismatch_checked",
                dt_utc=dt_utc.isoformat(),
                is_mismatch=is_mismatch,
            )
            span.set_status(Status(StatusCode.OK))
            return is_mismatch

        except Exception as e:
            logger.error("dst_mismatch_check_error", error=str(e))
            span.set_status(Status(StatusCode.ERROR, str(e)))
            raise

    # ========================================================================
    # Maximum Daily Loss (MDL)
    # ========================================================================

    @staticmethod
    def compute_mdl_limit(balance_at_midnight: Decimal, config: FTMOConfig) -> Decimal:
        """Compute Maximum Daily Loss (MDL) limit.

        MDL calculation differs by challenge type:
        - 2-Step: MDL_limit = Balance_at_midnight_CEST - (0.05 * Initial_Capital)
        - 1-Step: MDL_limit = Balance_at_midnight_CEST - (0.03 * Initial_Capital)

        Args:
            balance_at_midnight: Account balance at Prague midnight (CEST).
            config: FTMO configuration with initial capital and challenge type.

        Returns:
            MDL limit as Decimal (the minimum equity allowed for the day).

        Raises:
            ValueError: If inputs are invalid.
        """
        span = tracer.start_as_current_span("compute_mdl_limit")
        try:
            if balance_at_midnight < Decimal("0"):
                raise ValueError("balance_at_midnight must be non-negative")
            if config.initial_capital <= Decimal("0"):
                raise ValueError("config.initial_capital must be positive")

            # Get MDL percentage from config
            mdl_pct = config.mdl_pct / Decimal("100")
            mdl_amount = config.initial_capital * mdl_pct

            # MDL limit is balance minus the allowable loss
            mdl_limit = balance_at_midnight - mdl_amount

            logger.debug(
                "mdl_limit_computed",
                balance_at_midnight=str(balance_at_midnight),
                initial_capital=str(config.initial_capital),
                mdl_pct=str(config.mdl_pct),
                mdl_amount=str(mdl_amount),
                mdl_limit=str(mdl_limit),
                challenge_type=config.challenge_type.value,
            )
            span.set_attribute("mdl_limit", str(mdl_limit))
            span.set_attribute("mdl_amount", str(mdl_amount))
            span.set_status(Status(StatusCode.OK))
            return mdl_limit

        except Exception as e:
            logger.error(
                "mdl_limit_error",
                error=str(e),
                balance_at_midnight=str(balance_at_midnight),
            )
            span.set_status(Status(StatusCode.ERROR, str(e)))
            raise

    @staticmethod
    def check_mdl_breach(equity: Decimal, mdl_limit: Decimal) -> bool:
        """Check if current equity breaches the MDL limit.

        Breach occurs when: Current_equity <= MDL_limit

        Args:
            equity: Current account equity.
            mdl_limit: Computed MDL limit.

        Returns:
            True if MDL limit is breached (equity <= limit).
        """
        span = tracer.start_as_current_span("check_mdl_breach")
        try:
            breached = equity <= mdl_limit
            logger.debug(
                "mdl_breach_checked",
                equity=str(equity),
                mdl_limit=str(mdl_limit),
                breached=breached,
            )
            span.set_attribute("breached", breached)
            span.set_status(Status(StatusCode.OK))
            return breached

        except Exception as e:
            logger.error("mdl_breach_check_error", error=str(e))
            span.set_status(Status(StatusCode.ERROR, str(e)))
            raise

    # ========================================================================
    # Maximum Overall Loss (ML)
    # ========================================================================

    @staticmethod
    def compute_ml_limit(config: FTMOConfig, highest_eod_balance: Decimal) -> Decimal:
        """Compute Maximum Loss (ML) limit.

        ML calculation differs by challenge type:
        - 2-Step (static): equity cannot drop below 90% of initial capital
        - 1-Step (EOD trailing): ML_limit = MAX(Initial_Capital, highest_EOD_balance) - (0.10 * Initial_Capital)

        Args:
            config: FTMO configuration with initial capital and challenge type.
            highest_eod_balance: Highest end-of-day balance seen so far.

        Returns:
            ML limit as Decimal (the minimum equity allowed overall).

        Raises:
            ValueError: If inputs are invalid.
        """
        span = tracer.start_as_current_span("compute_ml_limit")
        try:
            if highest_eod_balance < Decimal("0"):
                raise ValueError("highest_eod_balance must be non-negative")

            ml_pct = config.ml_pct / Decimal("100")
            ml_amount = config.initial_capital * ml_pct

            if config.challenge_type == ChallengeType.TWO_STEP:
                # 2-Step: static limit based on initial capital
                ml_limit = config.initial_capital - ml_amount
                logger.debug(
                    "ml_limit_computed_two_step",
                    initial_capital=str(config.initial_capital),
                    ml_pct=str(config.ml_pct),
                    ml_amount=str(ml_amount),
                    ml_limit=str(ml_limit),
                )
            else:
                # 1-Step: trailing limit based on highest EOD balance
                reference_balance = max(config.initial_capital, highest_eod_balance)
                ml_limit = reference_balance - ml_amount
                logger.debug(
                    "ml_limit_computed_one_step",
                    initial_capital=str(config.initial_capital),
                    highest_eod_balance=str(highest_eod_balance),
                    reference_balance=str(reference_balance),
                    ml_pct=str(config.ml_pct),
                    ml_amount=str(ml_amount),
                    ml_limit=str(ml_limit),
                )

            span.set_attribute("ml_limit", str(ml_limit))
            span.set_attribute("challenge_type", config.challenge_type.value)
            span.set_status(Status(StatusCode.OK))
            return ml_limit

        except Exception as e:
            logger.error("ml_limit_error", error=str(e), config=config.model_dump())
            span.set_status(Status(StatusCode.ERROR, str(e)))
            raise

    @staticmethod
    def check_ml_breach(equity: Decimal, ml_limit: Decimal) -> bool:
        """Check if current equity breaches the ML limit.

        Breach occurs when: Current_equity <= ML_limit

        Args:
            equity: Current account equity.
            ml_limit: Computed ML limit.

        Returns:
            True if ML limit is breached (equity <= limit).
        """
        span = tracer.start_as_current_span("check_ml_breach")
        try:
            breached = equity <= ml_limit
            logger.debug(
                "ml_breach_checked",
                equity=str(equity),
                ml_limit=str(ml_limit),
                breached=breached,
            )
            span.set_attribute("breached", breached)
            span.set_status(Status(StatusCode.OK))
            return breached

        except Exception as e:
            logger.error("ml_breach_check_error", error=str(e))
            span.set_status(Status(StatusCode.ERROR, str(e)))
            raise

    # ========================================================================
    # Pre-Midnight Safety Module
    # ========================================================================

    @staticmethod
    def check_overnight_safety(
        positions: list[PositionInfo],
        equity: Decimal,
        config: FTMOConfig,
        current_time_utc: datetime,
        mdl_limit: Decimal,
    ) -> FlattenDecision:
        """Evaluate whether positions should be flattened before Prague midnight.

        Critical safety module that runs ~30 minutes before midnight to assess
        if open positions with floating losses should be closed to avoid carrying
        losses into the new day and breaching MDL.

        Performs worst-case scenario analysis: assumes all positions move against
        us and evaluates if remaining capital provides sufficient MDL headroom.

        Args:
            positions: List of currently open positions.
            equity: Current account equity.
            config: FTMO configuration including pre_midnight_flatten_minutes.
            current_time_utc: Current time in UTC.
            mdl_limit: Computed MDL limit for today.

        Returns:
            FlattenDecision with recommendation and analysis.

        Raises:
            ValueError: If inputs are invalid.
        """
        span = tracer.start_as_current_span("check_overnight_safety")
        try:
            if equity < Decimal("0"):
                raise ValueError("equity must be non-negative")
            if config.initial_capital <= Decimal("0"):
                raise ValueError("config.initial_capital must be positive")
            if not positions:
                # No open positions - safe to proceed
                logger.debug("overnight_safety_no_positions")
                return FlattenDecision(
                    should_flatten=False,
                    reason="No open positions",
                    positions_to_close=[],
                    headroom=equity - mdl_limit,
                )
            minutes_to_midnight = FTMORuleEngine.minutes_to_midnight(current_time_utc)

            # Check if we're in the pre-midnight window
            in_window = minutes_to_midnight <= Decimal(config.pre_midnight_flatten_minutes)

            if not in_window:
                logger.debug(
                    "not_in_midnight_window",
                    minutes_to_midnight=str(minutes_to_midnight),
                    window_minutes=config.pre_midnight_flatten_minutes,
                )
                span.set_attribute("in_window", False)
                span.set_status(Status(StatusCode.OK))
                return FlattenDecision(
                    should_flatten=False,
                    reason=f"Not in pre-midnight window ({minutes_to_midnight} min to midnight)",
                    positions_to_close=[],
                    headroom=equity - mdl_limit,
                )

            # Calculate worst-case loss if all positions reverse
            total_unrealized_loss = Decimal("0")
            positions_with_loss = []

            for pos in positions:
                if pos.profit < Decimal("0"):
                    total_unrealized_loss += abs(pos.profit)
                    positions_with_loss.append(pos)

            # Worst-case scenario: all losses realized
            worst_case_equity = equity - total_unrealized_loss

            # Check headroom after worst case
            headroom_worst_case = worst_case_equity - mdl_limit
            headroom_pct_worst_case = (
                (headroom_worst_case / config.initial_capital * Decimal("100"))
                if config.initial_capital > 0
                else Decimal("0")
            )

            # Safety thresholds
            MIN_HEADROOM_BUFFER = config.initial_capital * Decimal("0.02")  # 2% of initial capital
            CRITICAL_THRESHOLD_PCT = Decimal("2")  # If within 2% of MDL in worst case

            # Should flatten if worst case puts us near breach
            # Only flatten if: (1) worst-case headroom drops below buffer, OR
            # (2) we have floating losses AND headroom is tight
            should_flatten = (
                headroom_worst_case <= MIN_HEADROOM_BUFFER
                or (headroom_pct_worst_case <= CRITICAL_THRESHOLD_PCT and total_unrealized_loss > Decimal("0"))
            )

            reason_parts = []
            if should_flatten:
                reason_parts.append(
                    f"In pre-midnight window ({minutes_to_midnight:.0f} min to midnight)."
                )
                if headroom_worst_case <= MIN_HEADROOM_BUFFER:
                    reason_parts.append(
                        f"Worst-case headroom ${headroom_worst_case:.2f} below ${MIN_HEADROOM_BUFFER:.2f} threshold."
                    )
                if total_unrealized_loss > Decimal("0"):
                    reason_parts.append(
                        f"${total_unrealized_loss:.2f} floating loss in {len(positions_with_loss)} position(s)."
                    )
            else:
                reason_parts.append(
                    f"Safe to hold positions. "
                    f"Worst-case headroom: ${headroom_worst_case:.2f} ({headroom_pct_worst_case:.2f}% of capital)."
                )

            reason = " ".join(reason_parts)

            logger.info(
                "overnight_safety_checked",
                should_flatten=should_flatten,
                minutes_to_midnight=str(minutes_to_midnight),
                current_equity=str(equity),
                worst_case_equity=str(worst_case_equity),
                total_unrealized_loss=str(total_unrealized_loss),
                headroom_worst_case=str(headroom_worst_case),
                mdl_limit=str(mdl_limit),
                positions_with_loss=len(positions_with_loss),
                reason=reason,
            )

            span.set_attribute("should_flatten", should_flatten)
            span.set_attribute("worst_case_equity", str(worst_case_equity))
            span.set_attribute("headroom_worst_case", str(headroom_worst_case))
            span.set_status(Status(StatusCode.OK))

            return FlattenDecision(
                should_flatten=should_flatten,
                reason=reason,
                positions_to_close=positions_with_loss if should_flatten else [],
                headroom=headroom_worst_case,
            )

        except Exception as e:
            logger.error("overnight_safety_check_error", error=str(e))
            span.set_status(Status(StatusCode.ERROR, str(e)))
            raise

    # ========================================================================
    # Headroom Calculations
    # ========================================================================

    @staticmethod
    def compute_headroom(equity: Decimal, mdl_limit: Decimal) -> Decimal:
        """Compute headroom (buffer) before MDL breach.

        Headroom represents the amount of additional loss the account can
        absorb before hitting the MDL limit.

        Args:
            equity: Current account equity.
            mdl_limit: Computed MDL limit.

        Returns:
            Headroom in account currency (equity - mdl_limit).
        """
        span = tracer.start_as_current_span("compute_headroom")
        try:
            headroom = equity - mdl_limit
            logger.debug(
                "headroom_computed",
                equity=str(equity),
                mdl_limit=str(mdl_limit),
                headroom=str(headroom),
            )
            span.set_attribute("headroom", str(headroom))
            span.set_status(Status(StatusCode.OK))
            return headroom

        except Exception as e:
            logger.error("compute_headroom_error", error=str(e))
            span.set_status(Status(StatusCode.ERROR, str(e)))
            raise

    @staticmethod
    def compute_headroom_pct(
        equity: Decimal, initial_capital: Decimal, mdl_limit: Decimal
    ) -> Decimal:
        """Compute headroom as percentage of initial capital.

        Args:
            equity: Current account equity.
            initial_capital: Initial account capital.
            mdl_limit: Computed MDL limit.

        Returns:
            Headroom as percentage of initial capital (can be negative).

        Raises:
            ValueError: If initial_capital is zero or negative, or if equity is None.
        """
        span = tracer.start_as_current_span("compute_headroom_pct")
        try:
            if equity is None:
                raise ValueError("equity cannot be None")
            if initial_capital <= Decimal("0"):
                raise ValueError("initial_capital must be positive")
            if equity < Decimal("0"):
                raise ValueError("equity must be non-negative")

            headroom = equity - mdl_limit
            headroom_pct = (headroom / initial_capital) * Decimal("100")

            logger.debug(
                "headroom_pct_computed",
                equity=str(equity),
                mdl_limit=str(mdl_limit),
                initial_capital=str(initial_capital),
                headroom_pct=str(headroom_pct),
            )
            span.set_attribute("headroom_pct", str(headroom_pct))
            span.set_status(Status(StatusCode.OK))
            return headroom_pct

        except Exception as e:
            logger.error("compute_headroom_pct_error", error=str(e))
            span.set_status(Status(StatusCode.ERROR, str(e)))
            raise

    # ========================================================================
    # Full Risk Check Orchestration
    # ========================================================================

    @staticmethod
    def run_all_checks(
        account_state: AccountState,
        ftmo_limits: FTMOLimits,
        config: FTMOConfig,
        positions: Optional[list[PositionInfo]] = None,
    ) -> RiskCheckResult:
        """Execute all risk checks and return comprehensive results.

        Orchestrates MDL, ML, and risk state evaluation in a single call.
        This is the primary entry point for risk analysis.

        Args:
            account_state: Current account snapshot.
            ftmo_limits: FTMO challenge limits with balance_at_midnight.
            config: FTMO configuration.
            positions: List of open positions (may be empty, defaults to empty list).

        Returns:
            RiskCheckResult with all breach and state information.

        Raises:
            ValueError: If required fields are missing or invalid.
        """
        span = tracer.start_as_current_span("run_all_checks")
        try:
            # Handle optional positions parameter
            if positions is None:
                positions = []

            # Validate required fields
            if account_state is None:
                raise ValueError("account_state cannot be None")
            if account_state.equity is None:
                raise ValueError("account_state.equity cannot be None")
            if ftmo_limits.balance_at_midnight is None:
                raise ValueError("ftmo_limits.balance_at_midnight is required")
            if account_state.equity < Decimal("0"):
                raise ValueError("account_state.equity must be non-negative")
            if config.initial_capital <= Decimal("0"):
                raise ValueError("config.initial_capital must be positive")
            # Guard against negative free_margin (edge case in MT5)
            if account_state.free_margin < Decimal("0"):
                logger.warning("negative_free_margin_detected", free_margin=str(account_state.free_margin))
                # Treat as zero for safety
                account_state = AccountState(
                    balance=account_state.balance,
                    equity=account_state.equity,
                    margin=account_state.margin,
                    free_margin=Decimal("0"),
                    initial_capital=account_state.initial_capital,
                    timestamp=account_state.timestamp,
                )

            # Compute MDL limit and check
            mdl_limit = FTMORuleEngine.compute_mdl_limit(ftmo_limits.balance_at_midnight, config)
            mdl_breached = FTMORuleEngine.check_mdl_breach(account_state.equity, mdl_limit)

            # Compute ML limit and check
            ml_limit = FTMORuleEngine.compute_ml_limit(config, ftmo_limits.highest_eod_balance)
            ml_breached = FTMORuleEngine.check_ml_breach(account_state.equity, ml_limit)

            # Compute headrooms
            mdl_headroom = FTMORuleEngine.compute_headroom(account_state.equity, mdl_limit)
            mdl_headroom_pct = FTMORuleEngine.compute_headroom_pct(
                account_state.equity, config.initial_capital, mdl_limit
            )

            ml_headroom = FTMORuleEngine.compute_headroom(account_state.equity, ml_limit)
            ml_headroom_pct = FTMORuleEngine.compute_headroom_pct(
                account_state.equity, config.initial_capital, ml_limit
            )

            # Determine risk state
            if mdl_breached or ml_breached:
                risk_state = RiskState.HALT
                breach_message = "HALT: Risk limits breached. "
                if mdl_breached:
                    breach_message += f"MDL breached: equity ${account_state.equity} <= limit ${mdl_limit}. "
                if ml_breached:
                    breach_message += f"ML breached: equity ${account_state.equity} <= limit ${ml_limit}."
            else:
                # Determine CAUTION or RISK_OFF based on ML headroom percentage
                ml_headroom_pct_usage = (Decimal("100") - ml_headroom_pct)
                caution_threshold = config.caution_threshold_pct
                risk_off_threshold = config.risk_off_threshold_pct

                if ml_headroom_pct_usage >= risk_off_threshold:
                    risk_state = RiskState.RISK_OFF
                    breach_message = f"RISK_OFF: {ml_headroom_pct_usage:.1f}% of ML limit used (threshold: {risk_off_threshold}%)"
                elif ml_headroom_pct_usage >= caution_threshold:
                    risk_state = RiskState.CAUTION
                    breach_message = f"CAUTION: {ml_headroom_pct_usage:.1f}% of ML limit used (threshold: {caution_threshold}%)"
                else:
                    risk_state = RiskState.NORMAL
                    breach_message = "NORMAL: All limits healthy"

            logger.info(
                "all_checks_completed",
                mdl_breached=mdl_breached,
                ml_breached=ml_breached,
                mdl_limit=str(mdl_limit),
                ml_limit=str(ml_limit),
                mdl_headroom=str(mdl_headroom),
                mdl_headroom_pct=str(mdl_headroom_pct),
                ml_headroom=str(ml_headroom),
                ml_headroom_pct=str(ml_headroom_pct),
                risk_state=risk_state.value,
                equity=str(account_state.equity),
                balance_at_midnight=str(ftmo_limits.balance_at_midnight),
            )

            span.set_attribute("mdl_breached", mdl_breached)
            span.set_attribute("ml_breached", ml_breached)
            span.set_attribute("risk_state", risk_state.value)
            span.set_attribute("mdl_limit", str(mdl_limit))
            span.set_attribute("ml_limit", str(ml_limit))
            span.set_status(Status(StatusCode.OK))

            return RiskCheckResult(
                mdl_breached=mdl_breached,
                ml_breached=ml_breached,
                mdl_limit=mdl_limit,
                ml_limit=ml_limit,
                mdl_headroom=mdl_headroom,
                mdl_headroom_pct=mdl_headroom_pct,
                ml_headroom=ml_headroom,
                ml_headroom_pct=ml_headroom_pct,
                risk_state=risk_state,
                breach_message=breach_message,
            )

        except Exception as e:
            logger.error("all_checks_error", error=str(e), account_state=account_state.model_dump())
            span.set_status(Status(StatusCode.ERROR, str(e)))
            raise
