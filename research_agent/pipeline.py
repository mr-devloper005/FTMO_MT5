"""The research workflow as plain functions — the CLI runs it, the agent's tools wrap it.

    NASDAQ listing -> parallel screen -> shortlist -> strategies (existing + AI)
    -> screen-phase backtests -> validate the best -> verdicts -> leaderboard -> export

Every step is deterministic and resumable: the universe diffs, screening skips
what is fresh today, and backtests skip what is already in the store. Running
it twice costs almost nothing the second time.
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from research_agent import backtester, catalog, export, ranking, screener, universe
from research_agent.config import Settings
from research_agent.store import Store

Progress = Optional[Callable[[str], None]]


def new_run_id(kind: str) -> str:
    return f"{kind}-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}"


def ensure_universe(settings: Settings, max_age_hours: float = 20.0) -> Dict[str, Any]:
    """Refresh the NASDAQ listing when it is missing or older than a trading day."""
    store = Store(settings.db_path)
    with store.conn() as c:
        last = c.execute("SELECT MAX(last_seen) FROM universe").fetchone()[0]
    if last:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds() / 3600
        if age < max_age_hours:
            return {"refreshed": False, "active": len(store.active_symbols()), "age_hours": round(age, 1)}
    return {"refreshed": True, **universe.refresh(settings)}


def test_strategies(settings: Settings, symbols: List[str], strategy_names: Optional[List[str]] = None,
                    run_id: Optional[str] = None, progress: Progress = None) -> Dict[str, Any]:
    """Screen-phase backtests: every chosen strategy on its defaults, on every symbol."""
    cat = {s["name"]: s for s in catalog.catalog(settings)}
    names = strategy_names or list(cat)
    unknown = [n for n in names if n not in cat]
    if unknown:
        raise KeyError(f"unknown strategies: {unknown}")
    pairs = [{"symbol": sym, "strategy": n, "params": cat[n]["params"]} for sym in symbols for n in names]
    return backtester.run(settings, pairs, "screen", run_id=run_id, progress=progress)


def validate_best(settings: Settings, symbols: List[str], top_n: Optional[int] = None,
                  run_id: Optional[str] = None, progress: Progress = None) -> Dict[str, Any]:
    picked = backtester.pick_for_validation(settings, symbols, top_n)
    res = backtester.run(settings, picked, "validated", run_id=run_id, progress=progress)
    res["validated_pairs"] = [(p["symbol"], p["strategy"], p["timeframe"]) for p in picked]
    return res


def research(settings: Settings, screen_limit: int = 0, shortlist_size: Optional[int] = None,
             strategy_names: Optional[List[str]] = None, export_name: str = "leaderboard",
             progress: Progress = None) -> Dict[str, Any]:
    """The whole workflow without an LLM: existing strategies only."""
    log = progress or (lambda m: None)
    run_id = new_run_id("research")
    store = Store(settings.db_path)
    store.start_run(run_id, "research")
    t0 = time.time()
    catalog.sync(settings)
    out: Dict[str, Any] = {"run_id": run_id}
    out["universe"] = ensure_universe(settings)
    log(f"universe: {out['universe']}")
    out["screen"] = screener.run(settings, limit=screen_limit, progress=log)
    log(f"screen: {out['screen']}")
    short = [r["symbol"] for r in screener.shortlist(settings, shortlist_size)]
    out["shortlist"] = short
    log(f"shortlist ({len(short)}): {', '.join(short)}")
    out["backtests"] = {k: v for k, v in test_strategies(settings, short, strategy_names, run_id, log).items()
                        if k != "fingerprints"}
    out["validation"] = {k: v for k, v in validate_best(settings, short, run_id=run_id, progress=log).items()
                         if k != "fingerprints"}
    rows = ranking.leaderboard(settings, limit=1000, symbols=short)
    out["summary"] = ranking.summary(rows)
    out["exports"] = export.export(settings, rows, out["summary"], name=export_name)
    out["seconds"] = round(time.time() - t0, 1)
    store.finish_run(run_id, out)
    return out
