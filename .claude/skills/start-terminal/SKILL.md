---
name: start-terminal
description: Start, stop, or troubleshoot the FTMO_MT5 trading terminal (broker hub on :8600, MT5 bridge on :8500). Use when asked to run the app, start FTMO, open the terminal, or when MT5 shows disconnected or prices are 0.00.
---

# Start the terminal

## Layout

- **Windows VM:** MetaTrader 5 terminal + `mt5_api_server.py` (port 8500). Nothing else runs there.
- **Mac:** `scripts/vm_port_forward.py` (relays localhost:8500 → VM) + `broker_hub.py` (port 8600, the UI).

## Start order

1. VM: open MT5 and log in, then run `python mt5_api_server.py` (or `start_trading.bat`). This step is the user's — you can't reach the VM.
2. Mac, from the repo root: `./start.sh --stop` then `./start.sh`
3. Open http://localhost:8600

`./start.sh --bot` also starts the autonomous bot (`ftmo_mt5.main`). **Never use `--bot` unless the user explicitly asks** — it trades on its own and has never completed a verified trade cycle.

## Check health — don't trust the UI

The dashboard toast "Connected to MT5 bridge" has shown while the bridge was down. Verify directly:

```bash
curl -s --max-time 5 http://localhost:8500/api/health   # MT5 bridge
curl -s --max-time 5 http://localhost:8600/api/health   # hub
curl -s http://localhost:8600/api/brokers               # per-broker status
lsof -iTCP:8500 -sTCP:LISTEN -n -P; lsof -iTCP:8600 -sTCP:LISTEN -n -P
```

Logs: `logs/broker_hub.log`, `logs/vm_port_forward.log`.

## Common failures

| Symptom | Cause | Fix |
|---|---|---|
| `Connection refused` to :8500 in hub log | MT5 or `mt5_api_server.py` not running in VM | User starts them in VM |
| "address already in use" | Old process still bound | `./start.sh --stop`, wait, start again |
| Prices 0.00 on tastytrade | Sandbox feed is ~15 min delayed / market closed | Expected in sandbox |
| Webull offline | No `WEBULL_*` creds in `.env` | Expected unless configured |

## Report

Say which brokers are actually up (from `/api/brokers` and the logs), which are down and why, and the one action that fixes the most important one. Never place, modify, or close an order while checking.
