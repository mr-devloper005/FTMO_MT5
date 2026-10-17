#!/usr/bin/env python3
"""Standalone health check for the broker hub — no imports from ftmo_mt5.

Run on a schedule (see scripts/com.ftmo.healthcheck.plist). Hits the hub's own
/api/brokers, since that already reflects real connectivity per broker rather
than just "is the port open" — a stuck bridge answers a TCP connect but not an
HTTP request in time, and a plain port check would call that healthy.

Alerts (macOS notification + stderr) only on a *change* in mt5's connected
state, so a genuinely down VM does not renotify every 5 minutes forever.
"""
import json
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HUB_URL = "http://127.0.0.1:8600/api/brokers"
STATE_FILE = Path(__file__).parent / ".health_check_state.json"
LOG_FILE = Path(__file__).parent.parent / "logs" / "health_check.log"
TIMEOUT_S = 10

# Brokers whose disconnection is worth waking someone up for. Webull is off by
# default (WEBULL_AUTOCONNECT=0), so its "not connected" is expected, not a fault.
ALERT_ON = {"mt5"}


def notify(title: str, message: str) -> None:
    try:
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{message}" with title "{title}" sound name "Basso"'],
            check=False, timeout=5,
        )
    except Exception:
        pass


def load_prev_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state), encoding="utf-8")


def log(line: str) -> None:
    LOG_FILE.parent.mkdir(exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def main() -> int:
    now = datetime.now(timezone.utc).isoformat()
    try:
        with urllib.request.urlopen(HUB_URL, timeout=TIMEOUT_S) as resp:
            brokers = json.loads(resp.read().decode())
    except Exception as e:
        log(f"{now} HUB_UNREACHABLE {e}")
        notify("FTMO Hub DOWN", f"broker_hub.py did not answer on :8600 — {e}")
        return 2

    prev = load_prev_state()
    cur_state = {}
    problems = []
    for b in brokers:
        key, connected, detail = b["key"], b["connected"], b.get("detail", "")
        cur_state[key] = connected
        if not connected and key in ALERT_ON:
            problems.append(f"{key}: {detail}")
        was_connected = prev.get(key)
        if was_connected is True and connected is False and key in ALERT_ON:
            notify(f"FTMO: {key} disconnected", detail[:200])
        if was_connected is False and connected is True and key in ALERT_ON:
            notify(f"FTMO: {key} reconnected", detail[:200])

    save_state(cur_state)
    status_line = " | ".join(f"{k}={'up' if v else 'DOWN'}" for k, v in cur_state.items())
    log(f"{now} {status_line}" + (f" -- PROBLEMS: {'; '.join(problems)}" if problems else ""))

    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
