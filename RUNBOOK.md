# FTMO MT5 Trading System - Operational Runbook

This document provides step-by-step procedures for operating the FTMO MT5 trading system in production.

## Table of Contents
1. System Startup Procedure
2. Graceful Shutdown Procedure
3. Incident Response
4. Midnight Boundary Monitoring
5. DST Transition Checklist
6. Model Promotion Workflow
7. Event Store Audit and Replay
8. Health Check Interpretation
9. Alert Routing and Escalation

---

## 1. System Startup Procedure

### Pre-Startup Checklist

- [ ] MT5 terminal is running on VPS/Windows machine
- [ ] Network connectivity verified (ping broker server)
- [ ] Environment variables set correctly (MT5_LOGIN, MT5_SERVER, MT5_PASSWORD)
- [ ] Configuration file exists and is valid (config/1step.yaml or config/2step.yaml)
- [ ] Event store directory exists and is writable (data/events)
- [ ] Log directory exists and has sufficient disk space
- [ ] OpenTelemetry collector is running (if OTEL_ENDPOINT configured)
- [ ] Previous HALT state is resolved (if applicable)

### Startup Sequence

#### Step 1: Verify Environment Variables

```bash
# Check required credentials are set
echo "MT5_LOGIN=$MT5_LOGIN"
echo "MT5_SERVER=$MT5_SERVER"
# DO NOT ECHO MT5_PASSWORD in production logs

# Verify config path
echo "FTMO_CONFIG_PATH=${FTMO_CONFIG_PATH:-config/default.yaml}"

# Check event store directory
ls -la "${FTMO_EVENT_STORE_DIR:-data/events}/"
```

#### Step 2: Start System

```bash
# Option 1: CLI command
ftmo-mt5

# Option 2: Python module
python -m ftmo_mt5.main

# Option 3: With custom config
FTMO_CONFIG_PATH=config/1step.yaml ftmo-mt5

# Option 4: In background with logging
nohup ftmo-mt5 > logs/trading.log 2>&1 &
echo $! > logs/trading.pid
```

#### Step 3: Wait for Initialization

The system will log initialization steps. Wait for these messages:

```
event="telemetry_configured" otel_endpoint="..."
event="health_monitor_initialized"
event="mt5_gateway_initialized"
event="coordinator_initialized"
event="supervisor_initialized"
event="health_check_server_started" port=8080
event="starting_event_loop"
```

Expected startup time: 5-10 seconds

#### Step 4: Verify System Health

```bash
# Check health endpoint
curl -s http://localhost:8080/health | jq .
# Expected: {"status": "healthy"} or {"status": "unhealthy"}

# Check readiness
curl -s http://localhost:8080/ready | jq .
# Expected: {"status": "ready"} after market hours, "not_ready" during setup

# Sample metrics
curl -s http://localhost:8080/metrics | head -20
```

#### Step 5: Monitor Initial Trade Activity

```bash
# Watch logs for first signals
tail -f logs/trading.log | jq '.event'

# Look for these sequences:
# 1. market_data_received → symbol, price, timestamp
# 2. signal_generated → symbol, direction, confidence
# 3. risk_checked → action (allowed/caution/rejected)
# 4. order_submitted → order_id, symbol, volume, price
# 5. order_filled → order_id, fill_price, fill_volume
```

#### Startup Verification Checklist

- [ ] No error logs in first 30 seconds
- [ ] Health endpoint returns 200
- [ ] Signals being generated (check logs)
- [ ] Orders being submitted (check MT5 terminal)
- [ ] Event store receiving events (check data/events/ directory)
- [ ] Metrics endpoint operational (curl /metrics returns data)

---

## 2. Graceful Shutdown Procedure

### Standard Shutdown (Recommended)

#### Step 1: Signal System Shutdown

```bash
# If running in foreground (Ctrl+C)
# Press Ctrl+C in terminal

# If running in background
kill -SIGINT $(cat logs/trading.pid)

# If running as systemd service
sudo systemctl stop ftmo-mt5
```

#### Step 2: Monitor Shutdown Sequence

The system will:
1. Stop accepting new signals (5 seconds max)
2. Cancel pending orders (non-critical only)
3. Close/flatten positions (if safe and not in Risk Off)
4. Flush event store to disk
5. Close connections to MT5
6. Log completion

Expected shutdown time: 10-30 seconds

Watch logs:
```bash
tail -f logs/trading.log | grep -E "shutdown|closing|halting"
```

#### Step 3: Verify Shutdown Complete

```bash
# Check process terminated
ps aux | grep ftmo-mt5

# Check no orphaned MT5 connections
netstat -an | grep 443  # Broker connection port

# Verify event store flushed
ls -lh data/events/
# Should show recent modification time

# Check exit logs
tail -20 logs/trading.log | grep "shutdown_complete"
```

#### Shutdown Verification Checklist

- [ ] Process terminated cleanly (no error exit code)
- [ ] All positions closed or pending close
- [ ] Event store synced to disk
- [ ] No error logs in shutdown sequence
- [ ] MT5 terminal shows account state correct

### Emergency Shutdown (Loss of Connection/System Failure)

If system is unresponsive or stuck:

```bash
# Force kill process
kill -9 $(cat logs/trading.pid)

# Or if no PID file
pkill -9 ftmo-mt5

# Clean up connections
netstat -an | grep ESTABLISHED | awk '{print $5}' | grep -E ':(443|4317)' | xargs killall

# Restart system
ftmo-mt5
```

**WARNING**: Emergency shutdown may leave orders open in MT5. Check terminal for stranded positions.

---

## 3. Incident Response

### 3A. Risk State Transitions - Incident Response

The system transitions through risk states based on loss limits:

```
┌─────────────────────────────────────────────────┐
│             NORMAL (trading active)             │
│  - Daily loss used: < 50% of daily limit      │
│  - Total loss used: < 50% of total limit      │
└──────────────────┬──────────────────────────────┘
                   │ (50% daily loss used)
                   ▼
┌─────────────────────────────────────────────────┐
│             CAUTION (reduced risk)              │
│  - Daily loss used: 50-100% of daily limit    │
│  - Reduce position sizes to 50% of normal     │
│  - Extended stop loss distances                │
│  - No new entries on reversal signals         │
└──────────────────┬──────────────────────────────┘
                   │ (Daily loss limit exceeded)
                   ▼
┌─────────────────────────────────────────────────┐
│          RISK OFF (no new entries)              │
│  - Daily loss limit breached                   │
│  - Close-only mode, no new trades             │
│  - Exit positions on favorable levels         │
│  - Wait for next trading day reset             │
└──────────────────┬──────────────────────────────┘
                   │ (Total loss limit exceeded)
                   ▼
┌─────────────────────────────────────────────────┐
│     HALT (system shutdown - FINAL)              │
│  - Total loss limit breached (> 10% capital) │
│  - Challenge FAILED                            │
│  - All positions must be closed manually      │
│  - System restart requires manual recovery    │
└─────────────────────────────────────────────────┘
```

#### Incident: System Enters CAUTION Mode

**Alert**: `event="risk_state_changed" old_state="NORMAL" new_state="CAUTION"`

**Immediate Actions** (5 minutes):

1. **Review Daily Performance**
   ```bash
   # Check daily PnL in logs
   tail -100 logs/trading.log | jq 'select(.event=="risk_state_changed")'
   
   # Review all trades today
   python scripts/replay_events.py \
     --store-dir data/events \
     --from-timestamp "$(date +%Y-%m-%dT00:00:00+02:00)" \
     --event-type OrderFilled
   ```

2. **Analyze Losing Trades**
   ```bash
   # Find largest losses
   python scripts/replay_events.py \
     --store-dir data/events \
     --event-type OrderFilled \
     --sort-by pnl \
     --limit 10
   ```

3. **Check Signal Quality**
   - Look for signal reversals or conflicting indicators
   - Verify ML model isn't in drift state
   - Check market conditions (news, volatility spikes)

4. **Take Corrective Action**
   - Do NOT change system configuration immediately
   - Monitor next 5-10 trades closely
   - If trend continues, consider stopping system for analysis

**Escalation** (if CAUTION persists > 2 hours):
- [ ] Alert senior trader/risk manager
- [ ] Increase monitoring frequency (every 15 min)
- [ ] Prepare for potential RISK OFF transition
- [ ] Prepare event store backup

#### Incident: System Enters RISK OFF Mode

**Alert**: `event="risk_state_changed" old_state="CAUTION" new_state="RISK_OFF"`

**CRITICAL**: Daily loss limit has been exceeded. System now operates in close-only mode.

**Immediate Actions** (1 minute):

1. **ACKNOWLEDGE INCIDENT**
   ```bash
   # Log incident timestamp
   echo "INCIDENT: RISK_OFF at $(date)" >> logs/incidents.log
   
   # Notify team (via PagerDuty, Slack, etc.)
   # SMS: "FTMO-MT5: RISK OFF mode triggered - daily loss exceeded"
   ```

2. **ASSESS OPEN POSITIONS**
   ```bash
   # View all open positions in MT5 terminal
   # Or query via system:
   python -c "
   import asyncio
   from ftmo_mt5.execution.mt5_gateway import MT5Gateway
   gw = MT5Gateway(...)
   positions = asyncio.run(gw.get_open_positions())
   for p in positions:
       print(f'{p.symbol}: {p.volume} @ {p.open_price}')
   "
   ```

3. **WAIT FOR SYSTEM RESET**
   - System remains in RISK_OFF for remainder of trading day
   - Next calendar day at 00:00 Prague time, daily limits reset
   - Monitor for NORMAL state transition in logs
   - Do NOT restart or modify system

4. **POST-INCIDENT REVIEW**
   Once system transitions back to NORMAL (next day):
   
   ```bash
   # Generate incident report
   python scripts/replay_events.py \
     --store-dir data/events \
     --from-timestamp "2026-04-10T00:00:00+02:00" \
     --to-timestamp "2026-04-10T23:59:59+02:00" \
     --format html \
     --output incident_2026_04_10.html
   ```

**Escalation** (if RISK OFF triggered 2+ times in week):
- [ ] Escalate to challenge support
- [ ] Review signal agent parameters
- [ ] Consider retraining ML models
- [ ] Reduce leverage or position sizes

#### Incident: System Enters HALT State

**CRITICAL ALERT**: `event="risk_state_changed" old_state="RISK_OFF" new_state="HALT" reason="total_loss_limit_exceeded"`

**STATUS**: Challenge has FAILED. Total account loss exceeds 10%.

**Immediate Actions** (system automatically):
1. All positions forced closed
2. No new orders accepted
3. System shutdown initiated
4. Event store locked

**Post-Incident** (manual steps required):

1. **DO NOT RESTART SYSTEM**
   ```bash
   # System will not start if HALT in event store
   # Manual recovery required
   ```

2. **ANALYZE FAILURE**
   ```bash
   # Export all events for analysis
   python scripts/replay_events.py \
     --store-dir data/events \
     --format csv \
     --output trading_session.csv
   
   # Review in spreadsheet
   # Calculate cumulative PnL
   # Identify root cause of losses
   ```

3. **RECOVERY OPTIONS**

   **Option A: Start New Challenge**
   ```bash
   # Backup event store
   cp -r data/events data/events.bak.$(date +%Y%m%d_%H%M%S)
   
   # Clear event store for new challenge
   rm -rf data/events/*
   
   # Update credentials and config for new account
   export MT5_LOGIN=new_account_number
   # ... other config ...
   
   # Restart system
   ftmo-mt5
   ```

   **Option B: Maintain Event Store (for forensics)**
   ```bash
   # Keep current event store for analysis
   # Manually edit event store to clear HALT status
   # WARNING: Only if you understand event store format
   # This is NOT recommended for production
   ```

**Escalation** (post-failure):
- [ ] Report failure to FTMO support
- [ ] Request new challenge account if applicable
- [ ] Conduct thorough post-mortem
- [ ] Review and improve signal agent logic
- [ ] Retrain ML models with new market data
- [ ] Paper trade new strategy before next challenge

### 3B. MT5 Disconnect - Incident Response

**Alert**: `event="mt5_connection_lost" error="..."`

The system lost connection to MT5 terminal.

**Immediate Actions** (30 seconds):

1. **Check MT5 Terminal**
   ```bash
   # Windows
   tasklist | find "terminal64.exe"
   
   # Linux (if using Wine)
   ps aux | grep terminal64
   ```

2. **Verify Network**
   ```bash
   ping broker_server.com
   # Should get responses
   
   # Check firewall
   netstat -an | grep :443
   ```

3. **Restart MT5**
   ```bash
   # Windows: Kill and restart
   taskkill /IM terminal64.exe
   Start "C:\Program Files\...\terminal64.exe"
   
   # Wait 10 seconds for MT5 to start
   sleep 10
   ```

4. **Check System Recovery**
   ```bash
   # System should automatically reconnect
   # Watch logs for:
   tail -f logs/trading.log | grep -E "reconnect|connected|mt5_gateway"
   
   # Should see: event="mt5_connection_recovered"
   ```

**If system does not recover** (> 60 seconds):

```bash
# Check event log for errors
tail -50 logs/trading.log | jq '.error'

# Stop system
kill -SIGINT $(cat logs/trading.pid)

# Restart from scratch
ftmo-mt5
```

**Escalation** (if MT5 disconnections > 3/day):
- [ ] Check VPS stability
- [ ] Verify network connection to broker
- [ ] Consider broker server change
- [ ] Review MT5 logs for errors

### 3C. Agent Restart Limit Exceeded

**Alert**: `event="agent_restart_limit_exceeded" agent="signal_agent" attempts=3`

An agent has crashed repeatedly and exceeded restart limit.

**System Response**:
- System enters HALT state
- No further recovery attempts
- All positions forced closed

**Root Cause Analysis**:

1. **Check Agent Logs**
   ```bash
   tail -100 logs/trading.log | jq 'select(.agent=="signal_agent")'
   ```

2. **Identify Exception**
   ```bash
   tail -50 logs/trading.log | jq '.error'
   # Should show the underlying exception
   ```

3. **Common Causes**
   - Signal generation timeout (data feed issue)
   - ML model prediction NaN/inf (bad input data)
   - Configuration parsing error
   - Memory exhaustion

4. **Recovery**
   ```bash
   # Stop system
   kill -SIGINT $(cat logs/trading.pid)
   
   # Fix configuration or code
   # Examples:
   # - Update YAML syntax in config file
   # - Retrain ML model
   # - Increase memory allocation
   
   # Clear event store HALT (if confidence level high)
   # OR start with fresh event store
   rm -rf data/events/*
   
   # Restart
   ftmo-mt5
   ```

---

## 4. Midnight Boundary Monitoring

The system automatically monitors the midnight boundary in Prague time (CET/CEST) and implements position flattening.

### How Midnight Boundary Works

**Schedule** (Prague Time, CET = UTC+1, CEST = UTC+2):
- Daily limits reset at 00:00:00 Prague time
- Pre-midnight check triggers at: 23:30 (or configured minutes before midnight)
- All positions must be closed before 00:00

**System Actions**:
1. At configured time (e.g., 23:30), system checks time until midnight
2. If `pre_midnight_flatten_minutes` setting is reached, system:
   - Stops accepting new signals
   - Closes all open positions at market price
   - Records midnight close events
   - Verifies all positions closed
3. After midnight:
   - Daily loss limit resets
   - Risk state resets to NORMAL (if not in RISK OFF/HALT)
   - System resumes normal trading

### Monitoring Checklist

**Throughout Trading Day**:

```bash
# Monitor remaining time to midnight
python -c "
from datetime import datetime
from zoneinfo import ZoneInfo

prague_tz = ZoneInfo('Europe/Prague')
now = datetime.now(prague_tz)
midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
if now.hour >= 0:
    midnight = midnight.replace(day=now.day + 1) if now.day < 31 else midnight.replace(month=now.month + 1, day=1)

remaining = (midnight - now).total_seconds() / 60
print(f'Remaining minutes until midnight: {remaining:.0f}')
"

# Watch system logs for pre-midnight checks
tail -f logs/trading.log | grep -E "midnight|pre_midnight|daily_reset"
```

**Before Midnight (Last 30 Minutes)**:

- [ ] Monitor position count (should remain stable or decrease)
- [ ] Watch for flatten orders in MT5 terminal
- [ ] Verify no new orders being submitted
- [ ] Check account balance is accurate

**At Midnight**:

```bash
# Expected log sequence:
# "event": "approaching_midnight", "minutes_until": 5
# "event": "approaching_midnight", "minutes_until": 1
# "event": "pre_midnight_flatten_triggered"
# "event": "position_closed" (multiple)
# "event": "daily_limits_reset"
# "event": "risk_state_changed", "new_state": "NORMAL"
```

- [ ] All positions closed by 00:01
- [ ] Daily loss used = 0%
- [ ] Risk state = NORMAL
- [ ] No error logs during transition

**Issue: Positions Still Open at Midnight**

If positions remain open after midnight:

```bash
# Check MT5 terminal for stranded positions
# Query via system
python -c "
import asyncio
from ftmo_mt5.execution.mt5_gateway import MT5Gateway
gw = MT5Gateway(...)
positions = asyncio.run(gw.get_open_positions())
print(f'Open positions: {len(positions)}')
for p in positions:
    print(f'  {p.symbol}: {p.volume} @ {p.open_price}')
"

# Check event store for open_position events
python scripts/replay_events.py \
  --store-dir data/events \
  --from-timestamp "$(date +%Y-%m-%dT23:50:00+02:00)" \
  --event-type "PositionClosed" \
  --limit 20
```

**If positions not closing**:
1. Check for slow MT5 response (network/broker issue)
2. Check for insufficient margin to close positions
3. Manually close positions in MT5 terminal
4. Increase `pre_midnight_flatten_minutes` in config

### DST Midnight Transition

During DST changes (March/October), midnight may shift by 1 hour. System handles this automatically:

- **Spring Forward (March, 02:00 → 03:00)**: Midnight actual time moves 1 hour later UTC
- **Fall Back (October, 03:00 → 02:00)**: Midnight actual time moves 1 hour earlier UTC

The system uses `zoneinfo.ZoneInfo("Europe/Prague")` which automatically handles DST. No manual intervention needed.

---

## 5. DST Transition Checklist

European Daylight Saving Time transitions happen:
- **Spring Forward**: Last Sunday of March, 02:00 → 03:00 CET → CEST
- **Fall Back**: Last Sunday of October, 03:00 → 02:00 CEST → CET

This affects midnight boundary calculations.

### 3 Weeks Before Transition

- [ ] Review DST transition date (e.g., 2026-03-29)
- [ ] Check that `zoneinfo` library is up-to-date: `pip install --upgrade pytz` (for backup)
- [ ] Plan transition test window (non-trading hours)
- [ ] Notify team of potential midnight delay/acceleration

### 1 Week Before Transition

- [ ] Verify Prague timezone database is current
  ```bash
  python -c "from zoneinfo import ZoneInfo; print(ZoneInfo('Europe/Prague'))"
  ```
- [ ] Backtest midnight boundary at transition time
- [ ] Review pre-midnight flatten configuration
- [ ] Prepare rollback plan if needed

### Transition Day

**Morning of Transition**:
- [ ] Monitor system health throughout day
- [ ] Increase log level to DEBUG (if desired for visibility)
- [ ] Have backup support staff on standby
- [ ] Monitor midnight more closely (may have system delay)

**During Transition** (When clock hits transition time):

Spring Forward (02:00 → 03:00):
```bash
# System time jumps forward 1 hour
# Pre-midnight check now triggers at UTC+2:20 (23:20 CEST)
# Watch logs carefully during this period

tail -f logs/trading.log | grep -E "midnight|risk_state"
```

Fall Back (03:00 → 02:00):
```bash
# System time moves back 1 hour
# Potential for duplicate timestamps (02:00-02:59 occurs twice)
# System should handle with event deduplication

tail -f logs/trading.log | grep -E "duplicate|event_id"
```

**After Transition**:
- [ ] Verify midnight boundary reset worked
- [ ] Check daily limits reset correctly
- [ ] Review event timestamps for continuity
- [ ] Confirm no duplicate event processing

**Post-Transition Checklist**:
- [ ] Daily reset event recorded with correct timezone offset
- [ ] No error logs during DST hour
- [ ] Positions correctly closed at new midnight boundary
- [ ] Event store timestamps consistent

---

## 6. Model Promotion Workflow

Deploying new ML models to production trading.

### Phase 1: Model Training (Offline)

```bash
# 1. Gather training data
python -m ftmo_mt5.ml.data_gatherer \
  --symbols EURUSD,GBPUSD,USDJPY \
  --period 2y \
  --output data/training.parquet

# 2. Train new model
python -m ftmo_mt5.ml.model_trainer \
  --data data/training.parquet \
  --output models/signal_model_v3.pkl \
  --epochs 100 \
  --validation-split 0.2

# 3. Evaluate model
python -m ftmo_mt5.ml.model_evaluator \
  --model models/signal_model_v3.pkl \
  --test-data data/test_2024.parquet \
  --metrics accuracy precision recall f1
```

### Phase 2: Paper Trading (Simulation)

```bash
# 4. Paper trade model for 7 days
python scripts/paper_trade.py \
  --model models/signal_model_v3.pkl \
  --config config/1step.yaml \
  --duration 7d \
  --output paper_trade_report.html

# 5. Verify paper trade performance
# - Sharpe ratio > baseline model
# - Max drawdown < 5%
# - Win rate > 50%
# - No regime-dependent behavior

# 6. Get sign-off
# - Senior trader reviews results
# - Approves for production deployment
```

### Phase 3: Deployment (Production)

```bash
# 7. Backup current model
cp models/signal_model_production.pkl \
   models/signal_model_production.pkl.bak.$(date +%Y%m%d_%H%M%S)

# 8. Deploy new model
python scripts/deploy_model.py \
  --model models/signal_model_v3.pkl \
  --target production \
  --gate promotion \
  --rollback-on-error

# 9. Verify deployment
# Wait 5-10 minutes and check:
curl -s http://localhost:8080/metrics | grep signal_model
# Should show new model version

tail -f logs/trading.log | grep -E "model_loaded|model_version"
# Should show: event="model_loaded" model_version="v3"
```

### Phase 4: Monitoring (Post-Deployment)

```bash
# 10. Monitor for model drift
# System automatically monitors via drift_detection.py

tail -f logs/trading.log | grep drift_detected

# 11. Compare performance to baseline
# Weekly review of:
#     - Signal accuracy vs historical
#     - Correlation to market regime
#     - Position holding times
#     - Win/loss ratios

# 12. Set alert thresholds
# If correlation < 0.85 vs training data, alert
# If accuracy drops > 10%, trigger retraining
```

### Phase 5: Rollback (If Issues)

```bash
# 13. If new model underperforms:
python scripts/rollback_model.py \
  --target production \
  --to-version backup

# 14. Analyze failure
python scripts/replay_events.py \
  --store-dir data/events \
  --from-timestamp "2026-04-05T00:00:00+02:00" \
  --model-version v3 \
  --format csv \
  --output failed_trades.csv

# 15. Investigate and retrain
```

---

## 7. Event Store Audit and Replay

### Verifying Event Store Integrity

```bash
# 1. Check event store directory
ls -lah data/events/
# Should show recent files (last 24h for active trading)

# 2. Verify file integrity
python scripts/verify_event_store.py --store-dir data/events

# 3. Count events
python scripts/replay_events.py \
  --store-dir data/events \
  --count-only

# 4. Check for gaps or errors
python scripts/replay_events.py \
  --store-dir data/events \
  --validate
```

### Exporting for Analysis

```bash
# Export all events to CSV
python scripts/replay_events.py \
  --store-dir data/events \
  --format csv \
  --output trading_session.csv

# Export specific date range
python scripts/replay_events.py \
  --store-dir data/events \
  --from-timestamp "2026-04-09T00:00:00+02:00" \
  --to-timestamp "2026-04-10T23:59:59+02:00" \
  --format csv \
  --output trading_2026_04_09-10.csv

# Export specific event types
python scripts/replay_events.py \
  --store-dir data/events \
  --event-types OrderSubmitted,OrderFilled,OrderRejected \
  --format csv \
  --output orders_only.csv

# Export by symbol
python scripts/replay_events.py \
  --store-dir data/events \
  --symbol EURUSD \
  --format csv \
  --output eurusd_trades.csv
```

### Forensic Analysis

```bash
# Calculate daily PnL
python -c "
import pandas as pd
df = pd.read_csv('trading_session.csv')
df['timestamp'] = pd.to_datetime(df['timestamp'])
df['date'] = df['timestamp'].dt.date
daily = df[df['event_type']=='OrderFilled'].groupby('date')['pnl'].sum()
print(daily)
print(f'Total PnL: {daily.sum()}')
"

# Find largest losing trades
python -c "
import pandas as pd
df = pd.read_csv('trading_session.csv')
losses = df[df['pnl'] < 0].nlargest(10, 'pnl')[['timestamp', 'symbol', 'pnl']]
print(losses)
"

# Identify problem signals
python -c "
import pandas as pd
df = pd.read_csv('trading_session.csv')
signals = df[df['event_type']=='SignalGenerated']
losses = df[df['event_type']=='OrderFilled'][df['pnl'] < 0]
problem_signals = signals.merge(losses, on='order_id')
print(problem_signals[['timestamp', 'symbol', 'signal_direction', 'pnl']])
"
```

---

## 8. Health Check Interpretation

### Endpoint: GET /health

Returns system health status.

**Response: 200 OK**
```json
{"status": "healthy"}
```
- All agents running
- MT5 connection stable
- Event store operational
- No risk breaches
- Trading can proceed

**Response: 503 Service Unavailable**
```json
{"status": "unhealthy"}
```
Possible causes:
- [ ] MT5 connection lost
- [ ] Agent crash/restart
- [ ] Event store write failure
- [ ] System in HALT state
- [ ] Memory/resource exhaustion

**Troubleshooting unhealthy system**:
```bash
# Check logs for errors
tail -50 logs/trading.log | jq '.level' | grep -E "ERROR|CRITICAL"

# Check individual agent status
tail -50 logs/trading.log | jq 'select(.agent)' | jq '.agent' | sort | uniq -c

# Restart if needed
kill -SIGTERM $(cat logs/trading.pid)
sleep 5
ftmo-mt5
```

### Endpoint: GET /ready

Returns trading readiness status.

**Response: 200 OK**
```json
{"status": "ready"}
```
- Market hours active
- All agents initialized
- MT5 logged in
- Risk state normal or caution
- Ready to accept signals

**Response: 503 Service Unavailable**
```json
{"status": "not_ready"}
```
Possible reasons:
- [ ] Outside market hours
- [ ] System initializing (< 10 seconds from start)
- [ ] Waiting for first market data
- [ ] System in RISK OFF or HALT mode
- [ ] MT5 not logged in

**Expected readiness timeline**:
- T+0s: System starts
- T+2s: Components initialized
- T+5s: MT5 connection established
- T+10s: First market data received
- T+15s: Ready for trading (status = ready)

### Endpoint: GET /metrics

Prometheus metrics in text format.

**Key Metrics**:

```
# Trading Activity
ftmo_orders_submitted_total            → Total orders sent to MT5
ftmo_orders_filled_total               → Orders successfully filled
ftmo_orders_rejected_total             → Orders rejected by MT5
ftmo_orders_cancelled_total            → Orders cancelled by system

# Position Management
ftmo_open_positions                    → Number of active positions
ftmo_positions_closed_total            → Total positions closed
ftmo_average_holding_time_seconds      → Avg seconds position held

# Account Status
ftmo_account_equity                    → Current account balance
ftmo_account_free_margin               → Available margin for trading
ftmo_account_used_margin_pct           → Margin usage percentage (0-100)

# Risk Management
ftmo_daily_pnl                         → Profit/loss for current day
ftmo_daily_loss_used_pct               → Daily loss limit used (0-100)
ftmo_total_loss_used_pct               → Total loss limit used (0-100)
ftmo_risk_state                        → Current risk state (NORMAL=1, CAUTION=2, RISK_OFF=3, HALT=4)

# System Health
ftmo_agent_restarts_total              → Agent restart count
ftmo_mt5_disconnections_total          → MT5 connection losses
ftmo_event_store_size_bytes            → Event store size on disk

# Signal Quality
ftmo_signal_accuracy_pct               → % of signals resulting in profit
ftmo_signal_correlation_to_market      → ML model correlation to test data
```

**Monitoring Metrics**:

```bash
# Watch real-time metrics
while true; do
  curl -s http://localhost:8080/metrics | grep -E "ftmo_orders_submitted|ftmo_open_positions|ftmo_daily_pnl"
  sleep 5
done

# Export to file for analysis
curl -s http://localhost:8080/metrics > metrics_$(date +%Y%m%d_%H%M%S).txt

# Parse specific metric
curl -s http://localhost:8080/metrics | grep "ftmo_account_equity" | head -1
```

---

## 9. Alert Routing and Escalation

### Alert Severity Levels

**CRITICAL** (Page immediately, max 2 min response):
- System HALT triggered
- Agent restart limit exceeded
- Total loss limit breached
- Event store write failure
- MT5 connection lost (> 5 min)

**HIGH** (Notify team, 15 min response):
- System enters RISK OFF mode
- System enters CAUTION mode
- Daily loss limit approaching (> 80% used)
- Model drift detected (< 0.90 correlation)
- Agent restart triggered (< limit)
- MT5 connection lost (< 5 min)

**MEDIUM** (Log and monitor, no page):
- High slippage (> 2 pips average)
- Order rejection rate elevated
- Midnight boundary issue
- Health check failing (auto-recover)

**LOW** (Log only):
- Normal trading activity
- Risk state transitions (within normal)
- Model performance metrics
- Event store maintenance

### Routing Rules

```yaml
# alerts.yaml
alert_rules:
  - name: system_halt
    severity: CRITICAL
    condition: event == "risk_state_changed" AND new_state == "HALT"
    route_to:
      - pagerduty: FTMO_CRITICAL
      - slack: #ftmo-trading-critical
      - email: trading-lead@company.com
      - sms: "+1234567890"

  - name: risk_off_mode
    severity: HIGH
    condition: event == "risk_state_changed" AND new_state == "RISK_OFF"
    route_to:
      - pagerduty: FTMO_HIGH
      - slack: #ftmo-trading-alerts
      - email: trading-team@company.com

  - name: mt5_disconnected
    severity: HIGH
    condition: event == "mt5_connection_lost" AND duration_seconds > 300
    route_to:
      - pagerduty: FTMO_HIGH
      - slack: #ftmo-trading-alerts

  - name: agent_restart
    severity: HIGH
    condition: event == "agent_restarted"
    route_to:
      - slack: #ftmo-trading-alerts
      - email: trading-engineers@company.com
```

### Escalation Sequence

**Tier 1** (On-call trader): 2 min response
- Monitor alerts
- Perform immediate diagnostics
- Take manual corrective action if needed

**Tier 2** (Senior trader): 5 min response
- Analyze root cause
- Approve emergency shutdown
- Coordinate with support

**Tier 3** (Engineering): 10 min response
- Review code/configuration
- Prepare fixes or rollbacks
- Deploy hotfixes

**Tier 4** (Management): 15 min response
- Coordinate with FTMO support
- Update stakeholders
- Plan post-incident review

### Setting Up Alert Integrations

#### PagerDuty

```bash
# Install integration
pip install pdpyras

# Configure
export PAGERDUTY_API_KEY=key
export PAGERDUTY_SERVICE_ID=service

# Test
python scripts/test_alert_integration.py --platform pagerduty
```

#### Slack

```bash
# Create Slack webhook
# Go to: https://api.slack.com/messaging/webhooks

export SLACK_WEBHOOK_CRITICAL=https://hooks.slack.com/services/...
export SLACK_WEBHOOK_ALERTS=https://hooks.slack.com/services/...

# Test
python scripts/test_alert_integration.py --platform slack
```

#### SMS (Twilio)

```bash
# Sign up for Twilio account
# Get credentials

export TWILIO_ACCOUNT_SID=sid
export TWILIO_AUTH_TOKEN=token
export TWILIO_PHONE_FROM=+1234567890
export TWILIO_PHONE_TO=+9876543210

# Test
python scripts/test_alert_integration.py --platform twilio
```

---

## Quick Reference

### Common Commands

```bash
# Start system
ftmo-mt5

# Stop system
kill -SIGTERM $(cat logs/trading.pid)

# Check health
curl http://localhost:8080/health

# Watch logs
tail -f logs/trading.log | jq '.'

# Check open positions
tail -50 logs/trading.log | jq 'select(.event=="order_filled")' | jq '.symbol, .action'

# Export events
python scripts/replay_events.py --store-dir data/events --format csv --output export.csv

# Verify event store
python scripts/verify_event_store.py --store-dir data/events

# Paper trade model
python scripts/paper_trade.py --model models/signal_model_v3.pkl --duration 7d

# Deploy model
python scripts/deploy_model.py --model models/signal_model_v3.pkl --target production
```

### Contact Information

- **On-Call Trader**: [Phone/Slack]
- **Senior Trader**: [Phone/Slack]
- **Engineering Team**: [Slack #ftmo-engineering]
- **FTMO Support**: [Email/Phone]
- **Operations Center**: [Phone 24/7]

### Documentation Links

- Main README: README.md
- Architecture Guide: CLAUDE.md
- Configuration Reference: config/README.md
- API Documentation: docs/api.md
- Event Schema: docs/events.md
