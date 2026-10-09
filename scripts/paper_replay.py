#!/usr/bin/env python3
"""Replay the paper cycle day by day over real bars, to exercise the execution path.

    PYTHONPATH=. venv/bin/python3 scripts/paper_replay.py --state systematic/state-diversified --days 60

Why this exists: a live cycle only fills on a day when a promoted rule gives a
*fresh* signal, so the fill -> mark -> close path can sit unproven for weeks.
This drives the same `Cycle` — same PaperBroker, same RiskAgent, same EventStore
— over the last N trading days by feeding it a truncated view of real history.

What it proves: the execution plumbing (sizing, fills, marking, exits, risk
gating, the audit trail) works on real data.
What it does NOT prove: an edge. The promoted rules were validated on data that
includes this replay window, so the P&L here is look-ahead contaminated and is
not a forward test. Read it as a plumbing check, not a return.

Paper only: imports `systematic`, which has no live order path.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import shutil
from datetime import datetime, time, timezone
from pathlib import Path

import systematic
from systematic.config import load_ftmo_config, load_settings
from systematic.data import load_completed, trading_date, use_daily_cache
from systematic.trader import Cycle, load_ranking


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--state", required=True, help="state dir holding ranking.json")
    p.add_argument("--days", type=int, default=60)
    p.add_argument("--min-grade", default=None)
    p.add_argument("--settings", default=None,
                   help="MUST be the same settings research used — costs have to match")
    p.add_argument("--out", default=None, help="replay state dir (default <state>/replay)")
    args = p.parse_args()

    assert systematic.LIVE_TRADING_ENABLED is False
    print("PAPER REPLAY — simulated money only. Live trading: DISABLED.")

    src = Path(args.state)
    ranking = load_ranking(src)
    out = Path(args.out or src / "replay")
    if out.exists():
        shutil.rmtree(out)                      # a replay always starts from a clean account
    out.mkdir(parents=True)

    settings = load_settings(args.settings, min_grade=args.min_grade, state_dir=out)
    print(f"Costs: ${settings.commission()}/share/side, {settings.slippage()} tick(s) slippage"
          f"  (bars {settings.bars})")
    use_daily_cache(src)                        # reuse today's already-downloaded bars
    promoted = [x["symbol"] for x in ranking["promoted"]]
    print(f"Promoted: {', '.join(promoted) or 'none'} (floor {ranking['minGrade']}"
          f"{', EXPLORATORY' if ranking.get('exploratory') else ''})")
    if not promoted:
        return

    # The trading calendar comes from the data itself.
    full = {s: load_completed(s, "1d") for s in promoted}
    calendar = sorted({trading_date(c) for s in promoted for c in full[s]})[-args.days:]
    print(f"Replaying {len(calendar)} trading days: {calendar[0]} -> {calendar[-1]}\n")

    def fetch_asof(symbol, timeframe, bars=None, now=None):
        """Only bars that had already closed on the replay date."""
        asof = now.date()
        rows = [c for c in full[symbol] if trading_date(c) <= asof]
        return rows[-int(bars):] if bars else rows

    fills = closes = rejects = waits = 0
    for day in calendar:
        # after the close, so the day's own bar counts as completed
        now = datetime.combine(day, time(17, 0), tzinfo=timezone.utc)
        cycle = Cycle(settings, load_ftmo_config(), fetch=fetch_asof, now=now)
        if cycle.broker.halted:
            print(f"{day}  HALTED — stopping replay (only a person may clear a halt)")
            break
        result = asyncio.run(cycle.run(ranking))
        for a in result["actions"]:
            if a["kind"] == "OPEN":
                fills += 1
                print(f"{day}  OPEN  {a['symbol']:4} {a['side']:5} {a['quantity']:>5} @ {a['price']:>9} "
                      f"risk={a['riskState']} x{a['sizeMultiplier']}")
            elif a["kind"] == "CLOSE":
                closes += 1
                print(f"{day}  CLOSE {a['symbol']:4} {a['side']:5} {a['quantity']:>5} @ {a['price']:>9} "
                      f"pnl={a['pnl']:>10}  ({a['reason']})")
            elif a["kind"] == "REJECTED":
                rejects += 1
                print(f"{day}  REJECT {a['symbol']:4} {a['reason']}")
            elif a["kind"] == "WAIT":
                waits += 1
            elif a["kind"] in ("HALT", "RISK_STATE"):
                print(f"{day}  {a['kind']}: {a}")

    ledger = json.loads((out / "paper" / "ledger.json").read_text())
    last = (ledger["equity_history"] or [{}])[-1]
    equity = float(last.get("equity", ledger["initial_capital"]))
    start = float(ledger["initial_capital"])
    events = out / "paper" / "events"
    n_events = sum(1 for f in events.rglob("*.jsonl") for _ in f.open()) if events.exists() else 0
    print(f"\n--- replay summary (plumbing check, NOT a forward test) ---")
    print(f"fills {fills} · closes {closes} · rejections {rejects} · stale-signal waits {waits}")
    print(f"closed trades {len(ledger['trades'])} · commissions ${ledger['commissions']}")
    print(f"equity ${equity:,.2f} from ${start:,.2f}  ({(equity/start-1)*100:+.2f}%)")
    print(f"open positions {len(ledger['positions'])} · halted {bool(ledger['halted'])} · audit events {n_events}")
    print(f"state: {out}")


if __name__ == "__main__":
    main()
