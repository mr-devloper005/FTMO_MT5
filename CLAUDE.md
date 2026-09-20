# FTMO MT5 Trading System

## Project Structure
- `ftmo_mt5/` - Main trading system source code
  - `agents/` - Multi-agent components (signal, risk, execution, coordinator)
  - `core/` - Domain models, enums, exceptions
  - `engine/` - Rule engine for FTMO challenge compliance
  - `execution/` - MT5 gateway, executor, reconciliation
  - `ml/` - Signal filtering, drift detection, model promotion
  - `observability/` - Logging, telemetry, health monitoring
  - `store/` - Event store for audit trail
  - `supervisor/` - Watchdog and system supervision
  - `main.py` - Entry point and component orchestration
- `src/nehanth/` - Alternative source structure (legacy/development)
- `tests/` - Comprehensive test suite with integration tests
- `scripts/` - Operational scripts (run, paper trade, replay events)
- `config/` - YAML configuration files for various challenge types

## Key Architecture Decisions
1. **Coordinator/Specialist Pattern**: Sequential asyncio pipeline (signal → risk → execution)
   - Risk agent can HALT system on breach detection
   - Execution agent serializes MT5 API calls via threading.Lock()
   - All decision paths are synchronous (never asyncio.gather)

2. **Decimal for Financial Math**: Precision = 10 (sub-cent accuracy)
   - Set via `getcontext().prec = 10` in main.py
   - All monetary fields use `Decimal` type
   - Never use float for trades, positions, or capital calculations

3. **Immutable Event Models**: `frozen=True` on all Pydantic models
   - Events are append-only in EventStore
   - Enables reliable audit trails and replay
   - All state changes are event-sourced

4. **Prague Time Zone**: `zoneinfo.ZoneInfo("Europe/Prague")`
   - Midnight boundary for daily loss limits
   - Always use explicit ZoneInfo, never hardcoded UTC offsets
   - Account for DST transitions (happens 2x/year in Europe)

5. **MT5 Gateway Serialization**: `threading.Lock()` protects all API calls
   - Single-threaded execution to MT5 platform
   - Prevents race conditions on order submission/cancellation
   - Blocking calls wrapped in thread executor for async compatibility

6. **System HALT on Risk Breach**: Immediate and permanent
   - Risk agent crash or exception → instant system shutdown
   - Never auto-recover from HALT state
   - Manual intervention required (update event store + restart)

7. **Event Sourcing**: Append-only EventStore with replay capability
   - All orders, fills, risk state changes are events
   - Supports audit trail queries and full system replay
   - Event deduplication by order ID and timestamp

## Running

### Development
```bash
# Install dependencies
pip install -e ".[dev]"

# Run tests with coverage
pytest tests/ -v --cov=src/nehanth --cov-report=html

# Static analysis
ruff check src/ tests/
mypy src/ --strict

# Format code
black src/ tests/
```

### Production
```bash
# Run trading system
python -m ftmo_mt5.main

# Or via installed CLI
ftmo-mt5

# With custom config
FTMO_CONFIG_PATH=/path/to/config.yaml ftmo-mt5

# With MT5 credentials override (non-prod only)
MT5_LOGIN=12345 MT5_SERVER="ICMarkets-Demo" MT5_PASSWORD=pass ftmo-mt5
```

### Paper Trading
```bash
python scripts/paper_trade.py --config config/1step.yaml --duration 24h
```

### Event Replay (Audit)
```bash
python scripts/replay_events.py --store-dir data/events --from-timestamp "2026-04-10T08:00:00+02:00"
```

## Critical Constraints

1. **Never use float for money** - Always Decimal with precision 10
2. **Never hardcode UTC offsets** - Use `zoneinfo.ZoneInfo("Europe/Prague")`
3. **Never asyncio.gather() on decision paths** - Sequential signal → risk → execution only
4. **Never auto-recover from HALT state** - Manual recovery only
5. **Pre-midnight safety check is mandatory** - Flatten positions before midnight
6. **Risk agent exception = system HALT** - No graceful degradation for risk failures
7. **Threading.Lock() serializes all MT5 calls** - No concurrent orders or cancellations
8. **Event sourcing is immutable** - EventStore append-only, never edit/delete events

## Challenge Configuration

### 1-Step Challenge
- Initial capital: Varies by account
- Daily loss limit: 5% of initial capital
- Total loss limit: 10% of initial capital
- Profit target: 10% of initial capital
- Leverage limit: Depends on broker
- Max concurrent positions: Typically 5
- See: `config/1step.yaml`

### 2-Step Challenge
- Phase 1: Same as 1-Step, profit target 10%
- Phase 2: Capital doubles, requirements scale up
- Daily loss limit: 5% of phase capital
- Total loss limit: 10% of phase capital
- Profit target: 5% of phase capital
- See: `config/2step.yaml`

## Health Check Endpoints

Server runs on port 8080:
- `GET /health` - System health status (200 = healthy, 503 = unhealthy)
- `GET /ready` - Trading readiness (200 = ready, 503 = not ready)
- `GET /metrics` - Prometheus metrics in plain text format

## Environment Variables

**Core Trading**
- `MT5_LOGIN` - Account login number
- `MT5_SERVER` - Broker server name (e.g., "ICMarkets-Demo")
- `MT5_PASSWORD` - Account password

**Configuration**
- `FTMO_CONFIG_PATH` - Path to config YAML (default: config/default.yaml)
- `FTMO_EVENT_STORE_DIR` - Event store directory (default: data/events)

**System**
- `SYSTEM_TICK_INTERVAL_MS` - Market data tick interval (default: 1000)
- `SYSTEM_HEARTBEAT_INTERVAL_S` - Health check interval (default: 5)
- `SYSTEM_MAX_AGENT_RESTARTS` - Restart attempts before HALT (default: 3)

**Observability**
- `OTEL_ENDPOINT` - OpenTelemetry collector gRPC endpoint (e.g., "localhost:4317")
- `LOG_LEVEL` - Log level: DEBUG, INFO, WARNING, ERROR (default: INFO)

## Testing Strategy

- **Unit tests**: Agent logic, rule engine, signal generation
- **Integration tests**: End-to-end flows with mock MT5
- **Fuzzing**: Hypothesis-based property testing on order execution
- **Scenario tests**: Overnight holds, DST transitions, gap fills

Run: `pytest tests/ -v --cov=src/nehanth`

## Observability

### Logging
- Structured JSON output via structlog
- Fields: timestamp, level, event, agent, position_id, risk_state
- Supports multi-sink (console, file, remote syslog)

### Metrics
- Prometheus format from `/metrics` endpoint
- Counters: orders_submitted, fills_received, risk_halts
- Gauges: open_positions, account_equity, daily_pnl
- Histograms: fill_latency_ms, decision_time_ms

### Traces
- OpenTelemetry gRPC exporter
- Traces: signal generation, risk check, order execution
- Trace links for order ID correlation

## Midnight Boundary Handling

Pre-midnight safety procedures (Prague time):
1. Check remaining time until midnight
2. If less than N minutes (default 30): flatten all positions
3. Record midnight close events with timestamp
4. Verify all positions closed before EOD
5. Reset daily limits and risk state
6. Log transition for audit trail

## DST Transitions

Europe observes DST on last Sunday of March and October (3 weeks/year):
- March: UTC+01:00 → UTC+02:00 at 02:00 local time
- October: UTC+02:00 → UTC+01:00 at 03:00 local time

Mitigation:
- Always use `zoneinfo.ZoneInfo("Europe/Prague")` for time calculations
- Pre-midnight check accounts for potential hour shift
- Test midnight boundary procedures before DST dates
- Review event timestamps in UTC for DST consistency
