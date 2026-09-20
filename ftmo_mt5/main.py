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
import time
import os
import signal
import sys
from pathlib import Path
from typing import Optional, Any
from decimal import Decimal, getcontext
from datetime import datetime, timezone

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

import yaml
import structlog
from aiohttp import web

from ftmo_mt5.core.models import (
    FTMOConfig,
    SystemConfig,
    ChallengeType,
)
from ftmo_mt5.observability.logging_config import setup_logging
from ftmo_mt5.observability.telemetry import setup_telemetry
from ftmo_mt5.observability.health import HealthMonitor
import sys as _sys
if _sys.platform == "win32":
    from ftmo_mt5.execution.mt5_gateway import MT5Gateway
else:
    from ftmo_mt5.execution.remote_gateway import RemoteMT5Gateway as MT5Gateway
from ftmo_mt5.execution.mt5_executor import MT5Executor
from ftmo_mt5.execution.reconciliation import PositionReconciler
from ftmo_mt5.store.event_store import EventStore
from ftmo_mt5.engine.rule_engine import FTMORuleEngine
from ftmo_mt5.agents.signal_agent import SignalAgent
from ftmo_mt5.agents.risk_agent import RiskAgent
from ftmo_mt5.agents.execution_agent import ExecutionAgent
from ftmo_mt5.agents.coordinator import Coordinator
from ftmo_mt5.supervisor.watchdog import Supervisor


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
    """
    logger.info("initializing_components", ftmo_capital=ftmo_config.initial_capital)

    # Pick the broker this run trades through. Every gateway satisfies the same
    # interface (see execution/broker_base.py), so nothing downstream changes.
    broker = os.getenv("BROKER", "mt5").strip().lower()

    if broker == "webull":
        from ftmo_mt5.execution.webull_gateway import WebullGateway

        mt5_gateway = WebullGateway()
        logger.info("webull_gateway_initialized", endpoint=mt5_gateway.endpoint)
    elif sys.platform == "win32":
        # Windows can drive the MT5 terminal directly.
        mt5_gateway = MT5Gateway(
            login=system_config.mt5_login,
            server=system_config.mt5_server,
            password=system_config.mt5_password,
        )
        logger.info("mt5_gateway_initialized", mode="local")
    else:
        # Elsewhere MT5 is only reachable through the bridge in the Windows VM.
        vm_url = os.getenv("MT5_VM_URL", "http://192.168.64.2:8500")
        mt5_gateway = MT5Gateway(vm_url=vm_url)
        logger.info("mt5_gateway_initialized", mode="remote", url=vm_url)

    mt5_executor = MT5Executor(gateway=mt5_gateway)
    logger.info("mt5_executor_initialized")

    # Initialize reconciler
    position_reconciler = PositionReconciler(gateway=mt5_gateway)
    logger.info("position_reconciler_initialized")

    # Initialize event store
    event_store_dir = os.getenv("FTMO_EVENT_STORE_DIR", "data/events")
    os.makedirs(event_store_dir, exist_ok=True)
    event_store = EventStore(storage_path=event_store_dir)
    logger.info("event_store_initialized", path=event_store_dir)

    # Initialize rule engine
    rule_engine = FTMORuleEngine()
    logger.info("rule_engine_initialized")

    # Initialize agents
    signal_agent = SignalAgent(
        strategy_name="ftmo_main",
        symbols=system_config.symbols,
    )
    logger.info("signal_agent_initialized")

    risk_agent = RiskAgent(
        config=ftmo_config,
        rule_engine=rule_engine,
    )
    logger.info("risk_agent_initialized")

    execution_agent = ExecutionAgent(
        mt5_executor=mt5_executor,
    )
    logger.info("execution_agent_initialized")

    # Initialize coordinator
    coordinator_instance = Coordinator(
        signal_agent=signal_agent,
        risk_agent=risk_agent,
        execution_agent=execution_agent,
        event_store=event_store,
        rule_engine=rule_engine,
    )
    logger.info("coordinator_initialized")

    # Initialize supervisor
    supervisor = Supervisor(event_store=event_store)
    supervisor.register_agent(signal_agent)
    supervisor.register_agent(risk_agent)
    supervisor.register_agent(execution_agent)
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


def build_ftmo_limits(ftmo_config: FTMOConfig, account_state: Any) -> Any:
    """Snapshot the challenge limits the risk agent evaluates against."""
    from ftmo_mt5.core.models import FTMOLimits

    capital = Decimal(str(ftmo_config.initial_capital))
    return FTMOLimits(
        mdl_limit=capital * Decimal(str(ftmo_config.mdl_pct)) / Decimal("100"),
        ml_limit=capital * Decimal(str(ftmo_config.ml_pct)) / Decimal("100"),
        balance_at_midnight=account_state.balance,
        highest_eod_balance=max(account_state.balance, capital),
        challenge_type=ftmo_config.challenge_type,
    )


async def market_data_loop(
    coordinator_instance: Coordinator,
    mt5_gateway: Any,
    ftmo_config: FTMOConfig,
    system_config: SystemConfig,
    poll_interval_s: float = 5.0,
    timeframe: str = "M15",
) -> None:
    """Poll the market and drive one Signal→Risk→Execution pass per symbol.

    This is the system's actual pulse. Without it the agents sit idle, their
    heartbeats go stale, and the supervisor trips the kill switch — so every
    iteration also beats each agent's heartbeat, whether or not a signal fired.

    Args:
        coordinator_instance: Coordinator owning the sequential pipeline.
        mt5_gateway: Gateway used for candles, account state, and positions.
        ftmo_config: Challenge configuration (limits, capital, magic).
        system_config: System configuration (symbol list).
        poll_interval_s: Delay between full sweeps of the symbol list.
        timeframe: Candle timeframe the strategy runs on.
    """
    from ftmo_mt5.strategy import EMACrossoverStrategy

    strategy = EMACrossoverStrategy()
    symbols = list(system_config.symbols)

    logger.info(
        "market_data_loop_starting",
        symbols=symbols,
        timeframe=timeframe,
        poll_interval_s=poll_interval_s,
        strategy=strategy.name,
    )

    # Only act on a bar once: remember the last bar timestamp we evaluated.
    last_bar_seen: dict[str, int] = {}

    def beat() -> None:
        """Mark all three agents alive."""
        coordinator_instance.signal_agent.heartbeat()
        coordinator_instance.risk_agent.heartbeat()
        coordinator_instance.execution_agent.heartbeat()

    # The supervisor kills the system if the risk agent's heartbeat is 5s stale,
    # but a single broker call — an OHLCV fetch, or a whole signal→risk→order
    # round trip — can legitimately outlast that. Beating only at I/O boundaries
    # still lost the race, so a ticker beats on a short interval instead.
    #
    # It stays honest by watching the loop's own progress: if a sweep hasn't
    # completed within STALL_LIMIT_S the loop really is wedged, the ticker stops
    # beating, and the kill switch fires as designed.
    STALL_LIMIT_S = 180.0
    last_progress = time.monotonic()

    async def heartbeat_ticker() -> None:
        while True:
            if time.monotonic() - last_progress > STALL_LIMIT_S:
                logger.critical(
                    "market_loop_stalled",
                    seconds_since_progress=round(time.monotonic() - last_progress, 1),
                    message="No sweep completed — letting the supervisor halt the system",
                )
                return
            beat()
            await asyncio.sleep(1.0)

    ticker = asyncio.create_task(heartbeat_ticker())

    while True:
        try:
            beat()

            # Gateway calls are blocking HTTP — keep them off the event loop so
            # the supervisor's monitor task can still run while they're in flight.
            account_state = await asyncio.to_thread(mt5_gateway.get_account_info)
            positions = await asyncio.to_thread(mt5_gateway.get_positions)
            ftmo_limits = build_ftmo_limits(ftmo_config, account_state)

            logger.debug(
                "market_sweep",
                equity=str(account_state.equity),
                balance=str(account_state.balance),
                open_positions=len(positions),
            )

            for symbol in symbols:
                beat()
                bars = await asyncio.to_thread(
                    mt5_gateway.get_ohlcv, symbol, timeframe, 200
                )
                if len(bars) < strategy.min_bars:
                    logger.warning("no_candles_for_symbol", symbol=symbol, bars=len(bars))
                    continue

                # bars[-1] is still forming; bars[-2] is the newest closed bar.
                newest_closed = bars[-2]["time"]
                if last_bar_seen.get(symbol) == newest_closed:
                    continue
                last_bar_seen[symbol] = newest_closed

                tick_data = strategy.evaluate(symbol, bars)
                if tick_data is None:
                    continue

                await coordinator_instance.process_tick(
                    tick_data=tick_data,
                    account_state=account_state,
                    ftmo_limits=ftmo_limits,
                    positions=positions,
                )

                # A fill changes both figures — re-read before the next symbol.
                beat()
                account_state = await asyncio.to_thread(mt5_gateway.get_account_info)
                positions = await asyncio.to_thread(mt5_gateway.get_positions)

            last_progress = time.monotonic()
            await asyncio.sleep(poll_interval_s)

        except asyncio.CancelledError:
            logger.info("market_data_loop_cancelled")
            ticker.cancel()
            break
        except Exception as e:
            logger.error("market_data_loop_error", error=str(e), exc_info=True)
            await asyncio.sleep(poll_interval_s)


async def run_event_loop(
    supervisor: Supervisor,
    system_config: SystemConfig,
    coordinator_instance: Optional[Coordinator] = None,
    mt5_gateway: Optional[Any] = None,
    ftmo_config: Optional[FTMOConfig] = None,
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
    supervisor_task = asyncio.create_task(supervisor.start())
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

    # Market data task — the loop that actually trades. Without it the agents
    # never receive work and the supervisor kill-switches them on stale
    # heartbeats, so treat a missing coordinator/gateway as a hard error.
    if coordinator_instance and mt5_gateway and ftmo_config:
        market_task = asyncio.create_task(
            market_data_loop(
                coordinator_instance=coordinator_instance,
                mt5_gateway=mt5_gateway,
                ftmo_config=ftmo_config,
                system_config=system_config,
            )
        )
        tasks.append(market_task)
    else:
        logger.error(
            "market_data_loop_not_started",
            message="No coordinator/gateway supplied — system will not trade",
        )

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
        # get_metrics() returns a dict, so serialise it — handing the dict
        # straight to web.Response(text=...) raises and yields a 500.
        if health_monitor:
            return web.json_response(await health_monitor.get_metrics())
        return web.json_response({})

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
    logger.info("ftmo_mt5_started", version="1.0.0")

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
        loop_task = asyncio.create_task(
            run_event_loop(
                supervisor,
                system_config,
                coordinator_instance=coordinator_instance,
                mt5_gateway=mt5_gateway,
                ftmo_config=ftmo_config,
            )
        )

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
        # Cleanup
        try:
            await supervisor.stop()
            logger.info("supervisor_shutdown_complete")
        except Exception as e:
            logger.error("supervisor_shutdown_failed", error=str(e))

        if server_runner:
            try:
                await server_runner.cleanup()
                logger.info("health_check_server_stopped")
            except Exception as e:
                logger.error("server_cleanup_failed", error=str(e))

        logger.info("ftmo_mt5_shutdown_complete")


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
