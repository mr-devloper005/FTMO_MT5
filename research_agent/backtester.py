"""Parallel backtests on the project's own engine, deduplicated, in two phases.

    screen     one backtest per (stock, strategy, timeframe) on default parameters,
               on every timeframe in Settings.timeframes (config.TIMEFRAMES, the 10) —
               answers "does this beat buy & hold at all, and on which timeframe?"
    validated  the Lab's full robustness grading (verdict.validate: out-of-sample,
               cost ladder, top-trade removal, walk-forward, parameter sensitivity)
               for the best few strategy x timeframe combinations per stock

Nothing here fills an order or computes a return. ``metrics.measure`` and
``verdict.validate`` (ftmo_mt5/lab) run ``ftmo_mt5.backtest.run_backtest`` — the
same engine the panel, the MCP server and the systematic pipeline use. Costs
are US-stock costs from Settings and travel in the spec.

Every test is fingerprinted (stock, strategy, timeframe, parameters, bars,
data date, costs, phase). A fingerprint already in the store is returned, not
re-run. Daily fingerprints keep their pre-multi-timeframe form, so every daily
test already in the store still counts.
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import date
from typing import Any, Callable, Dict, List, Optional

from research_agent.config import DAILY, Settings
from research_agent.store import Store

Progress = Optional[Callable[[str], None]]
PHASES = ("screen", "validated")

# What a leaderboard row needs from measure(); the rest stays out of the store.
_KEEP = ("returnPct", "bhReturnPct", "vsBHPct", "beatPct", "beatsBH", "cagrPct", "bhCagrPct",
         "maxDDPct", "bhDDPct", "sharpe", "trades", "winRate", "pf", "topTradeShare",
         "timeInMarket", "windowYears", "net", "bh", "bars", "warmupBars", "from", "to")


MIN_BARS = 150        # below this a timeframe cannot warm up a 50-bar rule and still trade

def fingerprint(symbol: str, strategy: str, params: Dict, phase: str, as_of: str, settings: Settings,
                timeframe: str = DAILY) -> str:
    key = {"s": symbol, "k": strategy, "p": params or {}, "ph": phase, "d": as_of,
           "b": settings.history_bars(timeframe), "c": settings.cost_key()}
    if timeframe != DAILY:                       # daily keeps its original identity (see module doc)
        key["t"] = timeframe
    else:
        key["b"] = settings.bars
    return hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()[:24]


# ---------------------------------------------------------------- workers
def _init_worker(settings_dict: Dict) -> None:
    from systematic.data import use_daily_cache
    from research_agent import catalog
    s = Settings.from_dict(settings_dict)
    use_daily_cache(s.state_dir)
    catalog.install_all(s)                    # team + AI specs resolvable by name in this process


def _run_one(job: Dict, settings_dict: Dict) -> Dict:
    """One backtest (screen) or one full robustness grading (validated)."""
    from ftmo_mt5.lab import metrics, service, verdict
    from research_agent import bars
    from systematic.research import robustness_score, spec_for
    s = Settings.from_dict(settings_dict)
    tf = job["timeframe"]
    out = {k: job[k] for k in ("fingerprint", "symbol", "strategy", "params", "phase", "as_of", "timeframe")}
    t0 = time.time()
    try:
        candles = bars.load(job["symbol"], tf, s)
        if len(candles) < MIN_BARS:
            raise ValueError(f"only {len(candles)} completed {tf} bars")
        size = service.size_for(job["symbol"], candles, metrics.CAPITAL)
        spec = spec_for(job["strategy"], job["params"], s, size)
        if job["phase"] == "screen":
            full, grade, why, extra = metrics.measure(job["symbol"], tf, spec, candles), None, [], {}
        else:
            v = verdict.validate(job["symbol"], tf, spec, candles)
            full, grade, why = v["full"], v["verdict"], v.get("why", [])
            extra = {"oosVsBHPct": v["oos"]["vsBHPct"], "oosBeatsBH": bool(v["oos"]["beatsBH"]),
                     "oosReturnPct": v["oos"]["returnPct"], "oosBHReturnPct": v["oos"]["bhReturnPct"],
                     "foldsWon": v["foldsWon"], "foldsRun": v["foldsRun"],
                     "survivesTicks": v["survivesTicks"], "variationsHeldPct": v["variationsHeldPct"],
                     "paramKey": v.get("paramKey"),
                     "survivesTop3Removal": bool(v["gates"].get("survivesTop3Removal"))}
        m = {k: full.get(k) for k in _KEEP}
        m.update(extra, positionSize=size, strategy_parameters=dict(job["params"] or {}))
        if grade:
            m["robustnessScore"] = robustness_score({**m, "sharpe": m.get("sharpe") or 0.0,
                                                     "maxDDPct": m.get("maxDDPct") or 0.0})
        out.update(metrics=m, grade=grade, why=why, bars=len(candles), error=None)
        # Feed the project-wide results index too (best effort, same as systematic/).
        out["_index"] = {**full, "strategy_parameters": dict(job["params"] or {})}
    except Exception as e:                                       # noqa: BLE001
        out.update(metrics={}, grade=None, why=[], error=f"{type(e).__name__}: {str(e)[:200]}")
    out["seconds"] = round(time.time() - t0, 2)
    return out


def _index(row: Dict) -> None:
    try:
        from mcp_server import results_index
        results_index.record(row["_index"], strategy=row["strategy"], verdict=row.get("grade"),
                             source="research_agent")
    except Exception:                                            # noqa: BLE001
        pass


# ---------------------------------------------------------------- runner
def plan(settings: Settings, pairs: List[Dict], phase: str) -> List[Dict]:
    """Turn (symbol, strategy, params[, timeframe]) into fingerprinted jobs.

    A pair without a timeframe runs on every configured timeframe — that is the
    normal path. A pair that names one (validation picks) runs on that one only.
    """
    if phase not in PHASES:
        raise ValueError(f"phase must be one of {PHASES}")
    store = Store(settings.db_path)
    jobs = []
    for p in pairs:
        scr = store.screen(p["symbol"]) or {}
        as_of = scr.get("as_of") or date.today().isoformat()
        params = dict(p.get("params") or {})
        tfs = [p["timeframe"]] if p.get("timeframe") else list(settings.timeframes)
        bad = [tf for tf in tfs if tf not in settings.timeframes]
        if bad:
            raise ValueError(f"timeframe {bad} is not one of the configured {list(settings.timeframes)}")
        for tf in tfs:
            jobs.append({"symbol": p["symbol"], "strategy": p["strategy"], "params": params, "phase": phase,
                         "as_of": as_of, "timeframe": tf,
                         "fingerprint": fingerprint(p["symbol"], p["strategy"], params, phase, as_of, settings, tf)})
    return jobs


def run(settings: Settings, pairs: List[Dict], phase: str = "screen", run_id: Optional[str] = None,
        progress: Progress = None) -> Dict[str, Any]:
    """Run every job not already in the store, in parallel. Returns counts and the run id."""
    settings.ensure_dirs()
    store = Store(settings.db_path)
    run_id = run_id or f"bt-{uuid.uuid4().hex[:8]}"
    jobs = plan(settings, pairs, phase)
    seen, todo = set(), []
    for j in jobs:                                   # dedupe within the batch and against history
        if j["fingerprint"] in seen:
            continue
        seen.add(j["fingerprint"])
        if not store.has_test(j["fingerprint"]):
            todo.append(j)
    skipped = len(seen) - len(todo)
    done, errors, t0 = 0, 0, time.time()
    if todo:
        with ProcessPoolExecutor(max_workers=min(settings.workers, len(todo)), initializer=_init_worker,
                                 initargs=(settings.to_dict(),)) as ex:
            futs = [ex.submit(_run_one, j, settings.to_dict()) for j in todo]
            for f in as_completed(futs):
                row = f.result()
                done += 1
                if row["error"]:
                    errors += 1                      # errors are not stored, so they retry next time
                else:
                    row["run_id"] = run_id
                    store.save_test(row)
                    _index(row)
                if progress and (done % 25 == 0 or done == len(todo)):
                    progress(f"{phase}: {done}/{len(todo)} run · {skipped} already known · {errors} errors")
    return {"run_id": run_id, "phase": phase, "requested": len(jobs), "unique": len(seen),
            "ran": done - errors, "skipped_known": skipped, "errors": errors,
            "seconds": round(time.time() - t0, 1),
            "fingerprints": [j["fingerprint"] for j in jobs]}


def pick_for_validation(settings: Settings, symbols: List[str], top_n: Optional[int] = None) -> List[Dict]:
    """Per stock, the best strategy x timeframe screen results worth the full robustness test.

    All timeframes compete for the same top-N slots. Order: beats buy & hold
    first, then outperformance. Rules with too few trades are skipped — the
    grader would fail them on trade count anyway.
    """
    store = Store(settings.db_path)
    n = top_n or settings.validate_top_n
    out = []
    for sym in symbols:
        as_of = (store.screen(sym) or {}).get("as_of")
        rows = [r for r in store.tests(symbol=sym, phase="screen")
                if (r["metrics"].get("trades") or 0) >= settings.min_trades
                and (as_of is None or r["as_of"] == as_of)]          # current data only
        rows.sort(key=lambda r: (not r["metrics"].get("beatsBH"), -(r["metrics"].get("vsBHPct") or -1e9)))
        latest = {}
        for r in rows:                               # one row per strategy x timeframe
            if r["timeframe"] in settings.timeframes:
                latest.setdefault((r["strategy"], r["timeframe"]), r)
        out += [{"symbol": sym, "strategy": r["strategy"], "params": r["params"], "timeframe": r["timeframe"]}
                for r in list(latest.values())[:n]]
    return out
