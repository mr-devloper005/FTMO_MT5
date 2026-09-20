#!/usr/bin/env bash
#
# Start everything that runs on the Mac, in the right order.
#
#   ./start.sh          port forwarder + broker hub  (manual trading)
#   ./start.sh --bot    the above plus the autonomous bot
#   ./start.sh --stop   stop everything
#
# The MT5 side is NOT started here: MetaTrader 5 only speaks to Windows, so the
# terminal and mt5_api_server.py live in the VM and have to be running there
# first. This script checks for them and says so rather than failing obscurely.

set -uo pipefail
cd "$(dirname "$0")"

PY=./venv/bin/python
HUB_PORT=8600
BRIDGE_PORT=8500
LOG_DIR=logs

green() { printf '\033[32m%s\033[0m\n' "$1"; }
warn()  { printf '\033[33m%s\033[0m\n' "$1"; }
red()   { printf '\033[31m%s\033[0m\n' "$1"; }

# Killing a process frees its socket a moment later; starting the replacement
# immediately is what produced the recurring "address already in use".
wait_for_port_free() {
  local port=$1 tries=0
  while lsof -iTCP:"$port" -sTCP:LISTEN -n -P >/dev/null 2>&1; do
    tries=$((tries + 1))
    [ "$tries" -gt 20 ] && { red "port $port still busy after 10s"; return 1; }
    sleep 0.5
  done
  return 0
}

stop_all() {
  for name in "ftmo_mt5.main" broker_hub vm_port_forward; do
    if pgrep -f "$name" >/dev/null; then
      pkill -f "$name" && green "stopped $name"
    fi
  done
  wait_for_port_free "$HUB_PORT"
  wait_for_port_free "$BRIDGE_PORT"
}

start_one() {                       # name, port, command…
  local name=$1 port=$2; shift 2
  if pgrep -f "$name" >/dev/null; then
    warn "$name already running — leaving it alone"
    return 0
  fi
  [ "$port" != "-" ] && { wait_for_port_free "$port" || return 1; }
  mkdir -p "$LOG_DIR"
  nohup "$@" >"$LOG_DIR/$name.log" 2>&1 &
  sleep 1
  if pgrep -f "$name" >/dev/null; then
    green "started $name  (log: $LOG_DIR/$name.log)"
  else
    red "$name failed to start — see $LOG_DIR/$name.log"
    return 1
  fi
}

case "${1:-}" in
  --stop)
    stop_all
    exit 0
    ;;
esac

echo "── FTMO_MT5 ──"

start_one vm_port_forward "$BRIDGE_PORT" $PY scripts/vm_port_forward.py

# The forwarder only relays; if nothing answers behind it, MT5 is down in the VM.
sleep 1
if curl -s --max-time 5 "http://localhost:$BRIDGE_PORT/api/health" >/dev/null 2>&1; then
  green "MT5 bridge reachable"
else
  warn "MT5 bridge NOT reachable — start MetaTrader 5 and mt5_api_server.py in the VM."
  warn "  tastytrade will still work; MT5 will show as down."
fi

start_one broker_hub "$HUB_PORT" $PY broker_hub.py

if [ "${1:-}" = "--bot" ]; then
  start_one "ftmo_mt5.main" - $PY -m ftmo_mt5.main
else
  echo "   (bot not started — pass --bot to run it)"
fi

# The hub answers only once its brokers have been probed, and a browser opened
# before that shows a dead page. Wait for it, then open — but never block the
# script on a broker that is down.
printf '   waiting for the terminal'
for _ in $(seq 1 40); do
  if curl -s -o /dev/null --max-time 2 "http://localhost:$HUB_PORT/"; then
    ready=1; break
  fi
  printf '.'
  sleep 2
done
echo

if [ "${ready:-}" = "1" ]; then
  green "Terminal ready: http://localhost:$HUB_PORT"
  [ "${NO_OPEN:-}" = "1" ] || open "http://localhost:$HUB_PORT" 2>/dev/null
else
  red "Terminal did not answer in 80s — see $LOG_DIR/broker_hub.log"
fi

echo "   stop everything:  ./start.sh --stop"
