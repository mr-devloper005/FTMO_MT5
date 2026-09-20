"""Comprehensive test suite for Event Store.

Tests:
- Append and retrieve operations
- Correlation ID filtering
- Event type filtering
- Time range queries
- File persistence and replay
- Thread safety (concurrent appends)
- Snapshot creation
- JSON serialization/deserialization with Decimals
"""

import pytest
import tempfile
import threading
import json
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from ftmo_mt5.store.event_store import EventStore, DecimalEncoder
from ftmo_mt5.core.models import (
    SignalGenerated,
    RiskChecked,
    OrderSubmitted,
    OrderFilled,
    OrderRejected,
    PositionClosed,
    RiskStateChanged,
    CircuitBreakerTriggered,
    TradingSignal,
    TradeRequest,
    SignalDirection,
    OrderAction,
    OrderType,
    RiskState,
)


# ============================================================================
# Event Store Basics
# ============================================================================


class TestEventStoreBasics:
    """Test basic event store operations."""

    @pytest.fixture
    def event_store(self):
        """Create temporary event store."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = EventStore(Path(tmpdir))
            yield store

    def test_event_store_initialization(self, event_store):
        """Test event store initializes successfully."""
        assert event_store.event_count == 0
        assert event_store.file_path.exists()

    def test_append_single_event(self, event_store):
        """Test appending a single event."""
        corr_id = uuid4()
        event = RiskChecked(
            timestamp=datetime.utcnow(),
            correlation_id=corr_id,
            approved=True,
            risk_state=RiskState.NORMAL,
            reason="Trade approved",
        )

        event_store.append(event)

        assert event_store.event_count == 1

    def test_append_multiple_events(self, event_store):
        """Test appending multiple events."""
        corr_id = uuid4()

        for i in range(5):
            event = RiskChecked(
                timestamp=datetime.utcnow(),
                correlation_id=corr_id,
                approved=True,
                risk_state=RiskState.NORMAL,
                reason=f"Trade {i} approved",
            )
            event_store.append(event)

        assert event_store.event_count == 5

    def test_get_latest_events(self, event_store):
        """Test retrieving latest N events."""
        corr_id = uuid4()

        for i in range(10):
            event = RiskChecked(
                timestamp=datetime.utcnow() + timedelta(seconds=i),
                correlation_id=corr_id,
                approved=True,
                risk_state=RiskState.NORMAL,
                reason=f"Trade {i}",
            )
            event_store.append(event)

        latest_5 = event_store.get_latest(5)
        assert len(latest_5) == 5

        # Latest should be the last appended
        assert "Trade 9" in latest_5[-1].reason
        assert "Trade 5" in latest_5[0].reason


# ============================================================================
# Correlation ID Filtering
# ============================================================================


class TestCorrelationIDFiltering:
    """Test filtering by correlation ID."""

    @pytest.fixture
    def event_store(self):
        """Create temporary event store."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = EventStore(Path(tmpdir))
            yield store

    def test_get_by_correlation_id(self, event_store):
        """Test retrieving events by correlation ID."""
        corr_id_1 = uuid4()
        corr_id_2 = uuid4()

        # Add events with different correlation IDs
        for i in range(3):
            event = RiskChecked(
                timestamp=datetime.utcnow(),
                correlation_id=corr_id_1,
                approved=True,
                risk_state=RiskState.NORMAL,
                reason=f"Trade 1-{i}",
            )
            event_store.append(event)

        for i in range(2):
            event = RiskChecked(
                timestamp=datetime.utcnow(),
                correlation_id=corr_id_2,
                approved=False,
                risk_state=RiskState.HALT,
                reason=f"Trade 2-{i}",
            )
            event_store.append(event)

        # Retrieve by first correlation ID
        events_1 = event_store.get_by_correlation_id(str(corr_id_1))
        assert len(events_1) == 3
        assert all(e.correlation_id == corr_id_1 for e in events_1)

        # Retrieve by second correlation ID
        events_2 = event_store.get_by_correlation_id(str(corr_id_2))
        assert len(events_2) == 2
        assert all(e.correlation_id == corr_id_2 for e in events_2)

    def test_correlation_id_trace_full_lifecycle(self, event_store):
        """Test tracing full trade lifecycle with correlation ID."""
        corr_id = uuid4()

        # Simulate trade lifecycle
        signal_event = SignalGenerated(
            timestamp=datetime.utcnow(),
            correlation_id=corr_id,
            signal=TradingSignal(
                symbol="EURUSD",
                direction=SignalDirection.LONG,
                confidence=Decimal("0.85"),
                timestamp=datetime.utcnow(),
                correlation_id=corr_id,
                strategy_name="MA_Crossover",
            ),
        )
        event_store.append(signal_event)

        risk_event = RiskChecked(
            timestamp=datetime.utcnow(),
            correlation_id=corr_id,
            approved=True,
            risk_state=RiskState.NORMAL,
            reason="Risk check passed",
        )
        event_store.append(risk_event)

        order_event = OrderSubmitted(
            timestamp=datetime.utcnow(),
            correlation_id=corr_id,
            trade_request=TradeRequest(
                symbol="EURUSD",
                action=OrderAction.BUY,
                order_type=OrderType.MARKET,
                volume=Decimal("1.0"),
                magic=234000,
                correlation_id=corr_id,
            ),
            submission_time=datetime.utcnow(),
        )
        event_store.append(order_event)

        # Retrieve full lifecycle
        lifecycle_events = event_store.get_by_correlation_id(str(corr_id))
        assert len(lifecycle_events) == 3
        assert isinstance(lifecycle_events[0], SignalGenerated)
        assert isinstance(lifecycle_events[1], RiskChecked)
        assert isinstance(lifecycle_events[2], OrderSubmitted)


# ============================================================================
# Event Type Filtering
# ============================================================================


class TestEventTypeFiltering:
    """Test filtering by event type."""

    @pytest.fixture
    def event_store(self):
        """Create temporary event store."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = EventStore(Path(tmpdir))
            yield store

    def test_get_by_type(self, event_store):
        """Test retrieving events by type."""
        corr_id = uuid4()

        # Add mixed event types
        event_store.append(
            SignalGenerated(
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
        )

        event_store.append(
            RiskChecked(
                timestamp=datetime.utcnow(),
                correlation_id=corr_id,
                approved=True,
                risk_state=RiskState.NORMAL,
                reason="Approved",
            )
        )

        event_store.append(
            RiskStateChanged(
                timestamp=datetime.utcnow(),
                correlation_id=corr_id,
                old_state=RiskState.NORMAL,
                new_state=RiskState.CAUTION,
                trigger="Headroom reduced",
            )
        )

        # Filter by type
        risk_checked_events = event_store.get_by_type(RiskChecked)
        assert len(risk_checked_events) == 1
        assert isinstance(risk_checked_events[0], RiskChecked)

        state_changed_events = event_store.get_by_type(RiskStateChanged)
        assert len(state_changed_events) == 1
        assert isinstance(state_changed_events[0], RiskStateChanged)


# ============================================================================
# Time Range Queries
# ============================================================================


class TestTimeRangeQueries:
    """Test time-based event queries."""

    @pytest.fixture
    def event_store(self):
        """Create temporary event store."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = EventStore(Path(tmpdir))
            yield store

    def test_get_events_since(self, event_store):
        """Test retrieving events since a timestamp."""
        corr_id = uuid4()
        now = datetime.utcnow()

        # Add events at different times
        for i in range(5):
            event = RiskChecked(
                timestamp=now + timedelta(seconds=i * 10),
                correlation_id=corr_id,
                approved=True,
                risk_state=RiskState.NORMAL,
                reason=f"Event {i}",
            )
            event_store.append(event)

        # Query events since 25 seconds ago (should get last 3 events)
        cutoff = now + timedelta(seconds=25)
        recent_events = event_store.get_events_since(cutoff)

        # Should get events at 30, 40 seconds (2 events with timestamps >= cutoff)
        assert len(recent_events) >= 2


# ============================================================================
# File Persistence and Replay
# ============================================================================


class TestFilePersistenceAndReplay:
    """Test file persistence and event replay."""

    def test_events_persisted_to_file(self):
        """Test events are written to JSONL file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = EventStore(Path(tmpdir))
            corr_id = uuid4()

            event = RiskChecked(
                timestamp=datetime.utcnow(),
                correlation_id=corr_id,
                approved=True,
                risk_state=RiskState.NORMAL,
                reason="Test",
            )
            store.append(event)

            # Check file exists and has content
            assert store.file_path.exists()
            with open(store.file_path, "r") as f:
                lines = f.readlines()
                assert len(lines) >= 1

    def test_load_events_from_file_on_init(self):
        """Test event store loads persisted events on initialization."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)

            # Create store and append events
            store1 = EventStore(tmppath)
            corr_id = uuid4()

            for i in range(3):
                event = RiskChecked(
                    timestamp=datetime.utcnow(),
                    correlation_id=corr_id,
                    approved=True,
                    risk_state=RiskState.NORMAL,
                    reason=f"Event {i}",
                )
                store1.append(event)

            # Create new store from same path
            store2 = EventStore(tmppath)

            # Should have loaded 3 events
            assert store2.event_count == 3

    def test_replay_all_events(self):
        """Test replaying all events through callback."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = EventStore(Path(tmpdir))
            corr_id = uuid4()

            # Add events
            for i in range(5):
                event = RiskChecked(
                    timestamp=datetime.utcnow(),
                    correlation_id=corr_id,
                    approved=i % 2 == 0,
                    risk_state=RiskState.NORMAL,
                    reason=f"Event {i}",
                )
                store.append(event)

            # Replay through callback
            replayed_events = []

            def collect_event(event):
                replayed_events.append(event)

            count = store.replay(collect_event)

            assert count == 5
            assert len(replayed_events) == 5

    def test_replay_from_file(self):
        """Test replaying events from persisted file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)

            # Create and populate first store
            store1 = EventStore(tmppath)
            corr_id = uuid4()

            for i in range(3):
                event = RiskChecked(
                    timestamp=datetime.utcnow(),
                    correlation_id=corr_id,
                    approved=True,
                    risk_state=RiskState.NORMAL,
                    reason=f"Event {i}",
                )
                store1.append(event)

            # Replay from file
            store2 = EventStore(tmppath)
            replayed = []

            def collect_event(event):
                replayed.append(event)

            count = store2.replay_from_file(store2.file_path, collect_event)

            assert count == 3
            assert len(replayed) == 3


# ============================================================================
# Thread Safety
# ============================================================================


class TestThreadSafety:
    """Test concurrent access to event store."""

    def test_concurrent_appends(self):
        """Test thread-safe concurrent appends."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = EventStore(Path(tmpdir))
            corr_id = uuid4()

            def append_events(thread_id):
                for i in range(10):
                    event = RiskChecked(
                        timestamp=datetime.utcnow(),
                        correlation_id=corr_id,
                        approved=True,
                        risk_state=RiskState.NORMAL,
                        reason=f"Thread {thread_id} Event {i}",
                    )
                    store.append(event)

            threads = []
            for i in range(5):
                t = threading.Thread(target=append_events, args=(i,))
                threads.append(t)
                t.start()

            for t in threads:
                t.join()

            # Should have 50 events total (5 threads * 10 events)
            assert store.event_count == 50


# ============================================================================
# Snapshot Creation
# ============================================================================


class TestSnapshotCreation:
    """Test event store snapshots."""

    @pytest.fixture
    def populated_store(self):
        """Create event store with mixed events."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = EventStore(Path(tmpdir))
            corr_ids = [uuid4() for _ in range(3)]

            # Add various event types
            for corr_id in corr_ids:
                store.append(
                    SignalGenerated(
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
                )

                store.append(
                    RiskChecked(
                        timestamp=datetime.utcnow(),
                        correlation_id=corr_id,
                        approved=True,
                        risk_state=RiskState.NORMAL,
                        reason="Approved",
                    )
                )

            yield store

    def test_snapshot_creation(self, populated_store):
        """Test creating event store snapshot."""
        snapshot = populated_store.snapshot()

        assert snapshot.total_events == 6
        assert len(snapshot.events_by_type) == 2
        assert "SignalGenerated" in snapshot.events_by_type
        assert "RiskChecked" in snapshot.events_by_type
        assert snapshot.events_by_type["SignalGenerated"] == 3
        assert snapshot.events_by_type["RiskChecked"] == 3

    def test_snapshot_correlation_ids(self, populated_store):
        """Test snapshot includes recent correlation IDs."""
        snapshot = populated_store.snapshot()

        assert len(snapshot.latest_correlation_ids) <= 100
        # Should have 3 unique correlation IDs
        assert len(snapshot.latest_correlation_ids) == 3


# ============================================================================
# JSON Serialization with Decimals
# ============================================================================


class TestJSONSerialization:
    """Test JSON serialization/deserialization with special types."""

    def test_decimal_encoder(self):
        """Test DecimalEncoder handles Decimal types."""
        data = {
            "amount": Decimal("123.456"),
            "percentage": Decimal("0.95"),
            "quantity": Decimal("1.5"),
        }

        json_str = json.dumps(data, cls=DecimalEncoder)
        decoded = json.loads(json_str)

        assert decoded["amount"] == "123.456"
        assert decoded["percentage"] == "0.95"

    def test_event_with_decimal_serialization(self):
        """Test full event with Decimal serialization."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = EventStore(Path(tmpdir))
            corr_id = uuid4()

            signal = TradingSignal(
                symbol="EURUSD",
                direction=SignalDirection.LONG,
                confidence=Decimal("0.123456789"),
                timestamp=datetime.utcnow(),
                correlation_id=corr_id,
                strategy_name="Test",
                meta_label_prob=Decimal("0.987654321"),
            )

            event = SignalGenerated(
                timestamp=datetime.utcnow(),
                correlation_id=corr_id,
                signal=signal,
            )

            store.append(event)

            # Retrieve and verify precision
            retrieved_events = store.get_by_type(SignalGenerated)
            assert len(retrieved_events) == 1

            retrieved_signal = retrieved_events[0].signal
            assert retrieved_signal.confidence == Decimal("0.123456789")
            assert retrieved_signal.meta_label_prob == Decimal("0.987654321")

    def test_order_filled_decimal_precision(self):
        """Test OrderFilled event preserves Decimal precision."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = EventStore(Path(tmpdir))
            corr_id = uuid4()

            event = OrderFilled(
                timestamp=datetime.utcnow(),
                correlation_id=corr_id,
                deal_ticket=999,
                fill_price=Decimal("1.08505000"),
                slippage=Decimal("-0.00005000"),
                volume_filled=Decimal("1.50000000"),
            )

            store.append(event)

            # Retrieve and verify
            retrieved_events = store.get_by_type(OrderFilled)
            retrieved_event = retrieved_events[0]

            assert retrieved_event.fill_price == Decimal("1.08505000")
            assert retrieved_event.slippage == Decimal("-0.00005000")
            assert retrieved_event.volume_filled == Decimal("1.50000000")


# ============================================================================
# Edge Cases
# ============================================================================


class TestEdgeCases:
    """Test edge cases and error conditions."""

    @pytest.fixture
    def event_store(self):
        """Create temporary event store."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = EventStore(Path(tmpdir))
            yield store

    def test_get_empty_results(self, event_store):
        """Test queries on empty store."""
        non_existent_id = str(uuid4())
        events = event_store.get_by_correlation_id(non_existent_id)
        assert events == []

    def test_get_latest_zero(self, event_store):
        """Test get_latest with n=0."""
        corr_id = uuid4()
        event = RiskChecked(
            timestamp=datetime.utcnow(),
            correlation_id=corr_id,
            approved=True,
            risk_state=RiskState.NORMAL,
            reason="Test",
        )
        event_store.append(event)

        latest = event_store.get_latest(0)
        assert latest == []

    def test_get_latest_more_than_stored(self, event_store):
        """Test get_latest when requesting more than available."""
        corr_id = uuid4()

        for i in range(3):
            event = RiskChecked(
                timestamp=datetime.utcnow(),
                correlation_id=corr_id,
                approved=True,
                risk_state=RiskState.NORMAL,
                reason=f"Event {i}",
            )
            event_store.append(event)

        # Request 10 but only 3 available
        latest = event_store.get_latest(10)
        assert len(latest) == 3
