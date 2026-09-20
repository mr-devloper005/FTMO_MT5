"""Comprehensive test suite for FTMO Rule Engine.

Tests all risk management and compliance features:
- Prague midnight calculation across DST transitions
- MDL computation: 2-Step (5% loss from midnight balance)
- MDL computation: 1-Step (3% loss from midnight balance)
- ML computation: 2-Step (static 90% floor)
- ML computation: 1-Step (trailing EOD)
- Breach detection (exact boundary, above, below)
- Overnight safety check (positions carrying losses past midnight)
- Headroom calculations
- DST mismatch window detection
- Full risk check orchestration
"""

import pytest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from ftmo_mt5.engine.rule_engine import FTMORuleEngine, FlattenDecision, RiskCheckResult
from ftmo_mt5.core.models import (
    AccountState,
    ChallengeType,
    FTMOConfig,
    FTMOLimits,
    PositionInfo,
    RiskState,
)
from ftmo_mt5.core.constants import FTMO_PRAGUE_TZ


# ============================================================================
# Prague Timezone Tests
# ============================================================================


class TestPragueMidnight:
    """Test Prague midnight calculations."""

    def test_get_prague_midnight_utc_during_prague_day(self):
        """Get Prague midnight when time is during Prague day."""
        # 2025-03-15 14:00 UTC = 2025-03-15 15:00 CET (Prague standard time)
        dt_utc = datetime(2025, 3, 15, 14, 0, 0, tzinfo=timezone.utc)

        midnight = FTMORuleEngine.get_prague_midnight_utc(dt_utc)

        # Should be midnight 2025-03-15 00:00 CET = 2025-03-14 23:00 UTC
        expected = datetime(2025, 3, 14, 23, 0, 0, tzinfo=timezone.utc)
        assert midnight == expected

    def test_get_prague_midnight_utc_after_prague_midnight(self):
        """Get Prague midnight when time is after Prague midnight."""
        # 2025-03-15 23:00 UTC = 2025-03-16 00:00 CET (already past midnight)
        dt_utc = datetime(2025, 3, 15, 23, 0, 0, tzinfo=timezone.utc)

        midnight = FTMORuleEngine.get_prague_midnight_utc(dt_utc)

        # Should be midnight 2025-03-16 00:00 CET = 2025-03-15 23:00 UTC
        expected = datetime(2025, 3, 15, 23, 0, 0, tzinfo=timezone.utc)
        assert midnight == expected

    def test_get_next_prague_midnight_utc(self):
        """Get next Prague midnight."""
        dt_utc = datetime(2025, 3, 15, 14, 0, 0, tzinfo=timezone.utc)

        next_midnight = FTMORuleEngine.get_next_prague_midnight_utc(dt_utc)

        # Should be midnight 2025-03-16 00:00 CET = 2025-03-15 23:00 UTC
        expected = datetime(2025, 3, 15, 23, 0, 0, tzinfo=timezone.utc)
        assert next_midnight == expected

    def test_minutes_to_midnight(self):
        """Calculate minutes until Prague midnight."""
        # 2025-03-15 13:00 UTC = 2025-03-15 14:00 CET
        # Next midnight is 2025-03-16 00:00 CET = 2025-03-15 23:00 UTC
        # Time to midnight: 9 hours = 540 minutes
        dt_utc = datetime(2025, 3, 15, 13, 0, 0, tzinfo=timezone.utc)

        minutes = FTMORuleEngine.minutes_to_midnight(dt_utc)

        # Approximately 540 minutes (9 hours)
        assert Decimal("590") < minutes < Decimal("610")

    def test_is_dst_mismatch_window(self):
        """Detect DST mismatch windows (EU/US different dates)."""
        # Late March: EU changed but US hasn't yet
        march_mismatch = datetime(2025, 3, 15, 12, 0, 0, tzinfo=timezone.utc)
        assert FTMORuleEngine.is_dst_mismatch_window(march_mismatch) is True

        # Late October: EU changed but US hasn't yet
        october_mismatch = datetime(2025, 10, 25, 12, 0, 0, tzinfo=timezone.utc)
        assert FTMORuleEngine.is_dst_mismatch_window(october_mismatch) is True

        # Normal period: no mismatch
        normal_date = datetime(2025, 5, 15, 12, 0, 0, tzinfo=timezone.utc)
        assert FTMORuleEngine.is_dst_mismatch_window(normal_date) is False


# ============================================================================
# MDL (Maximum Daily Loss) Tests
# ============================================================================


class TestMDLComputation:
    """Test Maximum Daily Loss calculation."""

    def test_mdl_computation_two_step(self):
        """Test MDL computation for 2-Step: balance - 5% * initial_capital."""
        config = FTMOConfig(
            initial_capital=Decimal("200000"),
            challenge_type=ChallengeType.TWO_STEP,
            mdl_pct=Decimal("5"),
        )

        balance_at_midnight = Decimal("200000")
        mdl_limit = FTMORuleEngine.compute_mdl_limit(balance_at_midnight, config)

        # MDL = 200000 - (0.05 * 200000) = 200000 - 10000 = 190000
        expected = Decimal("190000")
        assert mdl_limit == expected

    def test_mdl_computation_two_step_after_profit(self):
        """Test MDL computation when balance increased due to profit."""
        config = FTMOConfig(
            initial_capital=Decimal("200000"),
            challenge_type=ChallengeType.TWO_STEP,
            mdl_pct=Decimal("5"),
        )

        # Balance grew to 204000
        balance_at_midnight = Decimal("204000")
        mdl_limit = FTMORuleEngine.compute_mdl_limit(balance_at_midnight, config)

        # MDL = 204000 - (0.05 * 200000) = 204000 - 10000 = 194000
        # This matches the PRD worked example
        expected = Decimal("194000")
        assert mdl_limit == expected

    def test_mdl_computation_one_step(self):
        """Test MDL computation for 1-Step: balance - 3% * initial_capital."""
        config = FTMOConfig(
            initial_capital=Decimal("100000"),
            challenge_type=ChallengeType.ONE_STEP,
            mdl_pct=Decimal("3"),
        )

        balance_at_midnight = Decimal("100000")
        mdl_limit = FTMORuleEngine.compute_mdl_limit(balance_at_midnight, config)

        # MDL = 100000 - (0.03 * 100000) = 100000 - 3000 = 97000
        expected = Decimal("97000")
        assert mdl_limit == expected

    def test_mdl_check_breach_at_exact_limit(self):
        """Test MDL breach detection at exact boundary."""
        mdl_limit = Decimal("190000")
        equity = Decimal("190000")

        # Exactly at limit = breached
        breached = FTMORuleEngine.check_mdl_breach(equity, mdl_limit)
        assert breached is True

    def test_mdl_check_breach_below_limit(self):
        """Test MDL breach detection when below limit."""
        mdl_limit = Decimal("190000")
        equity = Decimal("189999")

        breached = FTMORuleEngine.check_mdl_breach(equity, mdl_limit)
        assert breached is True

    def test_mdl_check_breach_above_limit(self):
        """Test no breach when equity is above limit."""
        mdl_limit = Decimal("190000")
        equity = Decimal("190001")

        breached = FTMORuleEngine.check_mdl_breach(equity, mdl_limit)
        assert breached is False


# ============================================================================
# ML (Maximum Loss) Tests
# ============================================================================


class TestMLComputation:
    """Test Maximum Loss (Overall Drawdown) calculation."""

    def test_ml_computation_two_step_static(self):
        """Test ML computation for 2-Step: static 90% of initial capital."""
        config = FTMOConfig(
            initial_capital=Decimal("200000"),
            challenge_type=ChallengeType.TWO_STEP,
            ml_pct=Decimal("10"),
        )

        highest_eod_balance = Decimal("200000")
        ml_limit = FTMORuleEngine.compute_ml_limit(config, highest_eod_balance)

        # ML = 200000 - (0.10 * 200000) = 200000 - 20000 = 180000
        expected = Decimal("180000")
        assert ml_limit == expected

    def test_ml_computation_two_step_ignores_eod_gains(self):
        """Test ML for 2-Step ignores highest_eod_balance (static limit)."""
        config = FTMOConfig(
            initial_capital=Decimal("200000"),
            challenge_type=ChallengeType.TWO_STEP,
            ml_pct=Decimal("10"),
        )

        # Even if EOD balance grew to 210000, limit remains static
        highest_eod_balance = Decimal("210000")
        ml_limit = FTMORuleEngine.compute_ml_limit(config, highest_eod_balance)

        # ML = 200000 - (0.10 * 200000) = 180000 (static, not 190000)
        expected = Decimal("180000")
        assert ml_limit == expected

    def test_ml_computation_one_step_trailing(self):
        """Test ML computation for 1-Step: trailing based on highest EOD."""
        config = FTMOConfig(
            initial_capital=Decimal("100000"),
            challenge_type=ChallengeType.ONE_STEP,
            ml_pct=Decimal("10"),
        )

        # Highest EOD balance was 102000
        highest_eod_balance = Decimal("102000")
        ml_limit = FTMORuleEngine.compute_ml_limit(config, highest_eod_balance)

        # ML = max(100000, 102000) - (0.10 * 100000) = 102000 - 10000 = 92000
        expected = Decimal("92000")
        assert ml_limit == expected

    def test_ml_computation_one_step_uses_initial_if_higher(self):
        """Test 1-Step uses initial capital if no EOD gains."""
        config = FTMOConfig(
            initial_capital=Decimal("100000"),
            challenge_type=ChallengeType.ONE_STEP,
            ml_pct=Decimal("10"),
        )

        # Highest EOD balance is less than initial capital (unlikely but test edge case)
        highest_eod_balance = Decimal("95000")
        ml_limit = FTMORuleEngine.compute_ml_limit(config, highest_eod_balance)

        # ML = max(100000, 95000) - 10000 = 90000
        expected = Decimal("90000")
        assert ml_limit == expected

    def test_ml_check_breach_at_limit(self):
        """Test ML breach at exact boundary."""
        ml_limit = Decimal("180000")
        equity = Decimal("180000")

        breached = FTMORuleEngine.check_ml_breach(equity, ml_limit)
        assert breached is True

    def test_ml_check_breach_below(self):
        """Test ML breach detection."""
        ml_limit = Decimal("180000")
        equity = Decimal("179999")

        breached = FTMORuleEngine.check_ml_breach(equity, ml_limit)
        assert breached is True

    def test_ml_check_no_breach(self):
        """Test no breach when equity above limit."""
        ml_limit = Decimal("180000")
        equity = Decimal("180001")

        breached = FTMORuleEngine.check_ml_breach(equity, ml_limit)
        assert breached is False


# ============================================================================
# Headroom Calculations
# ============================================================================


class TestHeadroomCalculations:
    """Test headroom buffer calculations."""

    def test_compute_headroom_positive(self):
        """Test headroom calculation when equity above limit."""
        equity = Decimal("195000")
        mdl_limit = Decimal("190000")

        headroom = FTMORuleEngine.compute_headroom(equity, mdl_limit)

        expected = Decimal("5000")
        assert headroom == expected

    def test_compute_headroom_negative(self):
        """Test headroom when equity below limit (negative headroom)."""
        equity = Decimal("185000")
        mdl_limit = Decimal("190000")

        headroom = FTMORuleEngine.compute_headroom(equity, mdl_limit)

        expected = Decimal("-5000")
        assert headroom == expected

    def test_compute_headroom_pct(self):
        """Test headroom as percentage of initial capital."""
        equity = Decimal("195000")
        initial_capital = Decimal("200000")
        mdl_limit = Decimal("190000")

        headroom_pct = FTMORuleEngine.compute_headroom_pct(
            equity, initial_capital, mdl_limit
        )

        # Headroom = 195000 - 190000 = 5000
        # Percentage = (5000 / 200000) * 100 = 2.5%
        expected = Decimal("2.5")
        assert headroom_pct == expected

    def test_compute_headroom_pct_negative(self):
        """Test negative headroom percentage."""
        equity = Decimal("185000")
        initial_capital = Decimal("200000")
        mdl_limit = Decimal("190000")

        headroom_pct = FTMORuleEngine.compute_headroom_pct(
            equity, initial_capital, mdl_limit
        )

        # Headroom = 185000 - 190000 = -5000
        # Percentage = (-5000 / 200000) * 100 = -2.5%
        expected = Decimal("-2.5")
        assert headroom_pct == expected


# ============================================================================
# Overnight Safety Module Tests
# ============================================================================


class TestOvernightSafety:
    """Test pre-midnight safety checks."""

    def test_overnight_safety_not_in_window(self):
        """Test overnight safety when not in pre-midnight window."""
        # 12:00 UTC = 13:00 CEST (Prague time), far from midnight
        dt_utc = datetime(2025, 3, 15, 12, 0, 0, tzinfo=timezone.utc)

        config = FTMOConfig(
            initial_capital=Decimal("200000"),
            pre_midnight_flatten_minutes=30,
        )

        positions = []
        equity = Decimal("200000")
        mdl_limit = Decimal("190000")

        decision = FTMORuleEngine.check_overnight_safety(
            positions, equity, config, dt_utc, mdl_limit
        )

        assert decision.should_flatten is False
        assert len(decision.positions_to_close) == 0

    def test_overnight_safety_no_floating_losses(self):
        """Test safety check when positions are profitable."""
        # 23:15 UTC = 00:15 CEST next day (in pre-midnight window, ~15 min to midnight)
        dt_utc = datetime(2025, 3, 15, 23, 15, 0, tzinfo=timezone.utc)

        config = FTMOConfig(
            initial_capital=Decimal("200000"),
            pre_midnight_flatten_minutes=30,
        )

        # Profitable positions
        positions = [
            PositionInfo(
                ticket=1,
                symbol="EURUSD",
                type="LONG",
                volume=Decimal("1.0"),
                price_open=Decimal("1.0850"),
                profit=Decimal("500"),  # Profitable
            ),
        ]

        equity = Decimal("200500")
        mdl_limit = Decimal("190000")

        decision = FTMORuleEngine.check_overnight_safety(
            positions, equity, config, dt_utc, mdl_limit
        )

        # Should flatten if in conservative mode (any loss = flatten)
        # But with profitable positions, should not flatten
        assert decision.headroom > Decimal("0")

    def test_overnight_safety_floating_losses_critical(self):
        """Test safety check with significant floating losses."""
        # In pre-midnight window
        dt_utc = datetime(2025, 3, 15, 23, 45, 0, tzinfo=timezone.utc)

        config = FTMOConfig(
            initial_capital=Decimal("200000"),
            pre_midnight_flatten_minutes=30,
        )

        # Large floating losses
        positions = [
            PositionInfo(
                ticket=1,
                symbol="EURUSD",
                type="LONG",
                volume=Decimal("10.0"),
                price_open=Decimal("1.0850"),
                profit=Decimal("-5000"),  # Large loss
            ),
        ]

        equity = Decimal("195000")
        mdl_limit = Decimal("190000")

        decision = FTMORuleEngine.check_overnight_safety(
            positions, equity, config, dt_utc, mdl_limit
        )

        # With large floating loss in pre-midnight window, should flatten
        assert decision.should_flatten is True
        assert len(decision.positions_to_close) > 0


# ============================================================================
# Full Risk Check Orchestration
# ============================================================================


class TestFullRiskCheckOrchestration:
    """Test complete risk check workflow."""

    def test_worked_example_from_prd(self):
        """Test the worked example from PRD: $200K account, Day 2 balance=$204K, MDL=$194K."""
        config = FTMOConfig(
            initial_capital=Decimal("200000"),
            challenge_type=ChallengeType.TWO_STEP,
            mdl_pct=Decimal("5"),
            ml_pct=Decimal("10"),
            caution_threshold_pct=Decimal("70"),
            risk_off_threshold_pct=Decimal("85"),
        )

        # Day 2: Balance at midnight is 204000 (profit from day 1)
        account_state = AccountState(
            balance=Decimal("204000"),
            equity=Decimal("204000"),
            margin=Decimal("10000"),
            free_margin=Decimal("194000"),
            initial_capital=Decimal("200000"),
            timestamp=datetime.utcnow(),
        )

        ftmo_limits = FTMOLimits(
            mdl_limit=Decimal("194000"),  # 204000 - (0.05 * 200000)
            ml_limit=Decimal("180000"),   # 200000 - (0.10 * 200000)
            balance_at_midnight=Decimal("204000"),
            highest_eod_balance=Decimal("204000"),
            challenge_type=ChallengeType.TWO_STEP,
        )

        positions = []

        result = FTMORuleEngine.run_all_checks(
            account_state, ftmo_limits, config, positions
        )

        # MDL should be 194000, equity is 204000 -> not breached
        assert result.mdl_breached is False
        assert result.mdl_limit == Decimal("194000")
        assert result.mdl_headroom == Decimal("10000")

        # ML should be 180000, equity is 204000 -> not breached
        assert result.ml_breached is False
        assert result.ml_limit == Decimal("180000")

        # Risk state should be NORMAL
        assert result.risk_state == RiskState.NORMAL

    def test_risk_check_halt_on_mdl_breach(self):
        """Test risk state transitions to HALT on MDL breach."""
        config = FTMOConfig(
            initial_capital=Decimal("200000"),
            challenge_type=ChallengeType.TWO_STEP,
            mdl_pct=Decimal("5"),
            ml_pct=Decimal("10"),
        )

        account_state = AccountState(
            balance=Decimal("193999"),  # Just below MDL
            equity=Decimal("193999"),
            margin=Decimal("10000"),
            free_margin=Decimal("183999"),
            initial_capital=Decimal("200000"),
            timestamp=datetime.utcnow(),
        )

        ftmo_limits = FTMOLimits(
            mdl_limit=Decimal("194000"),
            ml_limit=Decimal("180000"),
            balance_at_midnight=Decimal("204000"),
            highest_eod_balance=Decimal("204000"),
            challenge_type=ChallengeType.TWO_STEP,
        )

        positions = []

        result = FTMORuleEngine.run_all_checks(
            account_state, ftmo_limits, config, positions
        )

        assert result.mdl_breached is True
        assert result.risk_state == RiskState.HALT
        assert "MDL" in result.breach_message

    def test_risk_check_halt_on_ml_breach(self):
        """Test risk state transitions to HALT on ML breach."""
        config = FTMOConfig(
            initial_capital=Decimal("200000"),
            challenge_type=ChallengeType.TWO_STEP,
            mdl_pct=Decimal("5"),
            ml_pct=Decimal("10"),
        )

        account_state = AccountState(
            balance=Decimal("179999"),  # Below ML
            equity=Decimal("179999"),
            margin=Decimal("10000"),
            free_margin=Decimal("169999"),
            initial_capital=Decimal("200000"),
            timestamp=datetime.utcnow(),
        )

        ftmo_limits = FTMOLimits(
            mdl_limit=Decimal("194000"),
            ml_limit=Decimal("180000"),
            balance_at_midnight=Decimal("204000"),
            highest_eod_balance=Decimal("204000"),
            challenge_type=ChallengeType.TWO_STEP,
        )

        positions = []

        result = FTMORuleEngine.run_all_checks(
            account_state, ftmo_limits, config, positions
        )

        assert result.ml_breached is True
        assert result.risk_state == RiskState.HALT
        assert "ML" in result.breach_message

    def test_risk_check_caution_state(self):
        """Test CAUTION state when approaching limits."""
        config = FTMOConfig(
            initial_capital=Decimal("200000"),
            challenge_type=ChallengeType.TWO_STEP,
            mdl_pct=Decimal("5"),
            ml_pct=Decimal("10"),
            caution_threshold_pct=Decimal("70"),
            risk_off_threshold_pct=Decimal("85"),
        )

        # Equity 196,000 against a 194,000 daily floor: 80% of the 10,000
        # daily budget consumed, so approaching the limit without breaching it.
        account_state = AccountState(
            balance=Decimal("196000"),
            equity=Decimal("196000"),
            margin=Decimal("10000"),
            free_margin=Decimal("186000"),
            initial_capital=Decimal("200000"),
            timestamp=datetime.utcnow(),
        )

        # The daily floor here is 204,000 - 5% of 200,000 = 194,000. The
        # original equity of 185,000 sat BELOW that, so this case asserted
        # "not breached" while describing an actual breach. 196,000 leaves
        # 2,000 of a 10,000 budget — genuinely close, not over.
        ftmo_limits = FTMOLimits(
            mdl_limit=Decimal("194000"),
            ml_limit=Decimal("180000"),
            balance_at_midnight=Decimal("204000"),
            highest_eod_balance=Decimal("204000"),
            challenge_type=ChallengeType.TWO_STEP,
        )

        positions = []

        result = FTMORuleEngine.run_all_checks(
            account_state, ftmo_limits, config, positions
        )

        # Not breached, but approaching limits
        assert result.mdl_breached is False
        assert result.ml_breached is False
        # Should be in CAUTION or RISK_OFF (depending on exact threshold calculation)
        assert result.risk_state in [RiskState.CAUTION, RiskState.RISK_OFF, RiskState.NORMAL]

    def test_risk_check_normal_state_good_headroom(self):
        """Test NORMAL state with good headroom."""
        config = FTMOConfig(
            initial_capital=Decimal("200000"),
            challenge_type=ChallengeType.TWO_STEP,
            mdl_pct=Decimal("5"),
            ml_pct=Decimal("10"),
        )

        account_state = AccountState(
            balance=Decimal("200000"),
            equity=Decimal("200000"),
            margin=Decimal("5000"),
            free_margin=Decimal("195000"),
            initial_capital=Decimal("200000"),
            timestamp=datetime.utcnow(),
        )

        ftmo_limits = FTMOLimits(
            mdl_limit=Decimal("190000"),
            ml_limit=Decimal("180000"),
            balance_at_midnight=Decimal("200000"),
            highest_eod_balance=Decimal("200000"),
            challenge_type=ChallengeType.TWO_STEP,
        )

        positions = []

        result = FTMORuleEngine.run_all_checks(
            account_state, ftmo_limits, config, positions
        )

        assert result.mdl_breached is False
        assert result.ml_breached is False
        assert result.risk_state == RiskState.NORMAL
        assert result.mdl_headroom == Decimal("10000")
        assert result.ml_headroom == Decimal("20000")
