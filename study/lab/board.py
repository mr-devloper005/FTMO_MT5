"""The running leaderboard. Every test ever run is appended here and kept."""
import json
from pathlib import Path

DB = Path(__file__).parent / "leaderboard.json"


def load():
    return json.loads(DB.read_text()) if DB.exists() else {"tests": []}


def add(**row):
    db = load()
    row["id"] = len(db["tests"]) + 1
    db["tests"].append(row)
    DB.write_text(json.dumps(db, indent=1))
    return row["id"]


def line(no, sym, tf, name, r):
    return (f"#{no:>3} {sym:<4}{tf:>4} {name:<34}{r['trades']:>5}tr"
            f"{r['net']:>11,.0f} B&H{r['bh']:>10,.0f}{r['vsBH']:>11,.0f}"
            f" PF{(r['pf'] or 0):>5.2f} DD{abs(r['maxDDPct']):>5.1f}%"
            f" TiM{r['timeInMarket']:>5.1f}%  {'BEAT' if r['beatsBH'] else ''}")
