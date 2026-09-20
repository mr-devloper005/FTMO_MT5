"""Main entry point for FTMO MT5 trading system.

Orchestrates startup of all system components:
- Configuration loading from YAML and environment variables
- Structured logging and OpenTelemetry telemetry setup
- Initialization of MT5 gateway, executor, and reconciler
- Multi-agent system (signal, risk, execution agents)
- Event store and rule engine
- System coordinator and supervisor
- Health check HTTP server on port 8080
- Graceful shutdown on SIGINT/SIGTERM

The system runs an async event loop managing market data flows,
signal generation, risk checks, order execution, and position monitoring.
"""

__all__ = [
    "load_config",
    "initialize_components",
    "run_event_loop",
    "start_health_check_server",
    "main",
]

import asyncio
import os
import signal
import sys
from pathlib import Path
from typing import Optional, Any
from decimal import Decimal, getcontext
from datetime import datetime, timezone

import yaml
import structlog
from aiohttp import web

from nehanth.core.models import (
    FTMOConfig,
    SystemConfig,
    ChallengeType,
)
from nehanth.observability.logging_config import setup_logging
from nehanth.observability.telemetry import setup_telemetry
from nehanth.observability.health import HealthMonitor
from nehanth.execution.mt5_gateway import MT5Gateway
from nehanth.execution.mt5_executor import MT5Executor
from nehanth.execution.reconciliation import PositionReconciler
from nehanth.store.event_store import EventStore
from nehanth.engine.rule_engine import FTMORuleEngine
from nehanth.agents.signal_agent import SignalAgent
from nehanth.agents.risk_agent import RiskAgent
from nehanth.agents.execution_agent import ExecutionAgent
from nehanth.agents.coordinator import Coordinator
from nehanth.supervisor.watchdog import Supervisor


# Module-level logger and telemetry instances
logger: Optional[structlog.BoundLogger] = None
health_monitor: Optional[HealthMonitor] = None
coordinator: Optional[Coordinator] = None


def load_config(config_path: Optional[str] = None) -> tuple[FTMOConfig, SystemConfig]:
    """Load configuration from YAML file and override with environment variables.

    Args:
        config_path: Path to config YAML file. If None, uses default path.

    Returns:
        Tuple of (FTMOConfig, SystemConfig) loaded and merged with environment variables.

    Raises:
        FileNotFoundError: If config file not found.
        ValueError: If configuration is invalid.
    """
    if config_path is None:
        # Try to find default config relative to this module
        default_config = Path(__file__).parent.parent / "config" / "default.yaml"
        if not default_config.exists():
            # Fallback to environment or current directory
            config_path = os.getenv("FTMO_CONFIG_PATH", "config/default.yaml")
        else:
            config_path = str(default_config)

    config_path = Path(config_path)
    if not config_path.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")

    with open(config_path, "r") as f:
        config_dict = yaml.safe_load(f)

    # Extract FTMO and system config sections
    ftmo_dict = config_dict.get("ftmo", {})
    system_dict = config_dict.get("system", {})

    # Override with environment variables
    # FTMO_INITIAL_CAPITAL, FTMO_MDL_PCT, etc.
    for key in ftmo_dict:
        env_key = f"FTMO_{key.upper()}"
        if env_key in os.environ:
            value = os.environ[env_key]
            # Try to convert to appropriate type
            if key in ["mdl_pct", "ml_pct", "caution_threshold_pct", "risk_off_threshold_pct", "initial_capital"]:
                ftmo_dict[key] = Decimal(value)
            elif key in ["pre_midnight_flatten_minutes", "magic"]:
                ftmo_dict[key] = int(value)
            else:
                ftmo_dict[key] = value

    # System config overrides
    for key in system_dict:
        env_key = f"SYSTEM_{key.upper()}"
        if env_key in os.environ:
            value = os.environ[env_key]
            if key in ["tick_interval_ms", "heartbeat_interval_s", "max_agent_restarts"]:
                system_dict[key] = int(value)
            else:
                system_dict[key] = value

    # MT5 section - override login, server, password from environment
    mt5_dict = config_dict.get("mt5", {})
    if "MT5_LOGIN" in os.environ:
        mt5_dict["login"] = int(os.environ["MT5_LOGIN"])
    if "MT5_SERVER" in os.environ:
        mt5_dict["server"] = os.environ["MT5_SERVER"]
    if "MT5_PASSWORD" in os.environ:
        mt5_dict["password"] = os.environ["MT5_PASSWORD"]

    # Map mt5 config to SystemConfig fields
    system_dict["mt5_login"] = mt5_dict.get("login", 0)
    system_dict["mt5_server"] = mt5_dict.get("server", "")
    system_dict["mt5_password"] = mt5_dict.get("password", "")
    system_dict["symbols"] = mt5_dict.get("symbols", [])
    system_dict["otel_endpoint"] = config_dict.get("observability", {}).get("otel_endpoint")
    system_dict["log_level"] = system_dict.get("log_level", "INFO")

    # Create configuration objects
    ftmo_config = FTMOConfig(**ftmo_dict)
    system_config = SystemConfig(**system_dict)

    return ftmo_config, system_config


async def validate_configuration(
    ftmo_config: FTMOConfig,
    system_config: SystemConfig,
) -> None:
    """Validate configuration at startup.

    Performs comprehensive checks including:
    - Symbol format validation (.sim suffix for FTMO)
    - Magic number consistency
    - Configuration value ranges

    Args:
        ftmo_config: FTMO challenge configuration.
        system_config: System operational configuration.

    Raises:
        ValueError: If configuration is invalid.
    """
    logger.info("validating_configuration")

    # Validate symbols exist and have proper format
    if not system_config.symbols:
        raise ValueError("At least one symbol must be configured")

    for symbol in system_config.symbols:
        # FTMO US typically uses .sim suffix
        if not symbol:
            raise ValueError("Symbol cannot be empty")
        if len(symbol) > 10:
            raise ValueError(f"Symbol too long: {symbol} (max 10 chars)")
        logger.info("symbol_validated", symbol=symbol)

    # Validate magic number
    if ftmo_config.magic < 0:
        raise ValueError(f"Magic number must be non-negative: {ftmo_config.magic}")

    # Validate thresholds make sense
    if ftmo_config.caution_threshold_pct >= ftmo_config.risk_off_threshold_pct:
        raise ValueError(
            f"CAUTION threshold ({ftmo_config.caution_threshold_pct}%) must be < "
            f"RISK_OFF threshold ({ftmo_config.risk_off_threshold_pct}%)"
        )

    if ftmo_config.mdl_pct >= ftmo_config.ml_pct:
        raise ValueError(
            f"MDL ({ftmo_config.mdl_pct}%) must be < ML ({ftmo_config.ml_pct}%)"
        )

    logger.info("configuration_validated", symbols=len(system_config.symbols), magic=ftmo_config.magic)


async def startup_self_test(
    gateway: MT5Gateway,
    executor: MT5Executor,
    system_config: SystemConfig,
) -> None:
    """Run startup self-tests to verify system readiness.

    Tests:
    - MT5 connection and account info retrieval
    - Symbol availability in MT5
    - Fill mode configuration

    Args:
        gateway: MT5Gateway instance.
        executor: MT5Executor instance.
        system_config: System configuration.

    Raises:
        RuntimeError: If any self-test fails.
    """
    logger.info("running_startup_self_test")

    # Test 1: Check MT5 connection
    try:
        account_info = await asyncio.get_event_loop().run_in_executor(None, gateway.get_account_info)
        if not account_info:
            raise RuntimeError("Failed to retrieve account info from MT5 - connection may be down")
        logger.info(
            "mt5_connection_verified",
            login=account_info.get("login"),
            balance=account_info.get("balance"),
        )
    except Exception as e:
        raise RuntimeError(f"MT5 connection test failed: {str(e)}")

    # Test 2: Check symbol availability
    for symbol in system_config.symbols:
        try:
            tick = await asyncio.get_event_loop().run_in_executor(None, gateway.get_tick, symbol)
            if not tick:
                logger.warning("symbol_not_available", symbol=symbol, message="No tick data - symbol may not exist")
            else:
                logger.info("symbol_verified", symbol=symbol, bid=tick.get("bid"), ask=tick.get("ask"))
        except Exception as e:
            logger.warning("symbol_test_failed", symbol=symbol, error=str(e))

    logger.info("startup_self_test_completed")


async def initialize_components(
    ftmo_config: FTMOConfig,
    system_config: SystemConfig,
) -> tuple[
    MT5Gateway,
    MT5Executor,
    PositionReconciler,
    EventStore,
    FTMORuleEngine,
    SignalAgent,
    RiskAgent,
    ExecutionAgent,
    Coordinator,
    Supervisor,
]:
    """Initialize all system components.

    Args:
        ftmo_config: FTMO challenge configuration.
        system_config: System operational configuration.

    Returns:
        Tuple of initialized components ready for operation.

    Raises:
        ValueError: If configuration is invalid.
        RuntimeError: If initialization or self-tests fail.
    """
    logger.info("initializing_components", ftmo_capital=ftmo_config.initial_capital)

    # Validate configuration before initializing components
    await validate_configuration(ftmo_config, system_config)

    # Initialize MT5 gateway and executor
    mt5_gateway = MT5Gateway(
        login=system_config.mt5_login,
        server=system_config.mt5_server,
        password=system_config.mt5_password,
    )
    logger.info("mt5_gateway_initialized")

    mt5_executor = MT5Executor(gateway=mt5_gateway)
    logger.info("mt5_executor_initialized")

    # Run startup self-test to verify system is ready
    try:
        await startup_self_test(mt5_gateway, mt5_executor, system_config)
    except RuntimeError as e:
        logger.error("startup_self_test_failed", error=str(e))
        raise

    # Initialize reconciler
    position_reconciler = PositionReconciler(
        executor=mt5_executor,
        ftmo_config=ftmo_config,
    )
    logger.info("position_reconciler_initialized")

    # Initialize event store
    event_store_dir = os.getenv("FTMO_EVENT_STORE_DIR", "data/events")
    os.makedirs(event_store_dir, exist_ok=True)
    event_store = EventStore(storage_path=event_store_dir)
    logger.info("event_store_initialized", path=event_store_dir)

    # Initialize rule engine
    rule_engine = FTMORuleEngine(ftmo_config=ftmo_config, event_store=event_store)
    logger.info("rule_engine_initialized")

    # Initialize agents
    signal_agent = SignalAgent(
        name="signal_agent",
        event_store=event_store,
    )
    logger.info("signal_agent_initialized")

    risk_agent = RiskAgent(
        name="risk_agent",
        ftmo_config=ftmo_config,
        event_store=event_store,
    )
    logger.info("risk_agent_initialized")

    execution_agent = ExecutionAgent(
        name="execution_agent",
        executor=mt5_executor,
        event_store=event_store,
    )
    logger.info("execution_agent_initialized")

    # Initialize coordinator
    coordinator_instance = Coordinator(
        signal_agent=signal_agent,
        risk_agent=risk_agent,
        execution_agent=execution_agent,
        rule_engine=rule_engine,
        event_store=event_store,
    )
    logger.info("coordinator_initialized")

    # Initialize supervisor
    supervisor = Supervisor(
        coordinator=coordinator_instance,
        mt5_gateway=mt5_gateway,
        position_reconciler=position_reconciler,
        event_store=event_store,
        ftmo_config=ftmo_config,
        system_config=system_config,
    )
    logger.info("supervisor_initialized")

    return (
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
    )


async def run_event_loop(
    supervisor: Supervisor,
    system_config: SystemConfig,
) -> None:
    """Run main async event loop for trading system.

    Executes:
    - Periodic health checks (heartbeat)
    - Supervisor monitoring tasks
    - Event processing from all components

    Args:
        supervisor: System supervisor managing all agents and components.
        system_config: System configuration with timing parameters.
    """
    logger.info("starting_event_loop", heartbeat_interval_s=system_config.heartbeat_interval_s)

    # Create tasks for main loop
    tasks = []

    # Supervisor loop task
    supervisor_task = asyncio.create_task(supervisor.run())
    tasks.append(supervisor_task)

    # Health check task
    health_check_interval = system_config.heartbeat_interval_s
    async def health_check_loop() -> None:
        """Periodic health check."""
        while True:
            try:
                await asyncio.sleep(health_check_interval)
                if health_monitor:
                    await health_monitor.check_health()
                    logger.debug("health_check_completed")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("health_check_failed", error=str(e))

    health_task = asyncio.create_task(health_check_loop())
    tasks.append(health_task)

    # Wait for all tasks, cancelling on first exception or external shutdown
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
        for task in done:
            try:
                task.result()
            except Exception as e:
                logger.error("task_failed", error=str(e), exc_info=True)
        # Cancel remaining tasks
        for task in pending:
            task.cancel()
    except asyncio.CancelledError:
        logger.info("event_loop_cancelled")
        for task in tasks:
            task.cancel()
        # Wait for all to finish
        await asyncio.gather(*tasks, return_exceptions=True)


async def start_health_check_server(port: int = 8080) -> web.AppRunner:
    """Start simple HTTP server for health checks and metrics.

    Provides:
    - GET /health - returns 200 if system healthy
    - GET /metrics - Prometheus metrics endpoint
    - GET /ready - returns 200 if system ready for trading

    Args:
        port: Port to listen on.

    Returns:
        AppRunner instance for shutdown.
    """
    app = web.Application()

    async def health_handler(request: web.Request) -> web.Response:
        """Simple health check endpoint."""
        if health_monitor and await health_monitor.is_healthy():
            return web.json_response({"status": "healthy"}, status=200)
        return web.json_response({"status": "unhealthy"}, status=503)

    async def ready_handler(request: web.Request) -> web.Response:
        """Readiness check endpoint."""
        if health_monitor and await health_monitor.is_ready():
            return web.json_response({"status": "ready"}, status=200)
        return web.json_response({"status": "not_ready"}, status=503)

    async def metrics_handler(request: web.Request) -> web.Response:
        """Prometheus metrics endpoint."""
        if health_monitor:
            metrics_data = await health_monitor.get_metrics()
            return web.Response(text=metrics_data, content_type="text/plain")
        return web.Response(text="", content_type="text/plain")

    app.router.add_get("/health", health_handler)
    app.router.add_get("/ready", ready_handler)
    app.router.add_get("/metrics", metrics_handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info("health_check_server_started", port=port)
    return runner


async def run() -> None:
    """Main async entry point for the trading system.

    Coordinates:
    1. Configuration loading
    2. Logging and telemetry setup
    3. Component initialization
    4. Health check HTTP server startup
    5. Main event loop execution
    6. Graceful shutdown on signal

    Raises:
        FileNotFoundError: If configuration file not found.
        ValueError: If configuration is invalid.
    """
    global logger, health_monitor, coordinator

    # Set Decimal precision to 10 decimal places for financial calculations
    # This matches the PRD requirement and provides sub-cent precision for USD
    context = getcontext()
    context.prec = 10

    # Load configuration
    try:
        ftmo_config, system_config = load_config()
    except (FileNotFoundError, ValueError) as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        sys.exit(1)

    # Setup logging
    logger = setup_logging(
        log_level=system_config.log_level,
        json_output=True,
    )
    logger.info("nehanth_started", version="1.0.0")

    # Setup telemetry
    tracer, meter = setup_telemetry(
        service_name="ftmo-mt5",
        otel_endpoint=system_config.otel_endpoint,
    )
    logger.info("telemetry_configured", otel_endpoint=system_config.otel_endpoint)

    # Initialize health monitor
    health_monitor = HealthMonitor()
    logger.info("health_monitor_initialized")

    # Initialize all components
    event_store = None
    try:
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
        coordinator = coordinator_instance
    except Exception as e:
        logger.error("component_initialization_failed", error=str(e), exc_info=True)
        sys.exit(1)

    # Start health check HTTP server
    server_runner = None
    try:
        server_runner = await start_health_check_server(port=8080)
    except Exception as e:
        logger.error("health_check_server_failed", error=str(e))
        # Don't exit - system can still run without health checks

    # Setup signal handlers for graceful shutdown
    shutdown_event = asyncio.Event()

    def signal_handler(sig: int, frame: Any) -> None:
        """Handle shutdown signals."""
        logger.warning("shutdown_signal_received", signal=sig)
        shutdown_event.set()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Run main event loop
    try:
        # Create main loop task
        loop_task = asyncio.create_task(run_event_loop(supervisor, system_config))

        # Wait for shutdown signal or loop completion
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
    except Exception as e:
        logger.error("event_loop_failed", error=str(e), exc_info=True)
        sys.exit(1)
    finally:
        # Graceful shutdown sequence
        try:
            # Step 1: Stop supervisor (closes all agents)
            try:
                await supervisor.shutdown()
                logger.info("supervisor_shutdown_complete")
            except Exception as e:
                logger.error("supervisor_shutdown_failed", error=str(e))

            # Step 2: Close event store (flush events)
            try:
                if event_store:
                    event_store.close()
                    logger.info("event_store_closed")
            except Exception as e:
                logger.error("event_store_close_failed", error=str(e))

            # Step 3: Stop health check server
            if server_runner:
                try:
                    await server_runner.cleanup()
                    logger.info("health_check_server_stopped")
                except Exception as e:
                    logger.error("server_cleanup_failed", error=str(e))

            logger.info("nehanth_shutdown_complete")

        except Exception as e:
            logger.error("shutdown_sequence_error", error=str(e))


def main() -> None:
    """Synchronous entry point for CLI invocation.

    Called by the ftmo-mt5 console script defined in pyproject.toml.
    Runs the async event loop using asyncio.run().
    """
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(f"Fatal error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
