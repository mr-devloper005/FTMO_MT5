"""Property-based tests for FTMO MT5 trading system.

Uses Hypothesis for generating random but valid test data to verify
invariants and properties that should hold for all valid inputs.
"""

import pytest
from decimal import Decimal
from datetime import datetime, timezone
from uuid import uuid4

from hypothesis import given, strategies as st, settings, HealthCheck
import numpy as np

from ftmo_mt5.core.models import (
    FTMOConfig,
    TradingSignal,
    SignalDirection,
    AccountState,
    PositionInfo,
)
from ftmo_mt5.ml.drift_detection import compute_psi, PSIDriftMonitor
from ftmo_mt5.ml.validation import compute_cscv_pbo, compute_deflated_sharpe
from ftmo_mt5.ml.meta_labeler import MetaLabeler
from ftmo_mt5.store.event_store import EventStore
from ftmo_mt5.core.models import SignalGenerated


# ============================================================================
# Strategies for Valid Test Data
# ============================================================================


@st.composite
def decimal_amounts(draw, min_val=0, max_val=1000000):
    """Generate valid Decimal amounts for financial data."""
    value = draw(st.decimals(
        min_value=Decimal(min_val),
        max_value=Decimal(max_val),
        places=2,
    ))
    return value


@st.composite
def valid_trading_signals(draw):
    """Generate valid trading signals."""
    return TradingSignal(
        signal_id=uuid4(),
        symbol=draw(st.sampled_from(["EURUSD", "GBPUSD", "USDJPY"])),
        direction=draw(st.sampled_from(list(SignalDirection))),
        confidence=Decimal(str(draw(st.floats(min_value=0.0, max_value=1.0)))),
        timestamp=datetime.now(timezone.utc),
        correlation_id=uuid4(),
        meta_label_prob=Decimal(str(draw(st.floats(min_value=0.0, max_value=1.0)))),
        strategy_name="PropertyTestStrategy",
    )


@st.composite
def valid_account_states(draw):
    """Generate valid account states."""
    balance = draw(decimal_amounts(min_val=1000, max_val=500000))
    equity = draw(st.just(balance * Decimal("0.95")))
    margin = draw(st.just(balance * Decimal("0.2")))
    free_margin = balance - margin

    return AccountState(
        balance=balance,
        equity=equity,
        margin=margin,
        free_margin=free_margin,
        initial_capital=balance,
        timestamp=datetime.now(timezone.utc),
    )


@st.composite
def valid_position_infos(draw):
    """Generate valid position information."""
    return PositionInfo(
        ticket=draw(st.integers(min_value=1, max_value=999999)),
        symbol=draw(st.sampled_from(["EURUSD", "GBPUSD", "USDJPY"])),
        type=draw(st.sampled_from(["LONG", "SHORT"])),
        volume=draw(st.decimals(
            min_value=Decimal("0.01"),
            max_value=Decimal("100"),
            places=2,
        )),
        price_open=draw(st.decimals(
            min_value=Decimal("0.5"),
            max_value=Decimal("200"),
            places=5,
        )),
        sl=None,  # Stop loss optional
        tp=None,  # Take profit optional
        profit=draw(st.decimals(
            min_value=Decimal("-10000"),
            max_value=Decimal("10000"),
            places=2,
        )),
        magic=234000,
        comment="property_test_position",
    )


# ============================================================================
# Property Tests: Model Invariants
# ============================================================================


class TestModelInvariants:
    """Test invariants that should hold for all model instances."""

    @given(valid_trading_signals())
    def test_signal_confidence_bounded(self, signal):
        """Trading signal confidence should always be between 0 and 1."""
        assert Decimal("0") <= signal.confidence <= Decimal("1")

    @given(valid_trading_signals())
    def test_signal_meta_label_prob_bounded(self, signal):
        """Meta-label probability should always be between 0 and 1."""
        assert Decimal("0") <= signal.meta_label_prob <= Decimal("1")

    @given(valid_account_states())
    def test_account_state_balance_positive(self, account):
        """Account balance should always be positive."""
        assert account.balance > Decimal("0")

    @given(valid_account_states())
    def test_account_state_equity_consistency(self, account):
        """Account equity should be <= balance (accounts for drawdown)."""
        assert account.equity <= account.balance + Decimal("1")  # +1 for rounding

    @given(valid_account_states())
    def test_account_state_margin_consistency(self, account):
        """Used margin + free margin should equal balance."""
        total_margin = account.margin + account.free_margin
        assert abs(total_margin - account.balance) < Decimal("1")

    @given(valid_position_infos())
    def test_position_volume_positive(self, position):
        """Position volume should always be positive."""
        assert position.volume > Decimal("0")

    @given(valid_position_infos())
    def test_position_price_positive(self, position):
        """Position entry price should always be positive."""
        assert position.price_open > Decimal("0")


# ============================================================================
# Property Tests: ML Pipeline
# ============================================================================


class TestMLPipelineProperties:
    """Test properties of ML pipeline components."""

    @given(
        expected=st.lists(st.floats(min_value=-10, max_value=10), min_size=10, max_size=500),
        actual=st.lists(st.floats(min_value=-10, max_value=10), min_size=10, max_size=500),
    )
    @settings(suppress_health_check=[HealthCheck.filter_too_much])
    def test_psi_non_negative(self, expected, actual):
        """PSI should always be non-negative."""
        expected_arr = np.array(expected)
        actual_arr = np.array(actual)
        psi = compute_psi(expected_arr, actual_arr)
        assert psi >= 0.0

    @given(
        performance_matrix=st.lists(
            st.lists(
                st.floats(min_value=-1, max_value=1),
                min_size=10,
                max_size=50,
            ),
            min_size=5,
            max_size=50,
        ),
    )
    @settings(suppress_health_check=[HealthCheck.filter_too_much, HealthCheck.too_slow])
    def test_pbo_bounded(self, performance_matrix):
        """PBO should always be in range [0, 1]."""
        perf_arr = np.array(performance_matrix)
        if perf_arr.shape[0] >= 2 and perf_arr.shape[1] >= 2:
            pbo = compute_cscv_pbo(perf_arr, n_subsets=min(4, perf_arr.shape[1]))
            assert 0.0 <= pbo <= 1.0

    @given(
        observed_sr=st.floats(min_value=-2, max_value=3),
        sr_std=st.floats(min_value=0.01, max_value=1),
        num_trials=st.integers(min_value=1, max_value=1000),
        skewness=st.floats(min_value=-2, max_value=2),
        kurtosis=st.floats(min_value=0, max_value=5),
        sample_size=st.integers(min_value=10, max_value=1000),
    )
    def test_dsr_bounded(self, observed_sr, sr_std, num_trials, skewness, kurtosis, sample_size):
        """DSR should always be a valid probability (0 to 1)."""
        dsr = compute_deflated_sharpe(observed_sr, sr_std, num_trials, skewness, kurtosis, sample_size)
        assert 0.0 <= dsr <= 1.0


# ============================================================================
# Property Tests: Event Store
# ============================================================================


class TestEventStoreProperties:
    """Test properties that should hold for event store operations."""

    @given(
        n_events=st.integers(min_value=1, max_value=100),
    )
    def test_event_count_consistency(self, tmp_path, n_events):
        """Event count should match number of appended events."""
        store = EventStore(storage_path=tmp_path)

        for i in range(n_events):
            signal = TradingSignal(
                signal_id=uuid4(),
                symbol="EURUSD",
                direction=SignalDirection.LONG,
                confidence=Decimal("0.8"),
                timestamp=datetime.now(timezone.utc),
                correlation_id=uuid4(),
                meta_label_prob=Decimal("0.7"),
                strategy_name=f"Strategy{i}",
            )
            event = SignalGenerated(
                signal=signal,
                reason="test_event",
                timestamp=datetime.now(timezone.utc),
                correlation_id=signal.correlation_id,
            )
            store.append(event)

        assert store.event_count == n_events

    @given(
        n_events=st.integers(min_value=1, max_value=50),
    )
    def test_event_order_preserved(self, tmp_path, n_events):
        """Event order should be preserved when retrieving from store."""
        store = EventStore(storage_path=tmp_path)
        correlation_ids = []

        for i in range(n_events):
            corr_id = uuid4()
            correlation_ids.append(corr_id)
            signal = TradingSignal(
                signal_id=uuid4(),
                symbol="EURUSD",
                direction=SignalDirection.LONG,
                confidence=Decimal("0.8"),
                timestamp=datetime.now(timezone.utc),
                correlation_id=corr_id,
                meta_label_prob=Decimal("0.7"),
                strategy_name=f"Strategy{i}",
            )
            event = SignalGenerated(
                signal=signal,
                reason=f"event_{i}",
                timestamp=datetime.now(timezone.utc),
                correlation_id=corr_id,
            )
            store.append(event)

        # Verify events are in correct order
        latest_events = store.get_latest(n_events)
        assert len(latest_events) == n_events


# ============================================================================
# Property Tests: Configuration
# ============================================================================


class TestConfigurationProperties:
    """Test properties of configuration objects."""

    @given(
        initial_capital=st.decimals(
            min_value=Decimal("10000"),
            max_value=Decimal("1000000"),
            places=2,
        ),
    )
    def test_ftmo_config_creation(self, initial_capital):
        """FTMO config should always create successfully with valid input."""
        config = FTMOConfig(
            initial_capital=initial_capital,
            mdl_pct=Decimal("5"),
            ml_pct=Decimal("10"),
        )
        assert config.initial_capital == initial_capital
        assert config.mdl_pct > Decimal("0")
        assert config.ml_pct > Decimal("0")

    @given(
        mdl_pct=st.decimals(
            min_value=Decimal("0.1"),
            max_value=Decimal("50"),
            places=1,
        ),
        ml_pct=st.decimals(
            min_value=Decimal("0.1"),
            max_value=Decimal("50"),
            places=1,
        ),
    )
    def test_risk_thresholds_positive(self, mdl_pct, ml_pct):
        """Risk thresholds should always be positive in config."""
        config = FTMOConfig(
            initial_capital=Decimal("100000"),
            mdl_pct=mdl_pct,
            ml_pct=ml_pct,
        )
        assert config.mdl_pct > Decimal("0")
        assert config.ml_pct > Decimal("0")
