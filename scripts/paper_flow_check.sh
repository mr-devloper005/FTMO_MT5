#!/usr/bin/env bash
#
# End-to-end PAPER trade validation: mode check -> order -> fill -> close.
#
#   ./scripts/paper_flow_check.sh [broker] [symbol] [qty]
#   ./scripts/paper_flow_check.sh tasty AAPL 1        (defaults)
#
# Refuses to send anything unless the broker self-reports demo/sandbox.
# US equity markets must be open; the hub sends market orders only, so a
# closed market returns tif_no_after_hours_opening_market_orders.

set -uo pipefail

BROKER=${1:-tasty}
SYMBOL=${2:-AAPL}
QTY=${3:-1}
HUB=http://localhost:8600

red()   { printf '\033[31m%s\033[0m\n' "$1"; }
green() { printf '\033[32m%s\033[0m\n' "$1"; }

# --- 1. the guard: prove this is simulated money ---------------------------
status=$(curl -s --max-time 10 "$HUB/api/brokers") || { red "hub not reachable at $HUB"; exit 1; }
detail=$(printf '%s' "$status" | python3 -c "
import json,sys
b=[x for x in json.load(sys.stdin) if x['key']=='$BROKER']
print(b[0]['detail'] if b else '', end='')
print('|'+str(b[0]['connected']) if b else '|False', end='')
")
mode=${detail%%|*}; connected=${detail##*|}

if [ "$connected" != "True" ]; then red "$BROKER not connected: $mode"; exit 1; fi
if ! printf '%s' "$mode" | grep -qiE 'sandbox|demo|paper'; then
  red "REFUSING: '$BROKER' does not self-report demo/sandbox."
  red "  reported: $mode"
  exit 1
fi
green "paper mode confirmed: $mode"

# --- 2. before ------------------------------------------------------------
echo "positions before: $(curl -s --max-time 15 "$HUB/api/$BROKER/positions")"

# --- 3. open --------------------------------------------------------------
echo "placing PAPER order: buy $QTY $SYMBOL"
resp=$(curl -s --max-time 30 -X POST "$HUB/api/$BROKER/order" \
  -H 'Content-Type: application/json' \
  -d "{\"symbol\":\"$SYMBOL\",\"side\":\"buy\",\"volume\":$QTY,\"sl\":0,\"tp\":0}")
echo "  -> $resp"
if printf '%s' "$resp" | grep -q '"success":false'; then
  red "order rejected — market closed, or see comment above. Nothing to clean up."
  exit 2
fi

# --- 4. fill --------------------------------------------------------------
for _ in $(seq 1 10); do
  pos=$(curl -s --max-time 15 "$HUB/api/$BROKER/positions")
  printf '%s' "$pos" | grep -q "$SYMBOL" && break
  sleep 2
done
echo "positions after open: $pos"
printf '%s' "$pos" | grep -q "$SYMBOL" || { red "no position appeared for $SYMBOL"; exit 3; }
green "fill confirmed"

# --- 5. close -------------------------------------------------------------
ticket=$(printf '%s' "$pos" | python3 -c "
import json,sys
p=[x for x in json.load(sys.stdin) if x.get('symbol')=='$SYMBOL']
print(p[0].get('ticket','') if p else '', end='')
")
[ -n "$ticket" ] || { red "position has no ticket — close by hand"; exit 4; }
echo "closing ticket $ticket"
curl -s --max-time 30 -X POST "$HUB/api/$BROKER/close/$ticket"; echo
sleep 3
echo "positions after close: $(curl -s --max-time 15 "$HUB/api/$BROKER/positions")"
green "end-to-end paper flow complete"
