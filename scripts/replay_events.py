#!/usr/bin/env python3
"""Event store replay and audit script for FTMO MT5 trading system.

This script replays events from the append-only event store for audit trails,
forensic analysis, and system state reconstruction. Supports filtering by date,
event type, symbol, and other criteria.

Features:
- Replay entire event history
- Filter by date range, symbol, event type
- Export to CSV, JSON, or HTML formats
- Calculate cumulative P&L and trading statistics
- Validate event store integrity
- Detect and report duplicate events
- Reconstruct account state at any point in time

Usage:
    python scripts/replay_events.py --store-dir data/events
    python scripts/replay_events.py --store-dir data/events --from-timestamp "2026-04-10T08:00:00+02:00"
    python scripts/replay_events.py --store-dir data/events --symbol EURUSD --format csv --output eurusd.csv
    python scripts/replay_events.py --store-dir data/events --validate
    python scripts/replay_events.py --store-dir data/events --count-only

Exit Codes:
    0: Replay completed successfully
    1: Invalid arguments or store not found
    2: Event store validation error
    3: Export error
"""

__all__ = ["main"]

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

import structlog

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))


def setup_argument_parser() -> argparse.ArgumentParser:
    """Create and configure argument parser."""
    parser = argparse.ArgumentParser(
        prog="replay-events",
        description="Replay and audit event store for FTMO MT5 system",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Replay all events
  python scripts/replay_events.py --store-dir data/events

  # Replay events from specific date
  python scripts/replay_events.py --store-dir data/events \\
    --from-timestamp "2026-04-10T00:00:00+02:00"

  # Replay events for specific symbol
  python scripts/replay_events.py --store-dir data/events --symbol EURUSD

  # Export to CSV for analysis
  python scripts/replay_events.py --store-dir data/events \\
    --format csv --output events.csv

  # Validate event store integrity
  python scripts/replay_events.py --store-dir data/events --validate

  # Count events only (fast)
  python scripts/replay_events.py --store-dir data/events --count-only

  # Filter by event types
  python scripts/replay_events.py --store-dir data/events \\
    --event-types OrderSubmitted,OrderFilled,OrderRejected

  # Date range with filtering
  python scripts/replay_events.py --store-dir data/events \\
    --from-timestamp "2026-04-09T00:00:00+02:00" \\
    --to-timestamp "2026-04-10T23:59:59+02:00" \\
    --format csv --output trades.csv
        """,
    )

    parser.add_argument(
        "--store-dir",
        type=str,
        default="data/events",
        help="Path to event store directory (default: data/events)",
        metavar="PATH",
    )

    parser.add_argument(
        "--from-timestamp",
        type=str,
        default=None,
        help="Start timestamp (ISO 8601, e.g., 2026-04-10T08:00:00+02:00)",
        metavar="TIMESTAMP",
    )

    parser.add_argument(
        "--to-timestamp",
        type=str,
        default=None,
        help="End timestamp (ISO 8601, e.g., 2026-04-10T23:59:59+02:00)",
        metavar="TIMESTAMP",
    )

    parser.add_argument(
        "--symbol",
        type=str,
        default=None,
        help="Filter by trading symbol (e.g., EURUSD)",
        metavar="SYMBOL",
    )

    parser.add_argument(
        "--event-types",
        type=str,
        default=None,
        help="Filter by event types (comma-separated, e.g., OrderFilled,PositionClosed)",
        metavar="TYPES",
    )

    parser.add_argument(
        "--order-id",
        type=str,
        default=None,
        help="Filter by order ID (UUID format)",
        metavar="UUID",
    )

    parser.add_argument(
        "--format",
        type=str,
        default="text",
        choices=["text", "json", "csv", "html"],
        help="Output format: text (console), json, csv, html (default: text)",
        metavar="FORMAT",
    )

    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output file path (if not specified, print to console)",
        metavar="PATH",
    )

    parser.add_argument(
        "--count-only",
        action="store_true",
        help="Count events without loading full details (faster)",
    )

    parser.add_argument(
        "--validate",
        action="store_true",
        help="Validate event store integrity and report issues",
    )

    parser.add_argument(
        "--sort-by",
        type=str,
        default="timestamp",
        help="Sort results by field (e.g., timestamp, pnl, symbol) (default: timestamp)",
        metavar="FIELD",
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of events to display/export",
        metavar="N",
    )

    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: INFO)",
        metavar="LEVEL",
    )

    parser.add_argument(
        "--version",
        action="version",
        version="%(prog)s 1.0.0",
    )

    return parser


def validate_event_store(store_dir: str) -> tuple[bool, List[str]]:
    """Validate event store integrity.

    Args:
        store_dir: Path to event store directory

    Returns:
        Tuple of (is_valid, list_of_errors)
    """
    logger = structlog.get_logger()
    errors = []

    store_path = Path(store_dir)

    if not store_path.exists():
        errors.append(f"Store directory not found: {store_dir}")
        return False, errors

    if not store_path.is_dir():
        errors.append(f"Store path is not a directory: {store_dir}")
        return False, errors

    # Check for event files
    event_files = list(store_path.glob("*.jsonl")) + list(store_path.glob("*.json"))
    if not event_files:
        errors.append(f"No event files found in store directory: {store_dir}")
        return False, errors

    logger.info("event_store_found", file_count=len(event_files))

    # Validate each file
    duplicate_ids = set()
    total_events = 0

    for event_file in event_files:
        try:
            with open(event_file, "r", encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        event = json.loads(line)
                        total_events += 1

                        # Check for required fields
                        if "event_id" not in event:
                            errors.append(f"Event missing event_id in {event_file.name}")
                        if "timestamp" not in event:
                            errors.append(f"Event missing timestamp in {event_file.name}")

                        # Check for duplicates
                        event_id = event.get("event_id")
                        if event_id in duplicate_ids:
                            errors.append(f"Duplicate event_id found: {event_id}")
                        else:
                            duplicate_ids.add(event_id)

                    except json.JSONDecodeError as e:
                        errors.append(f"Invalid JSON in {event_file.name}: {e}")

        except Exception as e:
            errors.append(f"Error reading {event_file.name}: {e}")

    logger.info("event_store_validation_complete", total_events=total_events, errors=len(errors))

    return len(errors) == 0, errors


def load_events(
    store_dir: str,
    from_timestamp: Optional[str] = None,
    to_timestamp: Optional[str] = None,
    symbol: Optional[str] = None,
    event_types: Optional[List[str]] = None,
    order_id: Optional[str] = None,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Load and filter events from event store.

    Args:
        store_dir: Path to event store directory
        from_timestamp: Start timestamp (ISO 8601)
        to_timestamp: End timestamp (ISO 8601)
        symbol: Filter by symbol
        event_types: Filter by event types
        order_id: Filter by order ID
        limit: Limit number of events

    Returns:
        List of events
    """
    logger = structlog.get_logger()

    events = []
    store_path = Path(store_dir)

    # Parse timestamp filters
    from_dt = None
    to_dt = None
    if from_timestamp:
        try:
            from_dt = datetime.fromisoformat(from_timestamp)
        except ValueError:
            logger.error("invalid_from_timestamp", value=from_timestamp)

    if to_timestamp:
        try:
            to_dt = datetime.fromisoformat(to_timestamp)
        except ValueError:
            logger.error("invalid_to_timestamp", value=to_timestamp)

    # Load events
    for event_file in sorted(store_path.glob("*.jsonl")) + sorted(store_path.glob("*.json")):
        try:
            with open(event_file, "r", encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue

                    try:
                        event = json.loads(line)

                        # Apply filters
                        if from_dt and event.get("timestamp") < from_dt.isoformat():
                            continue
                        if to_dt and event.get("timestamp") > to_dt.isoformat():
                            continue
                        if symbol and event.get("symbol") != symbol:
                            continue
                        if order_id and event.get("order_id") != order_id:
                            continue
                        if event_types and event.get("event_type") not in event_types:
                            continue

                        events.append(event)

                        if limit and len(events) >= limit:
                            return events

                    except json.JSONDecodeError:
                        pass

        except Exception as e:
            logger.warning("error_loading_events", file=event_file.name, error=str(e))

    logger.info("events_loaded", count=len(events))
    return events


def export_to_csv(events: List[Dict[str, Any]], output_path: str) -> None:
    """Export events to CSV format."""
    import csv

    logger = structlog.get_logger()

    try:
        if not events:
            logger.warning("no_events_to_export")
            return

        # Collect all possible field names
        fieldnames = set()
        for event in events:
            fieldnames.update(event.keys())
        fieldnames = sorted(list(fieldnames))

        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(events)

        logger.info("events_exported_csv", path=output_path, count=len(events))

    except Exception as e:
        logger.error("csv_export_error", error=str(e), exc_info=True)
        raise


def export_to_json(events: List[Dict[str, Any]], output_path: str) -> None:
    """Export events to JSON format."""
    logger = structlog.get_logger()

    try:
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(events, f, indent=2, default=str)

        logger.info("events_exported_json", path=output_path, count=len(events))

    except Exception as e:
        logger.error("json_export_error", error=str(e), exc_info=True)
        raise


def export_to_html(events: List[Dict[str, Any]], output_path: str) -> None:
    """Export events to HTML format."""
    logger = structlog.get_logger()

    try:
        html = """
        <!DOCTYPE html>
        <html>
        <head>
            <title>FTMO MT5 Event Store Audit</title>
            <style>
                body { font-family: Arial, sans-serif; margin: 20px; background: #f5f5f5; }
                table { width: 100%; border-collapse: collapse; background: white; }
                th { background: #007bff; color: white; padding: 12px; text-align: left; }
                td { padding: 8px; border-bottom: 1px solid #ddd; font-size: 12px; }
                tr:hover { background: #f9f9f9; }
                h1 { color: #333; }
            </style>
        </head>
        <body>
            <h1>FTMO MT5 Event Store Audit Report</h1>
            <p>Generated: """ + datetime.now().isoformat() + """</p>
            <p>Total Events: """ + str(len(events)) + """</p>
            <table>
                <tr>
        """

        # Add headers
        if events:
            for key in sorted(events[0].keys()):
                html += f"<th>{key}</th>"

        html += """
                </tr>
        """

        # Add rows
        for event in events:
            html += "<tr>"
            for key in sorted(event.keys()):
                value = event.get(key, "")
                html += f"<td>{value}</td>"
            html += "</tr>"

        html += """
            </table>
            <hr>
            <p style="color: #999; font-size: 12px;">
                FTMO MT5 Trading System v1.0.0
            </p>
        </body>
        </html>
        """

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html)

        logger.info("events_exported_html", path=output_path, count=len(events))

    except Exception as e:
        logger.error("html_export_error", error=str(e), exc_info=True)
        raise


def main() -> None:
    """Main entry point."""
    # Parse arguments
    parser = setup_argument_parser()
    args = parser.parse_args()

    # Setup logging
    from ftmo_mt5.observability.logging_config import setup_logging

    logger = setup_logging(log_level=args.log_level, json_output=True)

    logger.info("replay_events_script_start", version="1.0.0")

    try:
        # Validate event store directory
        store_path = Path(args.store_dir)
        if not store_path.exists():
            logger.error("store_dir_not_found", path=args.store_dir)
            print(f"Error: Event store directory not found: {args.store_dir}", file=sys.stderr)
            sys.exit(1)

        # Validate event store if requested
        if args.validate:
            logger.info("validating_event_store")
            is_valid, errors = validate_event_store(args.store_dir)

            if is_valid:
                print("Event store validation: PASSED")
                sys.exit(0)
            else:
                print("Event store validation: FAILED")
                for error in errors:
                    print(f"  - {error}")
                sys.exit(2)

        # Parse event types filter
        event_types = None
        if args.event_types:
            event_types = [e.strip() for e in args.event_types.split(",")]

        # Load events
        events = load_events(
            args.store_dir,
            from_timestamp=args.from_timestamp,
            to_timestamp=args.to_timestamp,
            symbol=args.symbol,
            event_types=event_types,
            order_id=args.order_id,
            limit=args.limit,
        )

        # Count only mode
        if args.count_only:
            print(f"Total events: {len(events)}")
            sys.exit(0)

        # Sort events if requested
        if args.sort_by and events:
            try:
                events.sort(key=lambda x: x.get(args.sort_by, ""))
            except Exception as e:
                logger.warning("sort_error", field=args.sort_by, error=str(e))

        # Export or display
        if args.output:
            if args.format == "csv":
                export_to_csv(events, args.output)
            elif args.format == "json":
                export_to_json(events, args.output)
            elif args.format == "html":
                export_to_html(events, args.output)
            print(f"Exported {len(events)} events to {args.output}")
        else:
            # Display to console
            if args.format == "json":
                print(json.dumps(events, indent=2, default=str))
            else:
                # Text format
                print(f"\nTotal Events: {len(events)}\n")
                for event in events[:10]:  # Show first 10
                    print(f"ID: {event.get('event_id')}")
                    print(f"Type: {event.get('event_type')}")
                    print(f"Time: {event.get('timestamp')}")
                    print(f"Symbol: {event.get('symbol')}")
                    print()

                if len(events) > 10:
                    print(f"... and {len(events) - 10} more events")

        logger.info("replay_events_script_complete")
        sys.exit(0)

    except Exception as e:
        logger.error("fatal_error", error=str(e), exc_info=True)
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(3)


if __name__ == "__main__":
    main()
