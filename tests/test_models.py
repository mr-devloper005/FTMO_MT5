"""Comprehensive test suite for FTMO MT5 domain models.

Tests all Pydantic models including:
- Value object creation and validation
- Frozen/immutable enforcement
- Field validators (volume > 0, confidence 0-1, etc.)
- Enum values
- Decimal precision preservation
- UUID generation for events
"""

import pytest
from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4
from pydantic import ValidationError

from ftmo_mt5.core.models import (
    # Enums
    OrderAction,
    OrderType,
    FillPolicy,
    RiskState,
    SignalDirection,
    ChallengeType,
    ExecutionStatus,
    # Value Objects
    TradingSignal,
    TradeRequest,
    AccountState,
    FTMOLimits,
    PositionInfo,
    # Events
    SignalGenerated,
    RiskChecked,
    OrderSubmitted,
    OrderFilled,
    OrderRejected,
    PositionClosed,
    RiskStateChanged,
    CircuitBreakerTriggered,
    # Config
    FTMOConfig,
    SystemConfig,
)


# ============================================================================
# Enum Tests
# ============================================================================


class TestEnums:
    """Test all enum definitions."""

    def test_order_action_values(self):
        """Verify OrderAction enum has all required values."""
        assert OrderAction.BUY.value == "BUY"
        assert OrderAction.SELL.value == "SELL"
        assert OrderAction.CLOSE_LONG.value == "CLOSE_LONG"
        assert OrderAction.CLOSE_SHORT.value == "CLOSE_SHORT"
        assert OrderAction.CLOSE_ALL.value == "CLOSE_ALL"

    def test_order_type_values(self):
        """Verify OrderType enum has all required values."""
        assert OrderType.MARKET.value == "MARKET"
        assert OrderType.LIMIT.value == "LIMIT"
        assert OrderType.STOP.value == "STOP"
        assert OrderType.STOP_LIMIT.value == "STOP_LIMIT"

    def test_fill_policy_values(self):
        """Verify FillPolicy enum has all required values."""
        assert FillPolicy.FOK.value == "FOK"
        assert FillPolicy.IOC.value == "IOC"
        assert FillPolicy.RETURN.value == "RETURN"
        assert FillPolicy.POLICY.value == "POLICY"

    def test_risk_state_values(self):
        """Verify RiskState enum has all required values."""
        assert RiskState.NORMAL.value == "NORMAL"
        assert RiskState.CAUTION.value == "CAUTION"
        assert RiskState.RISK_OFF.value == "RISK_OFF"
        assert RiskState.HALT.value == "HALT"

    def test_signal_direction_values(self):
        """Verify SignalDirection enum has all required values."""
        assert SignalDirection.LONG.value == "LONG"
        assert SignalDirection.SHORT.value == "SHORT"
        assert SignalDirection.FLAT.value == "FLAT"

    def test_challenge_type_values(self):
        """Verify ChallengeType enum has all required values."""
        assert ChallengeType.ONE_STEP.value == "ONE_STEP"
        assert ChallengeType.TWO_STEP.value == "TWO_STEP"


# ============================================================================
# TradingSignal Tests
# ============================================================================


class TestTradingSignal:
    """Test TradingSignal value object."""

    def test_create_valid_signal(self):
        """Create trading signal with valid data."""
        signal = TradingSignal(
            symbol="EURUSD",
            direction=SignalDirection.LONG,
            confidence=Decimal("0.85"),
            timestamp=datetime.utcnow(),
            correlation_id=uuid4(),
            strategy_name="MA_Crossover",
        )
        assert signal.symbol == "EURUSD"
        assert signal.direction == SignalDirection.LONG
        assert signal.confidence == Decimal("0.85")
        assert signal.strategy_name == "MA_Crossover"
        assert isinstance(signal.signal_id, UUID)

    def test_signal_confidence_validation(self):
        """Test confidence field validators (0-1 range)."""
        # Valid boundaries
        TradingSignal(
            symbol="EURUSD",
            direction=SignalDirection.LONG,
            confidence=Decimal("0"),
            timestamp=datetime.utcnow(),
            correlation_id=uuid4(),
            strategy_name="Test",
        )

        TradingSignal(
            symbol="EURUSD",
            direction=SignalDirection.LONG,
            confidence=Decimal("1"),
            timestamp=datetime.utcnow(),
            correlation_id=uuid4(),
            strategy_name="Test",
        )

        # Invalid: > 1
        with pytest.raises(ValidationError) as exc_info:
            TradingSignal(
                symbol="EURUSD",
                direction=SignalDirection.LONG,
                confidence=Decimal("1.5"),
                timestamp=datetime.utcnow(),
                correlation_id=uuid4(),
                strategy_name="Test",
            )
        assert "confidence" in str(exc_info.value).lower()

        # Invalid: < 0
        with pytest.raises(ValidationError) as exc_info:
            TradingSignal(
                symbol="EURUSD",
                direction=SignalDirection.LONG,
                confidence=Decimal("-0.1"),
                timestamp=datetime.utcnow(),
                correlation_id=uuid4(),
                strategy_name="Test",
            )
        assert "confidence" in str(exc_info.value).lower()

    def test_signal_frozen_immutability(self):
        """Verify TradingSignal is frozen and immutable."""
        signal = TradingSignal(
            symbol="EURUSD",
            direction=SignalDirection.LONG,
            confidence=Decimal("0.85"),
            timestamp=datetime.utcnow(),
            correlation_id=uuid4(),
            strategy_name="Test",
        )

        with pytest.raises(Exception):  # Pydantic raises ValidationError on frozen model
            signal.confidence = Decimal("0.5")

    def test_signal_decimal_conversion(self):
        """Test Decimal conversion from float/str in validator."""
        signal = TradingSignal(
            symbol="EURUSD",
            direction=SignalDirection.LONG,
            confidence=0.75,  # Float input
            timestamp=datetime.utcnow(),
            correlation_id=uuid4(),
            strategy_name="Test",
            meta_label_prob="0.65",  # String input
        )

        assert isinstance(signal.confidence, Decimal)
        assert isinstance(signal.meta_label_prob, Decimal)
        assert signal.confidence == Decimal("0.75")
        assert signal.meta_label_prob == Decimal("0.65")

    def test_signal_uuid_generation(self):
        """Test that signal_id is auto-generated as UUID."""
        signal1 = TradingSignal(
            symbol="EURUSD",
            direction=SignalDirection.LONG,
            confidence=Decimal("0.5"),
            timestamp=datetime.utcnow(),
            correlation_id=uuid4(),
            strategy_name="Test",
        )
        signal2 = TradingSignal(
            symbol="EURUSD",
            direction=SignalDirection.LONG,
            confidence=Decimal("0.5"),
            timestamp=datetime.utcnow(),
            correlation_id=uuid4(),
            strategy_name="Test",
        )

        # Each signal should have unique signal_id
        assert signal1.signal_id != signal2.signal_id
        assert isinstance(signal1.signal_id, UUID)
        assert isinstance(signal2.signal_id, UUID)


# ============================================================================
# TradeRequest Tests
# ============================================================================


class TestTradeRequest:
    """Test TradeRequest value object."""

    def test_create_market_order(self):
        """Create valid market order."""
        req = TradeRequest(
            symbol="EURUSD",
            action=OrderAction.BUY,
            order_type=OrderType.MARKET,
            volume=Decimal("1.0"),
            magic=234000,
            correlation_id=uuid4(),
        )
        assert req.symbol == "EURUSD"
        assert req.action == OrderAction.BUY
        assert req.order_type == OrderType.MARKET
        assert req.volume == Decimal("1.0")
        assert req.price is None
        assert req.fill_policy == FillPolicy.POLICY

    def test_create_limit_order(self):
        """Create valid limit order with price."""
        req = TradeRequest(
            symbol="EURUSD",
            action=OrderAction.BUY,
            order_type=OrderType.LIMIT,
            volume=Decimal("2.5"),
            price=Decimal("1.0850"),
            magic=234000,
            correlation_id=uuid4(),
        )
        assert req.price == Decimal("1.0850")

    def test_volume_validation(self):
        """Test volume must be > 0."""
        # Valid
        TradeRequest(
            symbol="EURUSD",
            action=OrderAction.BUY,
            order_type=OrderType.MARKET,
            volume=Decimal("0.01"),
            magic=234000,
            correlation_id=uuid4(),
        )

        # Invalid: volume = 0
        with pytest.raises(ValidationError) as exc_info:
            TradeRequest(
                symbol="EURUSD",
                action=OrderAction.BUY,
                order_type=OrderType.MARKET,
                volume=Decimal("0"),
                magic=234000,
                correlation_id=uuid4(),
            )
        assert "volume" in str(exc_info.value).lower()

        # Invalid: volume < 0
        with pytest.raises(ValidationError) as exc_info:
            TradeRequest(
                symbol="EURUSD",
                action=OrderAction.BUY,
                order_type=OrderType.MARKET,
                volume=Decimal("-1.0"),
                magic=234000,
                correlation_id=uuid4(),
            )
        assert "volume" in str(exc_info.value).lower()

    def test_decimal_conversion_for_volumes(self):
        """Test Decimal conversion for volume, price, sl, tp."""
        req = TradeRequest(
            symbol="EURUSD",
            action=OrderAction.BUY,
            order_type=OrderType.LIMIT,
            volume=1.5,  # Float
            price="1.0850",  # String
            sl=1.0750,  # Float
            tp="1.0950",  # String
            magic=234000,
            correlation_id=uuid4(),
        )

        assert isinstance(req.volume, Decimal)
        assert isinstance(req.price, Decimal)
        assert isinstance(req.sl, Decimal)
        assert isinstance(req.tp, Decimal)
        assert req.volume == Decimal("1.5")
        assert req.price == Decimal("1.0850")

    def test_trade_request_frozen(self):
        """Verify TradeRequest is frozen."""
        req = TradeRequest(
            symbol="EURUSD",
            action=OrderAction.BUY,
            order_type=OrderType.MARKET,
            volume=Decimal("1.0"),
            magic=234000,
            correlation_id=uuid4(),
        )

        with pytest.raises(Exception):
            req.volume = Decimal("2.0")


# ============================================================================
# AccountState Tests
# ============================================================================


class TestAccountState:
    """Test AccountState value object."""

    def test_create_account_state(self):
        """Create account state snapshot."""
        state = AccountState(
            balance=Decimal("100000"),
            equity=Decimal("98500"),
            margin=Decimal("15000"),
            free_margin=Decimal("85000"),
            initial_capital=Decimal("100000"),
            timestamp=datetime.utcnow(),
        )

        assert state.balance == Decimal("100000")
        assert state.equity == Decimal("98500")
        assert state.margin == Decimal("15000")
        assert state.free_margin == Decimal("85000")

    def test_account_state_decimal_conversion(self):
        """Test Decimal conversion for all amount fields."""
        state = AccountState(
            balance=100000.50,  # Float
            equity="98500.75",  # String
            margin=15000,  # Int
            free_margin="85000.25",  # String
            initial_capital=100000,  # Int
            timestamp=datetime.utcnow(),
        )

        assert isinstance(state.balance, Decimal)
        assert isinstance(state.equity, Decimal)
        assert isinstance(state.margin, Decimal)
        assert isinstance(state.free_margin, Decimal)

    def test_account_state_frozen(self):
        """Verify AccountState is frozen."""
        state = AccountState(
            balance=Decimal("100000"),
            equity=Decimal("98500"),
            margin=Decimal("15000"),
            free_margin=Decimal("85000"),
            initial_capital=Decimal("100000"),
            timestamp=datetime.utcnow(),
        )

        with pytest.raises(Exception):
            state.balance = Decimal("99000")


# ============================================================================
# FTMOLimits Tests
# ============================================================================


class TestFTMOLimits:
    """Test FTMOLimits value object."""

    def test_create_two_step_limits(self):
        """Create FTMO limits for two-step challenge."""
        limits = FTMOLimits(
            mdl_limit=Decimal("95000"),
            ml_limit=Decimal("90000"),
            balance_at_midnight=Decimal("100000"),
            highest_eod_balance=Decimal("100000"),
            challenge_type=ChallengeType.TWO_STEP,
        )

        assert limits.mdl_limit == Decimal("95000")
        assert limits.ml_limit == Decimal("90000")
        assert limits.challenge_type == ChallengeType.TWO_STEP

    def test_create_one_step_limits(self):
        """Create FTMO limits for one-step challenge."""
        limits = FTMOLimits(
            mdl_limit=Decimal("95000"),
            ml_limit=Decimal("90000"),
            balance_at_midnight=Decimal("100000"),
            highest_eod_balance=Decimal("102000"),
            challenge_type=ChallengeType.ONE_STEP,
        )

        assert limits.challenge_type == ChallengeType.ONE_STEP
        assert limits.highest_eod_balance == Decimal("102000")

    def test_ftmo_limits_decimal_conversion(self):
        """Test Decimal conversion for limit amounts."""
        limits = FTMOLimits(
            mdl_limit=95000.50,  # Float
            ml_limit="90000.75",  # String
            balance_at_midnight=100000,  # Int
            highest_eod_balance="102000.25",  # String
            challenge_type=ChallengeType.TWO_STEP,
        )

        assert isinstance(limits.mdl_limit, Decimal)
        assert isinstance(limits.ml_limit, Decimal)
        assert isinstance(limits.balance_at_midnight, Decimal)


# ============================================================================
# PositionInfo Tests
# ============================================================================


class TestPositionInfo:
    """Test PositionInfo value object."""

    def test_create_long_position(self):
        """Create long position info."""
        pos = PositionInfo(
            ticket=12345,
            symbol="EURUSD",
            type="LONG",
            volume=Decimal("1.5"),
            price_open=Decimal("1.0850"),
            sl=Decimal("1.0750"),
            tp=Decimal("1.0950"),
            profit=Decimal("150.50"),
            magic=234000,
        )

        assert pos.ticket == 12345
        assert pos.symbol == "EURUSD"
        assert pos.type == "LONG"
        assert pos.volume == Decimal("1.5")
        assert pos.profit == Decimal("150.50")

    def test_create_short_position(self):
        """Create short position with floating loss."""
        pos = PositionInfo(
            ticket=54321,
            symbol="GBPUSD",
            type="SHORT",
            volume=Decimal("2.0"),
            price_open=Decimal("1.2700"),
            profit=Decimal("-300.00"),  # Negative for loss
            magic=234000,
        )

        assert pos.type == "SHORT"
        assert pos.profit == Decimal("-300.00")

    def test_position_decimal_conversion(self):
        """Test Decimal conversion for position amounts."""
        pos = PositionInfo(
            ticket=12345,
            symbol="EURUSD",
            type="LONG",
            volume=1.5,  # Float
            price_open="1.0850",  # String
            sl=1.0750,  # Float
            tp="1.0950",  # String
            profit=150.50,  # Float
        )

        assert isinstance(pos.volume, Decimal)
        assert isinstance(pos.price_open, Decimal)
        assert isinstance(pos.profit, Decimal)


# ============================================================================
# Event Tests
# ============================================================================


class TestSignalGenerated:
    """Test SignalGenerated event."""

    def test_signal_generated_event(self):
        """Create SignalGenerated event."""
        signal = TradingSignal(
            symbol="EURUSD",
            direction=SignalDirection.LONG,
            confidence=Decimal("0.85"),
            timestamp=datetime.utcnow(),
            correlation_id=uuid4(),
            strategy_name="MA_Crossover",
        )

        event = SignalGenerated(
            timestamp=datetime.utcnow(),
            correlation_id=signal.correlation_id,
            signal=signal,
        )

        assert isinstance(event.event_id, UUID)
        assert event.signal == signal
        assert isinstance(event.timestamp, datetime)

    def test_event_frozen(self):
        """Verify events are frozen."""
        signal = TradingSignal(
            symbol="EURUSD",
            direction=SignalDirection.LONG,
            confidence=Decimal("0.85"),
            timestamp=datetime.utcnow(),
            correlation_id=uuid4(),
            strategy_name="Test",
        )

        event = SignalGenerated(
            timestamp=datetime.utcnow(),
            correlation_id=signal.correlation_id,
            signal=signal,
        )

        with pytest.raises(Exception):
            event.signal = None


class TestRiskChecked:
    """Test RiskChecked event."""

    def test_risk_checked_event_approved(self):
        """Create RiskChecked event for approved trade."""
        corr_id = uuid4()
        event = RiskChecked(
            timestamp=datetime.utcnow(),
            correlation_id=corr_id,
            approved=True,
            risk_state=RiskState.NORMAL,
            reason="Trade approved - sufficient headroom",
        )

        assert event.approved is True
        assert event.risk_state == RiskState.NORMAL
        assert "approved" in event.reason.lower()

    def test_risk_checked_event_rejected(self):
        """Create RiskChecked event for rejected trade."""
        corr_id = uuid4()
        event = RiskChecked(
            timestamp=datetime.utcnow(),
            correlation_id=corr_id,
            approved=False,
            risk_state=RiskState.HALT,
            reason="MDL limit breached",
        )

        assert event.approved is False
        assert event.risk_state == RiskState.HALT


class TestOrderSubmitted:
    """Test OrderSubmitted event."""

    def test_order_submitted_event(self):
        """Create OrderSubmitted event."""
        corr_id = uuid4()
        req = TradeRequest(
            symbol="EURUSD",
            action=OrderAction.BUY,
            order_type=OrderType.MARKET,
            volume=Decimal("1.0"),
            magic=234000,
            correlation_id=corr_id,
        )

        event = OrderSubmitted(
            timestamp=datetime.utcnow(),
            correlation_id=corr_id,
            trade_request=req,
            submission_time=datetime.utcnow(),
        )

        assert event.trade_request == req
        assert event.trade_request.symbol == "EURUSD"


class TestOrderFilled:
    """Test OrderFilled event."""

    def test_order_filled_event(self):
        """Create OrderFilled event."""
        corr_id = uuid4()
        event = OrderFilled(
            timestamp=datetime.utcnow(),
            correlation_id=corr_id,
            deal_ticket=999,
            fill_price=Decimal("1.0850"),
            slippage=Decimal("-0.0005"),
            volume_filled=Decimal("1.0"),
        )

        assert event.deal_ticket == 999
        assert event.fill_price == Decimal("1.0850")
        assert isinstance(event.slippage, Decimal)

    def test_order_filled_decimal_conversion(self):
        """Test Decimal conversion in OrderFilled."""
        corr_id = uuid4()
        event = OrderFilled(
            timestamp=datetime.utcnow(),
            correlation_id=corr_id,
            deal_ticket=999,
            fill_price="1.0850",  # String
            slippage=-0.0005,  # Float
            volume_filled=1.0,  # Float
        )

        assert isinstance(event.fill_price, Decimal)
        assert isinstance(event.slippage, Decimal)
        assert isinstance(event.volume_filled, Decimal)


class TestRiskStateChanged:
    """Test RiskStateChanged event."""

    def test_risk_state_transition(self):
        """Create RiskStateChanged event for state transition."""
        event = RiskStateChanged(
            timestamp=datetime.utcnow(),
            correlation_id=uuid4(),
            old_state=RiskState.NORMAL,
            new_state=RiskState.CAUTION,
            trigger="ML headroom 75% consumed",
        )

        assert event.old_state == RiskState.NORMAL
        assert event.new_state == RiskState.CAUTION
        assert "headroom" in event.trigger.lower()


class TestCircuitBreakerTriggered:
    """Test CircuitBreakerTriggered event."""

    def test_circuit_breaker_event(self):
        """Create CircuitBreakerTriggered event."""
        event = CircuitBreakerTriggered(
            timestamp=datetime.utcnow(),
            correlation_id=uuid4(),
            reason="MDL limit breached - emergency halt",
            positions_to_close=3,
        )

        assert "breach" in event.reason.lower()
        assert event.positions_to_close == 3


# ============================================================================
# Configuration Tests
# ============================================================================


class TestFTMOConfig:
    """Test FTMOConfig configuration model."""

    def test_two_step_config(self):
        """Create two-step challenge configuration."""
        config = FTMOConfig(
            initial_capital=Decimal("200000"),
            challenge_type=ChallengeType.TWO_STEP,
            mdl_pct=Decimal("5"),
            ml_pct=Decimal("10"),
        )

        assert config.initial_capital == Decimal("200000")
        assert config.challenge_type == ChallengeType.TWO_STEP
        assert config.mdl_pct == Decimal("5")
        assert config.ml_pct == Decimal("10")

    def test_one_step_config(self):
        """Create one-step challenge configuration."""
        config = FTMOConfig(
            initial_capital=Decimal("100000"),
            challenge_type=ChallengeType.ONE_STEP,
            mdl_pct=Decimal("3"),
            ml_pct=Decimal("10"),
        )

        assert config.challenge_type == ChallengeType.ONE_STEP
        assert config.mdl_pct == Decimal("3")

    def test_config_decimal_conversion(self):
        """Test Decimal conversion in FTMOConfig."""
        config = FTMOConfig(
            initial_capital=200000,  # Int
            challenge_type=ChallengeType.TWO_STEP,
            mdl_pct=5.0,  # Float
            ml_pct="10",  # String
        )

        assert isinstance(config.initial_capital, Decimal)
        assert isinstance(config.mdl_pct, Decimal)
        assert isinstance(config.ml_pct, Decimal)

    def test_config_frozen(self):
        """Verify FTMOConfig is frozen."""
        config = FTMOConfig(
            initial_capital=Decimal("200000"),
            challenge_type=ChallengeType.TWO_STEP,
        )

        with pytest.raises(Exception):
            config.initial_capital = Decimal("150000")


class TestSystemConfig:
    """Test SystemConfig configuration model."""

    def test_create_system_config(self):
        """Create system configuration."""
        config = SystemConfig(
            mt5_login=12345678,
            mt5_server="FTMO-Demo",
            mt5_password="encrypted_password",
            symbols=["EURUSD", "GBPUSD", "USDJPY"],
            log_level="INFO",
        )

        assert config.mt5_login == 12345678
        assert config.mt5_server == "FTMO-Demo"
        assert "EURUSD" in config.symbols
        assert len(config.symbols) == 3

    def test_system_config_validation(self):
        """Test SystemConfig validators."""
        # Empty symbols list - invalid
        with pytest.raises(ValidationError):
            SystemConfig(
                mt5_login=12345678,
                mt5_server="FTMO-Demo",
                mt5_password="password",
                symbols=[],  # Empty
            )

    def test_system_config_frozen(self):
        """Verify SystemConfig is frozen."""
        config = SystemConfig(
            mt5_login=12345678,
            mt5_server="FTMO-Demo",
            mt5_password="password",
            symbols=["EURUSD"],
        )

        with pytest.raises(Exception):
            config.mt5_login = 99999999


# ============================================================================
# Edge Cases and Integration Tests
# ============================================================================


class TestDecimalPrecision:
    """Test Decimal precision is preserved throughout models."""

    def test_signal_confidence_precision(self):
        """Verify high-precision decimal for confidence."""
        signal = TradingSignal(
            symbol="EURUSD",
            direction=SignalDirection.LONG,
            confidence=Decimal("0.123456789"),
            timestamp=datetime.utcnow(),
            correlation_id=uuid4(),
            strategy_name="Test",
        )

        assert signal.confidence == Decimal("0.123456789")

    def test_position_profit_precision(self):
        """Verify precision in position profit/loss."""
        pos = PositionInfo(
            ticket=1,
            symbol="EURUSD",
            type="LONG",
            volume=Decimal("1.0"),
            price_open=Decimal("1.08500000"),
            profit=Decimal("123.45678900"),
        )

        assert pos.price_open == Decimal("1.08500000")
        assert pos.profit == Decimal("123.45678900")


class TestEventUUIDs:
    """Test UUID generation consistency in events."""

    def test_unique_event_ids(self):
        """Verify each event gets unique event_id."""
        corr_id = uuid4()

        event1 = SignalGenerated(
            timestamp=datetime.utcnow(),
            correlation_id=corr_id,
            signal=TradingSignal(
                symbol="EURUSD",
                direction=SignalDirection.LONG,
                confidence=Decimal("0.5"),
                timestamp=datetime.utcnow(),
                correlation_id=corr_id,
                strategy_name="Test",
            ),
        )

        event2 = SignalGenerated(
            timestamp=datetime.utcnow(),
            correlation_id=corr_id,
            signal=TradingSignal(
                symbol="EURUSD",
                direction=SignalDirection.LONG,
                confidence=Decimal("0.5"),
                timestamp=datetime.utcnow(),
                correlation_id=corr_id,
                strategy_name="Test",
            ),
        )

        # Same correlation_id but different event_ids
        assert event1.correlation_id == event2.correlation_id
        assert event1.event_id != event2.event_id
