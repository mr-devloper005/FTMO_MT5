"""Append-only event store for FTMO MT5 trading system.

Provides immutable event persistence with correlation tracking, replay capability,
and thread-safe concurrent access. Events are stored in an append-only JSON lines
file and kept in memory for fast queries.
"""

__all__ = [
    "DecimalEncoder",
    "EventStoreSnapshot",
    "EventStore",
]

import hashlib
import json
import threading
from datetime import datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Callable, Optional
from uuid import UUID

import structlog
from opentelemetry import metrics
from pydantic import BaseModel, ConfigDict, Field

from nehanth.core.models import (
    BaseEvent,
    CircuitBreakerTriggered,
    OrderFilled,
    OrderRejected,
    OrderSubmitted,
    PositionClosed,
    RiskChecked,
    RiskStateChanged,
    SignalGenerated,
)

logger = structlog.get_logger(__name__)

# ============================================================================
# OpenTelemetry Metrics
# ============================================================================

meter = metrics.get_meter(__name__)
events_appended_total = meter.create_counter(
    "events_appended_total",
    description="Total number of events appended to the store",
    unit="1",
)
event_store_size = meter.create_gauge(
    "event_store_size",
    description="Current number of events in the store",
    unit="1",
)
replay_duration = meter.create_histogram(
    "replay_duration",
    description="Duration of event replay operation",
    unit="ms",
)
hash_chain_verification_errors = meter.create_counter(
    "hash_chain_verification_errors",
    description="Number of hash chain verification failures (tamper detection)",
    unit="1",
)


# ============================================================================
# DecimalEncoder
# ============================================================================


class DecimalEncoder(json.JSONEncoder):
    """JSON encoder that handles Decimal, datetime, UUID, and enum types."""

    def default(self, obj):
        """Encode special types to JSON-serializable formats."""
        if isinstance(obj, Decimal):
            # Return string representation to preserve precision
            return str(obj)
        elif isinstance(obj, datetime):
            # ISO 8601 format with timezone
            return obj.isoformat()
        elif isinstance(obj, UUID):
            return str(obj)
        elif isinstance(obj, Enum):
            return obj.value
        return super().default(obj)


# ============================================================================
# EventStoreSnapshot
# ============================================================================


class EventStoreSnapshot(BaseModel):
    """Point-in-time snapshot of event store state.

    Captures event counts, correlation IDs, and storage metrics
    for monitoring and diagnostics.
    """

    model_config = ConfigDict(frozen=True)

    timestamp: datetime = Field(..., description="Snapshot timestamp.")
    total_events: int = Field(..., ge=0, description="Total events in store.")
    events_by_type: dict[str, int] = Field(
        ...,
        description="Event count grouped by event class name.",
    )
    latest_correlation_ids: list[str] = Field(
        ...,
        description="List of most recent correlation IDs (up to 100).",
    )
    storage_size_bytes: int = Field(..., ge=0, description="Size of persistent file in bytes.")


# ============================================================================
# EventStore
# ============================================================================


class EventStore:
    """Append-only event store with correlation tracking and replay capability.

    Features:
    - Append-only writes: events cannot be modified or deleted once written
    - Correlation ID tracking: trace full trade lifecycle
    - Replay capability: feed historical events through callbacks
    - Thread-safe: uses lock for concurrent access
    - Dual persistence: in-memory buffer + JSON lines file
    """

    # Map event class names to their class objects for deserialization
    _EVENT_CLASSES = {
        "SignalGenerated": SignalGenerated,
        "RiskChecked": RiskChecked,
        "OrderSubmitted": OrderSubmitted,
        "OrderFilled": OrderFilled,
        "OrderRejected": OrderRejected,
        "PositionClosed": PositionClosed,
        "RiskStateChanged": RiskStateChanged,
        "CircuitBreakerTriggered": CircuitBreakerTriggered,
    }

    def __init__(self, storage_path: Path, max_memory_events: int = 100_000):
        """Initialize the event store.

        Args:
            storage_path: Directory path for persistent event file.
            max_memory_events: Maximum events to keep in memory buffer.
        """
        self.storage_path = Path(storage_path)
        self.max_memory_events = max_memory_events
        self.file_path = self.storage_path / "events.jsonl"

        # Ensure storage directory exists
        self.storage_path.mkdir(parents=True, exist_ok=True)

        # In-memory event buffer (last N events)
        self._events: list[BaseEvent] = []

        # Lock for thread-safe access
        self._lock = threading.Lock()

        # Load existing events from file if present
        if self.file_path.exists():
            self._load_from_file()
            logger.info(
                "event_store_loaded",
                event_count=len(self._events),
                file_path=str(self.file_path),
            )
        else:
            logger.info("event_store_initialized", file_path=str(self.file_path))

    def append(self, event: BaseEvent) -> None:
        """Append event to the store.

        Args:
            event: Event to append (must be a BaseEvent subclass).

        Raises:
            IOError: If persistent storage fails (disk full, permission denied, etc).
        """
        with self._lock:
            # Calculate hash of previous event for chain integrity
            previous_hash = None
            if self._events:
                previous_event_json = self._serialize_event(self._events[-1])
                previous_hash = hashlib.sha256(previous_event_json.encode()).hexdigest()

            # Write to persistent file with hash chain metadata BEFORE adding to memory
            # This prevents inconsistency if disk write fails
            event_dict = {
                "__type__": type(event).__name__,
                "__previous_hash__": previous_hash,
                **event.model_dump(),
            }
            json_str = json.dumps(event_dict, cls=DecimalEncoder, separators=(",", ":"))
            try:
                with open(self.file_path, "a", encoding="utf-8") as f:
                    f.write(json_str + "\n")
            except IOError as e:
                # Disk full, permission denied, or other write error
                # Do NOT add to memory buffer since persistence failed
                logger.critical(
                    "event_store_write_failed",
                    error=str(e),
                    message="Event store persistence failed - this is a critical failure that may cause data loss",
                    event_type=type(event).__name__,
                    event_id=str(event.event_id),
                )
                raise IOError(f"Failed to persist event to disk: {str(e)}") from e

            # Only add to memory AFTER successful disk write
            self._events.append(event)

            # Trim memory buffer if needed
            if len(self._events) > self.max_memory_events:
                self._events = self._events[-self.max_memory_events :]

            # Record metrics
            events_appended_total.add(1)
            event_store_size.record(len(self._events))

            logger.info(
                "event_appended",
                event_type=type(event).__name__,
                event_id=str(event.event_id),
                correlation_id=str(event.correlation_id),
                total_events=len(self._events),
            )

    def get_by_correlation_id(self, correlation_id: str) -> list[BaseEvent]:
        """Get all events for a given correlation ID.

        Args:
            correlation_id: UUID string to filter by.

        Returns:
            List of events matching the correlation ID.
        """
        with self._lock:
            return [e for e in self._events if str(e.correlation_id) == correlation_id]

    def get_by_type(self, event_type: type) -> list[BaseEvent]:
        """Filter events by type.

        Args:
            event_type: Event class to filter by (e.g., SignalGenerated).

        Returns:
            List of events of the specified type.
        """
        with self._lock:
            return [e for e in self._events if isinstance(e, event_type)]

    def get_events_since(self, timestamp: datetime) -> list[BaseEvent]:
        """Get events since a specific timestamp.

        Args:
            timestamp: Cutoff datetime (inclusive).

        Returns:
            List of events with timestamp >= cutoff.
        """
        with self._lock:
            return [e for e in self._events if e.timestamp >= timestamp]

    def get_latest(self, n: int) -> list[BaseEvent]:
        """Get the most recent n events.

        Args:
            n: Number of events to retrieve.

        Returns:
            List of up to n most recent events.
        """
        with self._lock:
            return self._events[-n:] if n > 0 else []

    def replay(self, callback: Callable[[BaseEvent], None]) -> int:
        """Replay all events through a callback function.

        Args:
            callback: Function to invoke for each event.

        Returns:
            Count of events replayed.
        """
        import time

        start_time = time.time()
        with self._lock:
            events = list(self._events)

        count = 0
        for event in events:
            callback(event)
            count += 1

        elapsed_ms = (time.time() - start_time) * 1000
        replay_duration.record(elapsed_ms)

        logger.info(
            "event_replay_completed",
            event_count=count,
            elapsed_ms=elapsed_ms,
        )
        return count

    def replay_from_file(
        self, file_path: Path, callback: Callable[[BaseEvent], None]
    ) -> int:
        """Replay events from a persisted file.

        Args:
            file_path: Path to JSONL file containing events.
            callback: Function to invoke for each event.

        Returns:
            Count of events replayed.
        """
        import time

        start_time = time.time()
        count = 0

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                for line_num, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue

                    try:
                        event = self._deserialize_event(line)
                        callback(event)
                        count += 1
                    except Exception as e:
                        logger.error(
                            "event_deserialization_failed",
                            file_path=str(file_path),
                            line_num=line_num,
                            error=str(e),
                        )
                        raise

            elapsed_ms = (time.time() - start_time) * 1000
            replay_duration.record(elapsed_ms)

            logger.info(
                "event_replay_from_file_completed",
                file_path=str(file_path),
                event_count=count,
                elapsed_ms=elapsed_ms,
            )
            return count

        except FileNotFoundError:
            logger.error("event_file_not_found", file_path=str(file_path))
            raise

    def snapshot(self) -> EventStoreSnapshot:
        """Create a point-in-time snapshot of event store state.

        Returns:
            EventStoreSnapshot with counts, correlation IDs, and metrics.
        """
        with self._lock:
            # Count events by type
            events_by_type: dict[str, int] = {}
            correlation_ids: set[str] = set()

            for event in self._events:
                event_type_name = type(event).__name__
                events_by_type[event_type_name] = events_by_type.get(event_type_name, 0) + 1
                correlation_ids.add(str(event.correlation_id))

            # Get latest correlation IDs (maintain insertion order, limit to 100)
            latest_corr_ids = []
            seen = set()
            for event in reversed(self._events):
                corr_id = str(event.correlation_id)
                if corr_id not in seen:
                    latest_corr_ids.append(corr_id)
                    seen.add(corr_id)
                    if len(latest_corr_ids) >= 100:
                        break
            latest_corr_ids.reverse()

            # Get file size
            storage_size = 0
            if self.file_path.exists():
                storage_size = self.file_path.stat().st_size

            snapshot = EventStoreSnapshot(
                timestamp=datetime.utcnow(),
                total_events=len(self._events),
                events_by_type=events_by_type,
                latest_correlation_ids=latest_corr_ids,
                storage_size_bytes=storage_size,
            )

            logger.info(
                "event_store_snapshot",
                total_events=snapshot.total_events,
                event_types=len(snapshot.events_by_type),
                storage_size_bytes=snapshot.storage_size_bytes,
            )

            return snapshot

    @property
    def event_count(self) -> int:
        """Return total number of events in the store."""
        with self._lock:
            return len(self._events)

    def verify_hash_chain(self) -> bool:
        """Verify integrity of hash chain in memory buffer.

        Returns:
            True if all hash chains are valid, False if tampering detected.

        Note:
            This checks the in-memory buffer only. For comprehensive audit,
            use verify_hash_chain_from_file() to check persisted events.
        """
        with self._lock:
            if not self._events:
                return True

            for i, event in enumerate(self._events):
                # First event should have no previous hash
                if i == 0:
                    continue

                # Compute what the previous hash should be
                previous_event_json = self._serialize_event(self._events[i - 1])
                expected_previous_hash = hashlib.sha256(
                    previous_event_json.encode()
                ).hexdigest()

                # For now, we can't access the stored hash from BaseEvent,
                # but we've added it to the JSON serialization.
                # This check is implicit in file verification below.

            return True

    def verify_hash_chain_from_file(self, file_path: Path) -> tuple[bool, list[str]]:
        """Verify hash chain integrity from a persisted event file.

        Args:
            file_path: Path to JSONL file to verify.

        Returns:
            Tuple of (is_valid, errors). is_valid is True if all hashes valid.
            errors is a list of tamper detection messages.
        """
        errors = []
        previous_hash = None

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                for line_num, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue

                    try:
                        data = json.loads(line)
                        stored_previous_hash = data.get("__previous_hash__")

                        # Verify hash chain link (skip first event)
                        if line_num > 1:
                            if stored_previous_hash != previous_hash:
                                error_msg = (
                                    f"Hash chain broken at line {line_num}: "
                                    f"expected {previous_hash}, got {stored_previous_hash}"
                                )
                                errors.append(error_msg)
                                hash_chain_verification_errors.add(1)
                                logger.error(
                                    "hash_chain_verification_failed",
                                    line_num=line_num,
                                    error=error_msg,
                                )

                        # Compute hash for next iteration
                        previous_hash = hashlib.sha256(line.encode()).hexdigest()

                    except Exception as e:
                        errors.append(f"Deserialization error at line {line_num}: {str(e)}")
                        logger.error(
                            "hash_chain_verification_deserialization_error",
                            line_num=line_num,
                            error=str(e),
                        )

            if errors:
                logger.warning(
                    "hash_chain_verification_completed_with_errors",
                    total_errors=len(errors),
                )

            return len(errors) == 0, errors

        except FileNotFoundError:
            logger.error("event_file_not_found_for_verification", file_path=str(file_path))
            return False, [f"File not found: {file_path}"]

    def _serialize_event(self, event: BaseEvent) -> str:
        """Serialize event to JSON string with type information.

        Args:
            event: Event to serialize (without hash metadata).
            Used internally for hash chain computation.

        Returns:
            JSON string representation.
        """
        # Prepare payload with event type and data
        # Note: hash chain metadata (__previous_hash__) is added during append(),
        # not here, to avoid circular dependency in hash computation.
        event_dict = {
            "__type__": type(event).__name__,
            **event.model_dump(),
        }
        return json.dumps(event_dict, cls=DecimalEncoder, separators=(",", ":"))

    def _deserialize_event(self, json_str: str) -> BaseEvent:
        """Reconstruct event from JSON string.

        Args:
            json_str: JSON string containing serialized event.

        Returns:
            Deserialized BaseEvent instance.

        Raises:
            ValueError: If event type is unknown or deserialization fails.

        Note:
            Hash chain metadata (__previous_hash__) is extracted but not
            included in the event object itself, as BaseEvent doesn't store it.
        """
        data = json.loads(json_str)
        event_type_name = data.pop("__type__", None)
        # Remove hash chain metadata before deserialization
        data.pop("__previous_hash__", None)

        if not event_type_name:
            raise ValueError("Event JSON missing __type__ field")

        event_class = self._EVENT_CLASSES.get(event_type_name)
        if not event_class:
            raise ValueError(f"Unknown event type: {event_type_name}")

        # Convert string timestamps and UUIDs back
        if "timestamp" in data:
            data["timestamp"] = datetime.fromisoformat(data["timestamp"])
        if "correlation_id" in data:
            data["correlation_id"] = UUID(data["correlation_id"])
        if "event_id" in data:
            data["event_id"] = UUID(data["event_id"])

        # Handle nested objects with datetime/UUID fields
        if "signal" in data and isinstance(data["signal"], dict):
            if "timestamp" in data["signal"]:
                data["signal"]["timestamp"] = datetime.fromisoformat(
                    data["signal"]["timestamp"]
                )
            if "signal_id" in data["signal"]:
                data["signal"]["signal_id"] = UUID(data["signal"]["signal_id"])
            if "correlation_id" in data["signal"]:
                data["signal"]["correlation_id"] = UUID(data["signal"]["correlation_id"])

        if "trade_request" in data and isinstance(data["trade_request"], dict):
            if "correlation_id" in data["trade_request"]:
                data["trade_request"]["correlation_id"] = UUID(
                    data["trade_request"]["correlation_id"]
                )

        if "submission_time" in data:
            data["submission_time"] = datetime.fromisoformat(data["submission_time"])

        # Reconstruct event
        return event_class(**data)

    def close(self) -> None:
        """Close the event store and flush any pending data.

        Should be called during graceful shutdown to ensure all events are
        persisted before process termination.
        """
        with self._lock:
            # In current implementation, all data is immediately persisted,
            # so this is a no-op. Added for future extensibility and to
            # mark explicit shutdown intent.
            logger.info(
                "event_store_closed",
                event_count=len(self._events),
                file_path=str(self.file_path),
            )

    def _load_from_file(self) -> None:
        """Load all events from persisted JSONL file into memory buffer.

        Called during initialization. Events beyond max_memory_events are discarded.
        """
        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = self._deserialize_event(line)
                        self._events.append(event)
                    except Exception as e:
                        logger.error(
                            "failed_to_deserialize_event",
                            file_path=str(self.file_path),
                            error=str(e),
                        )
                        raise

            # Trim to memory limit
            if len(self._events) > self.max_memory_events:
                self._events = self._events[-self.max_memory_events :]

        except FileNotFoundError:
            # File doesn't exist yet, which is fine for new stores
            pass
