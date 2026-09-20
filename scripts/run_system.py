#!/usr/bin/env python3
"""Production entry point for FTMO MT5 trading system.

This script provides a production-ready wrapper for running the trading system
with proper initialization, health checks, and graceful shutdown handling.

Usage:
    python scripts/run_system.py                    # Use default config
    python scripts/run_system.py --config config/1step.yaml
    python scripts/run_system.py --config config/1step.yaml --log-level DEBUG
    python scripts/run_system.py --health-check-only

Exit Codes:
    0: Normal shutdown
    1: Configuration error
    2: Component initialization failure
    3: Runtime error
    4: Shutdown error
"""

__all__ = ["main"]

import argparse
import asyncio
import logging
import os
import signal
import sys
from pathlib import Path
from typing import Optional, Any

import structlog

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from ftmo_mt5.main import load_config, initialize_components, run_event_loop, start_health_check_server
from ftmo_mt5.observability.logging_config import setup_logging


def setup_argument_parser() -> argparse.ArgumentParser:
    """Create and configure argument parser."""
    parser = argparse.ArgumentParser(
        prog="run-system",
        description="Production runner for FTMO MT5 trading system",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run with default configuration
  python scripts/run_system.py

  # Run with specific 1-Step challenge config
  python scripts/run_system.py --config config/1step.yaml

  # Run with debug logging
  python scripts/run_system.py --log-level DEBUG

  # Only perform health checks and exit
  python scripts/run_system.py --health-check-only

  # Specify custom event store location
  python scripts/run_system.py --event-store-dir /mnt/data/events

  # All options combined
  python scripts/run_system.py \\
    --config config/2step.yaml \\
    --log-level INFO \\
    --event-store-dir data/events \\
    --health-check-port 8080
        """,
    )

    parser.add_argument(
        "--config",
        type=str,
        default=os.getenv("FTMO_CONFIG_PATH", "config/default.yaml"),
        help="Path to YAML configuration file (default: config/default.yaml or FTMO_CONFIG_PATH env)",
        metavar="PATH",
    )

    parser.add_argument(
        "--log-level",
        type=str,
        default=os.getenv("LOG_LEVEL", "INFO"),
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: INFO or LOG_LEVEL env)",
        metavar="LEVEL",
    )

    parser.add_argument(
        "--event-store-dir",
        type=str,
        default=os.getenv("FTMO_EVENT_STORE_DIR", "data/events"),
        help="Directory for event store (default: data/events or FTMO_EVENT_STORE_DIR env)",
        metavar="PATH",
    )

    parser.add_argument(
        "--health-check-port",
        type=int,
        default=8080,
        help="Port for health check HTTP server (default: 8080)",
        metavar="PORT",
    )

    parser.add_argument(
        "--health-check-only",
        action="store_true",
        help="Only perform health checks and exit",
    )

    parser.add_argument(
        "--version",
        action="version",
        version="%(prog)s 1.0.0",
    )

    return parser


def validate_environment(config_path: str, event_store_dir: str) -> tuple[bool, str]:
    """Validate system environment before startup.

    Args:
        config_path: Path to configuration file
        event_store_dir: Path to event store directory

    Returns:
        Tuple of (is_valid, error_message)
    """
    errors = []

    # Check configuration file exists
    if not Path(config_path).exists():
        errors.append(f"Configuration file not found: {config_path}")

    # Check MT5 credentials
    if not os.getenv("MT5_LOGIN"):
        errors.append("MT5_LOGIN environment variable not set")
    if not os.getenv("MT5_SERVER"):
        errors.append("MT5_SERVER environment variable not set")
    if not os.getenv("MT5_PASSWORD"):
        errors.append("MT5_PASSWORD environment variable not set")

    # Check event store directory is writable
    event_store_path = Path(event_store_dir)
    try:
        event_store_path.mkdir(parents=True, exist_ok=True)
        # Test write permission
        test_file = event_store_path / ".write_test"
        test_file.touch()
        test_file.unlink()
    except Exception as e:
        errors.append(f"Event store directory not writable: {event_store_dir} ({e})")

    # Check logs directory
    log_dir = Path("logs")
    try:
        log_dir.mkdir(exist_ok=True)
    except Exception as e:
        errors.append(f"Cannot create logs directory: {e}")

    is_valid = len(errors) == 0
    error_message = "\n".join(errors)

    return is_valid, error_message


async def perform_health_checks(port: int) -> bool:
    """Perform startup health checks.

    Args:
        port: Health check server port

    Returns:
        True if all checks pass
    """
    logger = structlog.get_logger()

    checks_passed = 0
    checks_total = 3

    # Check 1: Configuration validity
    try:
        from ftmo_mt5.main import load_config as _load_config

        ftmo_config, system_config = _load_config()
        logger.info("health_check_config_valid", ftmo_capital=ftmo_config.initial_capital)
        checks_passed += 1
    except Exception as e:
        logger.error("health_check_config_invalid", error=str(e))

    # Check 2: MT5 Credentials
    if all(os.getenv(k) for k in ["MT5_LOGIN", "MT5_SERVER", "MT5_PASSWORD"]):
        logger.info("health_check_mt5_credentials_present")
        checks_passed += 1
    else:
        logger.error("health_check_mt5_credentials_missing")

    # Check 3: Event store
    event_store_dir = os.getenv("FTMO_EVENT_STORE_DIR", "data/events")
    if Path(event_store_dir).exists() and Path(event_store_dir).is_dir():
        logger.info("health_check_event_store_valid", path=event_store_dir)
        checks_passed += 1
    else:
        logger.error("health_check_event_store_invalid", path=event_store_dir)

    logger.info("health_checks_complete", passed=checks_passed, total=checks_total)
    return checks_passed == checks_total


async def run_main_loop(config_path: str, log_level: str, event_store_dir: str, port: int) -> int:
    """Run the main trading system loop.

    Args:
        config_path: Path to configuration file
        log_level: Logging level
        event_store_dir: Event store directory
        port: Health check server port

    Returns:
        Exit code (0 = success, non-zero = error)
    """
    logger = structlog.get_logger()

    # Set environment variables
    os.environ["FTMO_CONFIG_PATH"] = config_path
    os.environ["FTMO_EVENT_STORE_DIR"] = event_store_dir
    os.environ["LOG_LEVEL"] = log_level

    # Import main components
    from ftmo_mt5.main import (
        load_config,
        initialize_components,
        run_event_loop as main_run_event_loop,
        start_health_check_server,
    )

    try:
        # Load configuration
        logger.info("loading_configuration", config_path=config_path)
        ftmo_config, system_config = load_config(config_path)
        logger.info("configuration_loaded", challenge_type=ftmo_config.challenge_type)

        # Initialize components
        logger.info("initializing_components")
        (
            mt5_gateway,
            mt5_executor,
            position_reconciler,
            event_store,
            rule_engine,
            signal_agent,
            risk_agent,
            execution_agent,
            coordinator_instance,
            supervisor,
        ) = await initialize_components(ftmo_config, system_config)
        logger.info("components_initialized")

        # Start health check server
        logger.info("starting_health_check_server", port=port)
        server_runner = await start_health_check_server(port=port)

        # Setup graceful shutdown
        shutdown_event = asyncio.Event()

        def signal_handler(sig: int, frame: Any) -> None:
            """Handle shutdown signals."""
            logger.warning("shutdown_signal_received", signal=sig)
            shutdown_event.set()

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        # Run main event loop
        logger.info("starting_main_event_loop")
        loop_task = asyncio.create_task(main_run_event_loop(supervisor, system_config))

        # Wait for shutdown
        await asyncio.wait(
            [loop_task, asyncio.create_task(shutdown_event.wait())],
            return_when=asyncio.FIRST_COMPLETED,
        )

        # Cancel loop task if still running
        if not loop_task.done():
            loop_task.cancel()
            try:
                await loop_task
            except asyncio.CancelledError:
                pass

        logger.info("main_event_loop_completed")

        # Cleanup
        try:
            await supervisor.shutdown()
            logger.info("supervisor_shutdown_complete")
        except Exception as e:
            logger.error("supervisor_shutdown_failed", error=str(e))

        if server_runner:
            try:
                await server_runner.cleanup()
                logger.info("health_check_server_stopped")
            except Exception as e:
                logger.error("server_cleanup_failed", error=str(e))

        logger.info("system_shutdown_complete")
        return 0

    except KeyboardInterrupt:
        logger.warning("keyboard_interrupt_received")
        return 0
    except Exception as e:
        logger.error("system_error", error=str(e), exc_info=True)
        return 3


def main() -> None:
    """Main entry point."""
    # Parse arguments
    parser = setup_argument_parser()
    args = parser.parse_args()

    # Setup logging early
    logger = setup_logging(log_level=args.log_level, json_output=True)
    logger.info(
        "ftmo_mt5_production_startup",
        version="1.0.0",
        config=args.config,
        log_level=args.log_level,
    )

    # Validate environment
    is_valid, error_message = validate_environment(args.config, args.event_store_dir)
    if not is_valid:
        logger.error("environment_validation_failed", errors=error_message)
        print(f"Configuration Error:\n{error_message}", file=sys.stderr)
        sys.exit(1)

    # If health check only, perform and exit
    if args.health_check_only:
        logger.info("health_check_only_mode")
        try:
            checks_passed = asyncio.run(perform_health_checks(args.health_check_port))
            if checks_passed:
                logger.info("all_health_checks_passed")
                sys.exit(0)
            else:
                logger.error("some_health_checks_failed")
                sys.exit(1)
        except Exception as e:
            logger.error("health_check_error", error=str(e))
            sys.exit(1)

    # Run main loop
    try:
        exit_code = asyncio.run(
            run_main_loop(
                args.config,
                args.log_level,
                args.event_store_dir,
                args.health_check_port,
            )
        )
        sys.exit(exit_code)
    except KeyboardInterrupt:
        logger.warning("keyboard_interrupt")
        sys.exit(0)
    except Exception as e:
        logger.error("fatal_error", error=str(e), exc_info=True)
        sys.exit(3)


if __name__ == "__main__":
    main()
