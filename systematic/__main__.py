"""Command line for the paper-only systematic pipeline.

    python -m systematic research            # screen -> validate -> rank the watchlist
    python -m systematic cycle               # one paper-trading cycle (run after the US close)
    python -m systematic run                 # research, then a cycle
    python -m systematic status              # paper account, positions, halt state
    python -m systematic ui                  # web UI on http://127.0.0.1:8601
    python -m systematic reset-halt --by NAME --confirm   # a person clears a halt

Options: --watchlist FILE, --settings FILE, --state DIR, --min-grade GRADE
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import systematic
from systematic import report
from systematic.config import PROMOTION_FLOORS, load_ftmo_config, load_settings
from systematic.data import use_daily_cache
from systematic.universe import DEFAULT_WATCHLIST, load_watchlist

BANNER = "PAPER TRADING — simulated money only. Live trading: DISABLED (no live order path exists)."
STALE_RANKING_DAYS = 7


def _research(args, settings) -> dict:
    from systematic import research
    symbols = load_watchlist(Path(args.watchlist))
    cache = use_daily_cache(settings.state_dir)
    print(f"Researching {len(symbols)} symbols on {settings.timeframes} (data cache: {cache})")
    screened = research.screen(symbols, settings, progress=lambda m: print("  " + m))
    candidates = research.validate_candidates(screened, settings, progress=lambda m: print("  " + m))
    ranking = research.rank(candidates, settings)
    settings.state_dir.mkdir(parents=True, exist_ok=True)
    (settings.state_dir / "ranking.json").write_text(json.dumps(ranking, indent=2, default=str))
    path = report.write(settings.state_dir, "research", report.research_report(ranking, screened, symbols))
    print(f"\nGrades: {ranking['gradeTally']}")
    print(f"Promoted (floor {ranking['minGrade']}): " +
          (", ".join(f"{p['symbol']} {p['strategy']} [{p['verdict']}]" for p in ranking["promoted"]) or "none"))
    print(f"Report: {path}")
    return ranking


def _cycle(args, settings) -> dict:
    from systematic.trader import Cycle, load_ranking
    ranking = load_ranking(settings.state_dir)
    made = datetime.fromisoformat(ranking["generatedAt"])
    age = (datetime.now(timezone.utc) - made).days
    if age > STALE_RANKING_DAYS:
        print(f"WARNING: ranking is {age} days old; re-run `research` to refresh it.")
    use_daily_cache(settings.state_dir)
    result = asyncio.run(Cycle(settings, load_ftmo_config()).run(ranking))
    path = report.write(settings.state_dir, "cycle", report.cycle_report(result))
    print(f"Equity ${result['equity']} | risk state {result['riskState']} | "
          f"{len(result['positions'])} open | {len(result['actions'])} actions")
    for a in result["actions"]:
        print("  " + a["kind"] + " " + ", ".join(f"{k}={v}" for k, v in a.items() if k != "kind"))
    print(f"Report: {path}")
    return result


def _status(settings) -> None:
    ledger = settings.state_dir / "paper" / "ledger.json"
    if not ledger.exists():
        print("No paper account yet. Run `research`, then `cycle`.")
        return
    s = json.loads(ledger.read_text())
    last = (s["equity_history"] or [{}])[-1]
    print(f"Equity ${last.get('equity', s['initial_capital'])} | risk state {s['risk_state']} | "
          f"{len(s['positions'])} open | {len(s['trades'])} closed trades")
    if s.get("halted"):
        print(f"HALTED since {s['halted']['at']}: {s['halted']['reason']}")
    for sym, p in s["positions"].items():
        print(f"  {sym} {p['side']} {p['quantity']} @ {p['avg_price']} (last {p['last_price']}) — {p.get('strategy', '')}")


def _reset_halt(args, settings) -> None:
    if not (args.confirm and args.by):
        sys.exit("Refusing: clearing a halt needs --by NAME and --confirm, after a person has reviewed why it halted.")
    from systematic.paper_broker import PaperBroker
    from systematic.paper_broker import D
    cfg = load_ftmo_config()
    b = PaperBroker(settings.state_dir / "paper" / "ledger.json", cfg.initial_capital,
                    settings.commission(), settings.slippage(), D(settings.margin_rate))
    if not b.halted:
        print("Not halted; nothing to clear.")
        return
    print(f"Clearing halt ({b.halted['reason']}) on behalf of {args.by}.")
    b.clear_halt(args.by)
    b.save()


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="python -m systematic", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=["research", "cycle", "run", "status", "reset-halt", "ui"])
    p.add_argument("--port", type=int, default=8601, help="port for `ui`")
    p.add_argument("--watchlist", default=str(DEFAULT_WATCHLIST))
    p.add_argument("--settings", default=None)
    p.add_argument("--state", default=None, help="state directory (default systematic/state)")
    p.add_argument("--min-grade", choices=PROMOTION_FLOORS, default=None)
    p.add_argument("--by", default=None, help="who is clearing the halt")
    p.add_argument("--confirm", action="store_true")
    args = p.parse_args(argv)

    assert systematic.LIVE_TRADING_ENABLED is False
    print(BANNER)
    settings = load_settings(args.settings, min_grade=args.min_grade, state_dir=args.state)

    if args.command == "research":
        _research(args, settings)
    elif args.command == "cycle":
        _cycle(args, settings)
    elif args.command == "run":
        _research(args, settings)
        _cycle(args, settings)
    elif args.command == "status":
        _status(settings)
    elif args.command == "reset-halt":
        _reset_halt(args, settings)
    elif args.command == "ui":
        from systematic.webui import serve
        serve(port=args.port)


if __name__ == "__main__":
    main()
