# FTMO MT5 Trading System

An institutional-grade algorithmic trading system for FTMO challenges featuring multi-agent orchestration, machine learning signal filtering, and strict risk management compliance.

## System Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                    FTMO MT5 Trading System                      │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │                  Market Data Feed                         │   │
│  │              (MT5 Terminal → Gateway)                     │   │
│  └──────────────────────────────────────────────────────────┘   │
│                           │                                     │
│                           ▼                                     │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │              Signal Agent                                 │   │
│  │  - Technical indicators (MA, RSI, MACD, etc.)            │   │
│  │  - Feature engineering for ML models                     │   │
│  │  - Generates TradingSignal events                         │   │
│  └──────────────────────────────────────────────────────────┘   │
│                           │                                     │
│                           ▼                                     │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │              Risk Agent                                   │   │
│  │  - Daily loss limit enforcement (5%)                      │   │
│  │  - Total loss limit enforcement (10%)                     │   │
│  │  - Position sizing and margin validation                  │   │
│  │  - Caution mode and Risk Off mode transitions             │   │
│  │  - Circuit breaker on breach detection                    │   │
│  └──────────────────────────────────────────────────────────┘   │
│                           │                                     │
│                           ▼                                     │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │              Execution Agent                              │   │
│  │  - Order submission (market, limit, stop)                │   │
│  │  - Order cancellation and modification                   │   │
│  │  - Fill tracking and reconciliation                      │   │
│  │  - Position lifecycle management                         │   │
│  └──────────────────────────────────────────────────────────┘   │
│                           │                                     │
│                           ▼                                     │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │              MT5 Gateway (Thread-Safe)                    │   │
│  │  - Login/logout management                               │   │
│  │  - Order submission via threading.Lock()                 │   │
│  │  - Account state queries                                 │   │
│  │  - Historical data retrieval                             │   │
│  └──────────────────────────────────────────────────────────┘   │
│                           │                                     │
│                           ▼                                     │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │              MT5 Terminal (Platform)                      │   │
│  │  - Real-time market data                                │   │
│  │  - Order execution                                       │   │
│  │  - Account management                                    │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │         Event Store (Append-Only Audit Trail)            │   │
│  │  - All orders, fills, risk state changes                │   │
│  │  - Enable replay and forensic analysis                  │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │         Health Check Server (Port 8080)                  │   │
│  │  - GET /health → System health                          │   │
│  │  - GET /ready → Trading readiness                       │   │
│  │  - GET /metrics → Prometheus metrics                    │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘

Data Flow: Market Data → Signal Agent → Risk Agent → Execution Agent
           → MT5 Gateway → Order Execution → Event Store (audit trail)
```

## Prerequisites

### System Requirements
- **OS**: Windows Server 2016+ or Linux with Wine/PlayOnLinux for MT5
- **Python**: 3.11 or 3.12
- **RAM**: Minimum 4GB, recommended 8GB+
- **Disk**: 10GB for event store and historical data
- **Network**: Stable internet connection for broker connectivity

### Trading Requirements
- **FTMO Account**: Active challenge account
- **MT5 Terminal**: Installed on same machine or networked
- **Broker Credentials**: Login, password, and server name from FTMO
- **Capital**: Minimum $5,000 (varies by challenge type)

### Software Dependencies
```bash
# Core dependencies installed via pip
pydantic>=2.0              # Data validation
structlog                  # Structured logging
opentelemetry-*            # Distributed tracing
prometheus-client          # Metrics
numpy, scipy, pandas       # Numerical computing
scikit-learn               # ML preprocessing
pyyaml                     # Configuration
aiohttp                    # Async HTTP
```

## Installation

### Step 1: Clone and Setup Virtual Environment

```bash
# Clone repository
git clone <repository-url> ftmo-mt5
cd ftmo-mt5

# Create virtual environment
python3.11 -m venv venv

# Activate
# On Windows:
venv\Scripts\activate
# On Linux/macOS:
source venv/bin/activate
```

### Step 2: Install Package

```bash
# Development installation (includes test dependencies)
pip install -e ".[dev]"

# Production installation (minimal)
pip install -e "."

# Optional ML dependencies
pip install -e ".[ml]"
```

### Step 3: Configure MT5 Credentials

Create a `.env` file or export environment variables:

```bash
# Required
export MT5_LOGIN=12345678
export MT5_SERVER="ICMarkets-Demo"
export MT5_PASSWORD="your_password_here"

# Optional
export FTMO_CONFIG_PATH="config/1step.yaml"
export FTMO_EVENT_STORE_DIR="data/events"
export LOG_LEVEL="INFO"
export OTEL_ENDPOINT="localhost:4317"
```

### Step 4: Create Configuration File

Copy and customize configuration for your challenge:

```bash
# 1-Step Challenge
cp config/1step.yaml.example config/1step.yaml
# Edit with your parameters

# 2-Step Challenge
cp config/2step.yaml.example config/2step.yaml
# Edit with your parameters
```

Example configuration structure:
```yaml
ftmo:
  initial_capital: "100000"      # In account currency (Decimal string)
  challenge_type: "1STEP"         # 1STEP or 2STEP
  daily_loss_limit_pct: "5"       # % of initial capital
  total_loss_limit_pct: "10"      # % of initial capital
  profit_target_pct: "10"         # % to achieve challenge
  pre_midnight_flatten_minutes: 30 # Minutes before midnight to flatten
  magic: 999999                   # Magic number for order identification

system:
  tick_interval_ms: 1000
  heartbeat_interval_s: 5
  max_agent_restarts: 3
  log_level: "INFO"

mt5:
  login: 12345678
  server: "ICMarkets-Demo"
  password: "password"
  symbols:
    - EURUSD
    - GBPUSD
    - USDJPY
    - GOLD

observability:
  otel_endpoint: "localhost:4317"
```

### Step 5: Verify Installation

```bash
# Check CLI works
ftmo-mt5 --help

# Run tests to verify everything
pytest tests/ -v --cov=src/nehanth

# Check static analysis
ruff check src/ tests/
mypy src/ --strict
```

## Running the System

### Development Mode

```bash
# With debug logging
LOG_LEVEL=DEBUG ftmo-mt5

# With custom config
FTMO_CONFIG_PATH=config/1step.yaml ftmo-mt5

# Watch logs in real-time (separate terminal)
tail -f logs/trading.log | jq '.'
```

### Production Mode

```bash
# Standard startup
ftmo-mt5

# With systemd service (Linux)
sudo systemctl start ftmo-mt5
sudo systemctl status ftmo-mt5
tail -f /var/log/ftmo-mt5/trading.log

# With process manager (e.g., supervisor)
supervisord -c etc/supervisord.conf
```

### Health Checks

```bash
# System health
curl http://localhost:8080/health

# Trading readiness
curl http://localhost:8080/ready

# Prometheus metrics
curl http://localhost:8080/metrics
```

### Graceful Shutdown

```bash
# Send SIGINT (Ctrl+C in console)
kill -SIGINT <pid>

# System will:
# 1. Stop accepting new signals
# 2. Cancel all pending orders
# 3. Flatten all positions (if safe)
# 4. Close event store
# 5. Log shutdown completion
```

## Configuration Reference

### FTMO Challenge Configuration

#### 1-Step Challenge
```yaml
ftmo:
  challenge_type: "1STEP"
  daily_loss_limit_pct: "5"      # Example: $5,000 on $100,000 account
  total_loss_limit_pct: "10"     # Example: $10,000 total
  profit_target_pct: "10"        # Example: $10,000 profit = pass
```

#### 2-Step Challenge (Phase 1)
```yaml
ftmo:
  challenge_type: "2STEP"
  daily_loss_limit_pct: "5"      # % of phase 1 capital
  total_loss_limit_pct: "10"
  profit_target_pct: "10"        # After 10% profit, move to phase 2
```

#### 2-Step Challenge (Phase 2)
```yaml
ftmo:
  challenge_type: "2STEP"
  daily_loss_limit_pct: "5"      # % of doubled capital
  total_loss_limit_pct: "10"
  profit_target_pct: "5"         # Lower target in phase 2
```

### System Configuration

| Parameter | Default | Description |
|-----------|---------|-------------|
| `tick_interval_ms` | 1000 | Market data sampling interval |
| `heartbeat_interval_s` | 5 | Health check frequency |
| `max_agent_restarts` | 3 | Restart attempts before HALT |
| `log_level` | INFO | DEBUG, INFO, WARNING, ERROR |

### Environment Variables Summary

```bash
# Required for trading
MT5_LOGIN                    # Account number
MT5_SERVER                   # Broker server (e.g., "ICMarkets-Demo")
MT5_PASSWORD                 # Account password

# Configuration
FTMO_CONFIG_PATH             # Path to config YAML
FTMO_EVENT_STORE_DIR         # Event store directory

# System
SYSTEM_TICK_INTERVAL_MS      # Market data interval
SYSTEM_HEARTBEAT_INTERVAL_S  # Health check interval
SYSTEM_MAX_AGENT_RESTARTS    # Restart limit

# Observability
OTEL_ENDPOINT                # OpenTelemetry collector
LOG_LEVEL                    # Log level
```

## Monitoring and Observability

### Structured Logging

All logs are output in JSON format for easy parsing:

```json
{
  "timestamp": "2026-04-10T14:30:45.123456+02:00",
  "level": "INFO",
  "event": "order_submitted",
  "order_id": "550e8400-e29b-41d4-a716-446655440000",
  "symbol": "EURUSD",
  "action": "BUY",
  "volume": "1.5",
  "price": "1.08500"
}
```

Log aggregation setup:
```bash
# With ELK Stack
ftmo-mt5 | filebeat -c filebeat.yml | -> Elasticsearch -> Kibana

# With Loki
ftmo-mt5 | promtail --config loki.yml | -> Loki -> Grafana

# With Splunk
ftmo-mt5 | splunk-forwarder | -> Splunk HEC
```

### Metrics

Access Prometheus metrics at `/metrics`:

```
# Counters
ftmo_orders_submitted_total{symbol="EURUSD",action="BUY"}
ftmo_orders_filled_total{symbol="EURUSD",action="BUY"}
ftmo_orders_rejected_total{reason="insufficient_margin"}
ftmo_positions_closed_total{pnl="profit"}

# Gauges
ftmo_open_positions{symbol="EURUSD"}
ftmo_account_equity
ftmo_daily_pnl
ftmo_daily_loss_used_pct
ftmo_margin_usage_pct

# Histograms
ftmo_fill_latency_ms_bucket
ftmo_decision_time_ms_bucket
```

### OpenTelemetry Tracing

If `OTEL_ENDPOINT` is configured, traces are exported:

```bash
# Example: Using Jaeger
docker run -d -p 6831:6831/udp -p 16686:16686 jaegertracing/all-in-one

# Point system to Jaeger
export OTEL_ENDPOINT="localhost:4317"

# View traces at http://localhost:16686
```

### Health Check Interpretation

```bash
# Healthy system
$ curl http://localhost:8080/health
{"status": "healthy"}

# Unhealthy system (risk breach, component failure)
$ curl http://localhost:8080/health
{"status": "unhealthy"}

# Not ready (still initializing, no MT5 connection)
$ curl http://localhost:8080/ready
{"status": "not_ready"}

# Ready for trading
$ curl http://localhost:8080/ready
{"status": "ready"}
```

## Troubleshooting

### Common Issues

#### Issue: MT5 Connection Failed
```
Error: mt5_gateway.py: ConnectionError: Failed to connect to MT5 terminal
```

**Solution:**
1. Verify MT5 terminal is running: Check system taskbar
2. Verify credentials: `MT5_LOGIN`, `MT5_SERVER`, `MT5_PASSWORD`
3. Check network connectivity: Can you ping the broker server?
4. Firewall rules: Ensure MT5 server ports are accessible
5. Restart MT5 terminal and system

#### Issue: Orders Rejected - Insufficient Margin
```
Error: OrderRejected: reason="insufficient_margin"
```

**Solution:**
1. Check account equity: View in MT5 terminal
2. Verify position sizing logic in signal agent
3. Review daily loss limits - may have entered "Risk Off" mode
4. Increase account leverage (if allowed by FTMO)
5. Check for slippage on actual fills vs expected fills

#### Issue: Midnight Boundary Miss
```
Warning: positions_still_open_at_midnight
```

**Solution:**
1. Increase `pre_midnight_flatten_minutes` in config
2. Review signal generation - may be entering positions too late
3. Check for slow MT5 responses during market close
4. Monitor evening market conditions (higher volatility)
5. Consider disabling new entries 1 hour before midnight

#### Issue: Risk Agent HALT
```
Error: risk_state_changed: new_state="HALT", reason="daily_loss_limit_exceeded"
```

**Solution:**
1. Review all trades for that day in event store
2. Identify losing trades and refine signal filter
3. Consider reducing leverage or position sizes
4. Update event store to clear HALT (manual intervention)
5. Restart system

To manually recover from HALT:
```bash
# 1. Stop the system
# 2. Analyze event store
python scripts/replay_events.py --store-dir data/events

# 3. Update event store to clear HALT (requires database edit)
# 4. Restart system
```

#### Issue: High Latency / Slow Fills
```
Debug: fill_latency_ms=5000+
```

**Solution:**
1. Check MT5 network latency: ping broker servers
2. Review system CPU usage: htop / Task Manager
3. Verify no other heavy processes running
4. Check order size vs available liquidity
5. Review time of day (news events, end of session)
6. Reduce `tick_interval_ms` for faster signal generation

#### Issue: Model Drift Detection
```
Warning: signal_drift_detected: threshold=0.95, correlation=0.82
```

**Solution:**
1. Retrain ML models with recent data
2. Check for regime changes in market
3. Review signal agent feature engineering
4. Consider adding new indicators
5. Use drift detection gate before execution

#### Issue: Event Store Growing Too Large
```
Warning: event_store_size_exceeded_threshold: size_gb=50
```

**Solution:**
1. Archive old events to cold storage
2. Run event store cleanup (weekly/monthly)
3. Implement event retention policy
4. Compress event files

```bash
# Archive events older than 90 days
python scripts/archive_events.py --older-than-days 90

# Verify event store integrity
python scripts/verify_event_store.py
```

## Advanced Topics

### Model Promotion Workflow

Promote trained ML models to production:

```bash
# 1. Train model on historical data
python -m ftmo_mt5.ml.model_trainer --data data/eurusd_2020_2023.parquet --output models/

# 2. Backtest model
python scripts/backtest.py --model models/model_v2.pkl --config config/1step.yaml

# 3. Paper trade model
python scripts/paper_trade.py --model models/model_v2.pkl --duration 7d

# 4. Deploy to production
python scripts/deploy_model.py --model models/model_v2.pkl --gate promotion

# 5. Monitor for drift
# (automatic via drift_detection.py)
```

### Event Store Replay

Replay entire trading history for audit:

```bash
# Replay all events
python scripts/replay_events.py --store-dir data/events

# Replay with filters
python scripts/replay_events.py \
  --store-dir data/events \
  --from-timestamp "2026-04-01T00:00:00+02:00" \
  --to-timestamp "2026-04-10T23:59:59+02:00" \
  --symbol "EURUSD"

# Export to CSV for analysis
python scripts/replay_events.py --store-dir data/events --format csv --output events.csv
```

### DST Transitions

The system automatically handles European DST transitions:

- **March**: Spring forward (02:00 → 03:00 UTC+2)
- **October**: Fall back (03:00 → 02:00 UTC+1)

During transitions:
1. Midnight boundary check may skip or repeat
2. Historical data timestamps may have gaps
3. Performance metrics timestamps are UTC-normalized

**Testing DST transitions:**
```bash
# Simulate spring forward
TZ=Europe/Prague python -c "from datetime import datetime; from zoneinfo import ZoneInfo; tz=ZoneInfo('Europe/Prague'); dt=datetime(2026,3,29,1,30,0,tzinfo=tz); print(dt, dt.astimezone())"

# Simulate fall back
TZ=Europe/Prague python -c "from datetime import datetime; from zoneinfo import ZoneInfo; tz=ZoneInfo('Europe/Prague'); dt=datetime(2026,10,25,2,30,0,tzinfo=tz); print(dt, dt.astimezone())"
```

## Support and Contact

For issues or questions:
- Review CLAUDE.md for architecture details
- Check RUNBOOK.md for operational procedures
- Review logs in JSON format: `tail -f logs/trading.log | jq '.'`
- Export event store: `python scripts/replay_events.py --format csv`
- Contact: trading@ftmo.example.com

## License

MIT License - See LICENSE file for details
