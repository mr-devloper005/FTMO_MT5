"""Pytest configuration and fixtures for FTMO MT5 trading system tests.

Provides comprehensive fixtures for testing:
- Configuration objects (FTMO and System)
- Event store with temporary directory
- Mock MT5 gateway
- Sample trading signals and account states
- Sample positions
- System components
"""

import asyncio
import os
import tempfile
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, AsyncGenerator, Generator
from pathlib import Path
from uuid import uuid4, UUID

import pytest
from unittest.mock import AsyncMock, MagicMock

from ftmo_mt5.core.models import (
    FTMOConfig,
    SystemConfig,
    ChallengeType,
    TradingSignal,
    SignalDirection,
    TradeRequest,
    OrderAction,
    OrderType,
    FillPolicy,
    AccountState,
    PositionInfo,
    RiskState,
)
from ftmo_mt5.store.event_store import EventStore
from ftmo_mt5.execution.mt5_gateway import MT5Gateway, MockMT5


# ============================================================================
# Configuration Fixtures
# ============================================================================


@pytest.fixture
def ftmo_config() -> FTMOConfig:
    """Provide default FTMO configuration for testing.

    Returns a ONE_STEP challenge with 100k initial capital,
    5% MDL, and 10% ML limits.
    """
    return FTMOConfig(
        initial_capital=Decimal("100000"),
        challenge_type=ChallengeType.ONE_STEP,
        mdl_pct=Decimal("5"),
        ml_pct=Decimal("10"),
        caution_threshold_pct=Decimal("70"),
        risk_off_threshold_pct=Decimal("85"),
        pre_midnight_flatten_minutes=30,
        magic=234000,
    )


@pytest.fixture
def system_config() -> SystemConfig:
    """Provide default system configuration for testing.

    Includes MT5 connection params, symbols, and timing settings.
    """
    return SystemConfig(
        mt5_login=123456789,
        mt5_server="FTMODemo01",
        mt5_password="test_password",
        symbols=["EURUSD", "GBPUSD", "USDJPY"],
        tick_interval_ms=100,
        heartbeat_interval_s=5,
        max_agent_restarts=3,
        otel_endpoint=None,
        log_level="DEBUG",
    )


# ============================================================================
# Storage & Event Store Fixtures
# ============================================================================


@pytest.fixture
def event_store_dir() -> Generator[str, None, None]:
    """Create temporary directory for event store.

    Yields:
        Path to temporary directory.

    Cleans up after test.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
async def event_store(event_store_dir: str) -> AsyncGenerator[EventStore, None]:
    """Provide initialized event store with temporary storage.

    Args:
        event_store_dir: Temporary directory from fixture.

    Yields:
        EventStore instance ready for testing.
    """
    store = EventStore(storage_path=event_store_dir)
    yield store
    # Cleanup handled by event_store_dir fixture


# ============================================================================
# MT5 Gateway Fixtures
# ============================================================================


@pytest.fixture
def mock_mt5_gateway(system_config: SystemConfig) -> MT5Gateway:
    """Provide mock MT5 gateway for testing without broker connection.

    Returns gateway configured with test credentials.
    Uses MockMT5 internally for simulated order execution.
    """
    gateway = MT5Gateway(
        login=system_config.mt5_login,
        server=system_config.mt5_server,
        password=system_config.mt5_password,
    )
    # Gateway uses MockMT5 internally when not connected to real broker
    return gateway


# ============================================================================
# Trading Signal & Action Fixtures
# ============================================================================


@pytest.fixture
def sample_trading_signal() -> TradingSignal:
    """Provide sample trading signal for testing.

    Returns a LONG signal for EURUSD with high confidence.
    """
    correlation_id = uuid4()
    return TradingSignal(
        signal_id=uuid4(),
        symbol="EURUSD",
        direction=SignalDirection.LONG,
        confidence=Decimal("0.85"),
        timestamp=datetime.now(timezone.utc),
        correlation_id=correlation_id,
        meta_label_prob=Decimal("0.72"),
        strategy_name="TestStrategy",
    )


@pytest.fixture
def sample_trade_request() -> TradeRequest:
    """Provide sample trade request for testing.

    Returns a market BUY order for 1.0 lot of EURUSD.
    """
    correlation_id = uuid4()
    return TradeRequest(
        symbol="EURUSD",
        action=OrderAction.BUY,
        order_type=OrderType.MARKET,
        volume=Decimal("1.0"),
        price=None,
        sl=Decimal("1.0850"),
        tp=Decimal("1.1050"),
        deviation=10,
        magic=234000,
        comment="test_order",
        fill_policy=FillPolicy.IOC,
        correlation_id=correlation_id,
    )


@pytest.fixture
def sample_long_signal() -> TradingSignal:
    """Provide sample LONG trading signal."""
    correlation_id = uuid4()
    return TradingSignal(
        signal_id=uuid4(),
        symbol="GBPUSD",
        direction=SignalDirection.LONG,
        confidence=Decimal("0.75"),
        timestamp=datetime.now(timezone.utc),
        correlation_id=correlation_id,
        meta_label_prob=Decimal("0.68"),
        strategy_name="MomentumStrategy",
    )


@pytest.fixture
def sample_short_signal() -> TradingSignal:
    """Provide sample SHORT trading signal."""
    correlation_id = uuid4()
    return TradingSignal(
        signal_id=uuid4(),
        symbol="USDJPY",
        direction=SignalDirection.SHORT,
        confidence=Decimal("0.80"),
        timestamp=datetime.now(timezone.utc),
        correlation_id=correlation_id,
        meta_label_prob=Decimal("0.70"),
        strategy_name="TrendStrategy",
    )


@pytest.fixture
def sample_flat_signal() -> TradingSignal:
    """Provide sample FLAT (neutral) trading signal."""
    correlation_id = uuid4()
    return TradingSignal(
        signal_id=uuid4(),
        symbol="EURUSD",
        direction=SignalDirection.FLAT,
        confidence=Decimal("0.55"),
        timestamp=datetime.now(timezone.utc),
        correlation_id=correlation_id,
        meta_label_prob=Decimal("0.50"),
        strategy_name="NeutralStrategy",
    )


# ============================================================================
# Account State Fixtures
# ============================================================================


@pytest.fixture
def sample_account_state() -> AccountState:
    """Provide sample account state snapshot.

    Returns healthy account state with good margin.
    """
    return AccountState(
        balance=Decimal("105000"),
        equity=Decimal("105500"),
        margin=Decimal("25000"),
        free_margin=Decimal("80500"),
        initial_capital=Decimal("100000"),
        timestamp=datetime.now(timezone.utc),
    )


@pytest.fixture
def sample_account_state_low_margin() -> AccountState:
    """Provide account state with low available margin.

    For testing margin warning scenarios.
    """
    return AccountState(
        balance=Decimal("100000"),
        equity=Decimal("100000"),
        margin=Decimal("95000"),
        free_margin=Decimal("5000"),
        initial_capital=Decimal("100000"),
        timestamp=datetime.now(timezone.utc),
    )


@pytest.fixture
def sample_account_state_underwater() -> AccountState:
    """Provide account state with significant drawdown.

    For testing risk management escalation.
    """
    return AccountState(
        balance=Decimal("92000"),
        equity=Decimal("89000"),
        margin=Decimal("50000"),
        free_margin=Decimal("39000"),
        initial_capital=Decimal("100000"),
        timestamp=datetime.now(timezone.utc),
    )


@pytest.fixture
def sample_account_state_critical() -> AccountState:
    """Provide account state near margin call.

    For testing circuit breaker scenarios.
    """
    return AccountState(
        balance=Decimal("88000"),
        equity=Decimal("85000"),
        margin=Decimal("82000"),
        free_margin=Decimal("3000"),
        initial_capital=Decimal("100000"),
        timestamp=datetime.now(timezone.utc),
    )


# ============================================================================
# Position Fixtures
# ============================================================================


@pytest.fixture
def sample_position_long() -> PositionInfo:
    """Provide sample long position.

    1.0 lot EURUSD entry at 1.0950, currently 1.1000 (profitable).
    """
    return PositionInfo(
        ticket=1001,
        symbol="EURUSD",
        type="LONG",
        volume=Decimal("1.0"),
        price_open=Decimal("1.0950"),
        sl=Decimal("1.0850"),
        tp=Decimal("1.1100"),
        profit=Decimal("50"),
        magic=234000,
        comment="test_long_position",
    )


@pytest.fixture
def sample_position_short() -> PositionInfo:
    """Provide sample short position.

    0.5 lot GBPUSD entry at 1.2700, currently 1.2680 (profitable).
    """
    return PositionInfo(
        ticket=1002,
        symbol="GBPUSD",
        type="SHORT",
        volume=Decimal("0.5"),
        price_open=Decimal("1.2700"),
        sl=Decimal("1.2800"),
        tp=Decimal("1.2600"),
        profit=Decimal("10"),
        magic=234000,
        comment="test_short_position",
    )


@pytest.fixture
def sample_position_losing() -> PositionInfo:
    """Provide sample losing position.

    For testing stop loss and position management.
    """
    return PositionInfo(
        ticket=1003,
        symbol="USDJPY",
        type="LONG",
        volume=Decimal("2.0"),
        price_open=Decimal("149.50"),
        sl=Decimal("148.50"),
        tp=Decimal("151.50"),
        profit=Decimal("-200"),
        magic=234000,
        comment="test_losing_position",
    )


@pytest.fixture
def sample_positions_list(
    sample_position_long: PositionInfo,
    sample_position_short: PositionInfo,
) -> list[PositionInfo]:
    """Provide list of multiple open positions."""
    return [sample_position_long, sample_position_short]


# ============================================================================
# Async Test Support Fixtures
# ============================================================================


@pytest.fixture
def event_loop() -> Generator[asyncio.AbstractEventLoop, None, None]:
    """Provide event loop for async tests.

    Ensures pytest-asyncio properly manages event loop lifecycle.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    yield loop
    loop.close()


# ============================================================================
# Mock Component Fixtures
# ============================================================================


@pytest.fixture
def mock_executor() -> MagicMock:
    """Provide mock MT5 executor."""
    executor = MagicMock()
    executor.execute_trade = AsyncMock()
    executor.close_position = AsyncMock()
    executor.get_positions = AsyncMock(return_value=[])
    executor.get_account_state = AsyncMock()
    return executor


@pytest.fixture
def mock_gateway() -> MagicMock:
    """Provide mock MT5 gateway."""
    gateway = MagicMock()
    gateway.connect = AsyncMock()
    gateway.disconnect = AsyncMock()
    gateway.is_connected = MagicMock(return_value=True)
    gateway.get_tick = AsyncMock()
    gateway.send_order = AsyncMock()
    return gateway


@pytest.fixture
def mock_rule_engine() -> MagicMock:
    """Provide mock rule engine."""
    engine = MagicMock()
    engine.check_signal = AsyncMock(return_value=True)
    engine.update_ftmo_limits = AsyncMock()
    engine.check_drawdown = AsyncMock(return_value=RiskState.NORMAL)
    return engine


# ============================================================================
# Environment Fixtures
# ============================================================================


@pytest.fixture(autouse=True)
def clean_env() -> Generator[None, None, None]:
    """Clean up environment variables before and after tests."""
    original_env = os.environ.copy()
    yield
    os.environ.clear()
    os.environ.update(original_env)
