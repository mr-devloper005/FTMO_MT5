"""An index of every backtest this project has already run.

Two costs this removes, both measured rather than assumed:

  * Re-running work already done. ~4,000 results were computed across this
    project's research and written to sixteen JSON files and two CSVs, then
    never consulted again -- so every new question re-ran from scratch. The
    lab is deterministic on cached data, which makes a repeat run pure waste.
  * Re-reading results that say nothing. Of 500 graded verdicts, 471 were
    FAILED. Reading them individually cost tokens and taught nothing that the
    count alone didn't.

SQLite because it is in the standard library, indexed, and one file -- no new
dependency for a store this small.

The index is a cache of past work, not a source of truth. Yahoo data updates,
so a stored row records the exact bar window it was computed over
(`from_ts`/`to_ts`/`bars`); a lookup that matters should check those before
trusting the verdict rather than assuming a symbol's history is unchanged.
"""
from __future__ import annotations

import csv
import glob
import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO = Path(__file__).resolve().parents[1]
DB_PATH = Path(os.getenv("LAB_INDEX_DB", REPO / "study" / "lab" / "results_index.db"))

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    fingerprint      TEXT PRIMARY KEY,
    symbol           TEXT NOT NULL,
    timeframe        TEXT NOT NULL,
    strategy         TEXT NOT NULL,
    params_json      TEXT,
    bars             INTEGER,
    from_ts          INTEGER,
    to_ts            INTEGER,
    trailing_stop_atr REAL,
    trades           INTEGER,
    return_pct       REAL,
    bh_return_pct    REAL,
    vs_bh_pct        REAL,
    beats_bh         INTEGER,
    verdict          TEXT,
    source           TEXT,
    recorded_at      REAL
);
CREATE INDEX IF NOT EXISTS idx_stk ON runs (symbol, timeframe, strategy);
CREATE INDEX IF NOT EXISTS idx_verdict ON runs (verdict);
"""


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.executescript(_SCHEMA)
    return c


def fingerprint(symbol: str, timeframe: str, strategy: str, params: Optional[Dict],
                bars: Optional[int], from_ts: Optional[int], to_ts: Optional[int],
                trailing_stop_atr: Optional[float]) -> str:
    """Identity of a run: what was tested, how, and over exactly which bars.

    The bar window is part of it deliberately. The same spec over a different
    window is a different result, and pretending otherwise is how a stale
    number outlives the data it came from.
    """
    payload = json.dumps({
        "sym": symbol, "tf": timeframe, "strat": strategy,
        "params": {k: params[k] for k in sorted(params)} if params else {},
        "bars": bars, "from": from_ts, "to": to_ts, "trail": trailing_stop_atr,
    }, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:20]


def record(result: Dict[str, Any], strategy: Optional[str] = None,
           verdict: Optional[str] = None, source: str = "mcp",
           trailing_stop_atr: Optional[float] = None) -> Optional[str]:
    """Store one measure()/validate() result. Idempotent on fingerprint."""
    sym = result.get("symbol")
    tf = result.get("timeframe") or result.get("timeframe_label")
    strat = strategy or result.get("strategy")
    if not (sym and tf and strat):
        return None

    params = result.get("strategy_parameters") or None
    fp = fingerprint(sym, tf, strat, params, result.get("bars"),
                     result.get("from"), result.get("to"), trailing_stop_atr)
    with _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (fp, sym, tf, strat, json.dumps(params) if params else None,
             result.get("bars"), result.get("from"), result.get("to"),
             trailing_stop_atr, result.get("trades"), result.get("returnPct"),
             result.get("bhReturnPct"), result.get("vsBHPct"),
             1 if result.get("beatsBH") else 0, verdict, source, time.time()))
    return fp


def lookup(symbol: Optional[str] = None, timeframe: Optional[str] = None,
           strategy: Optional[str] = None, verdict: Optional[str] = None,
           beats_bh_only: bool = False, limit: int = 50) -> Dict[str, Any]:
    """What do we already know about this combination?

    Returns matching rows plus a verdict tally, so "we tested this 30 times and
    all 30 FAILED" costs one small answer instead of thirty rows.
    """
    where, args = [], []
    for col, val in (("symbol", symbol), ("timeframe", timeframe),
                     ("strategy", strategy), ("verdict", verdict)):
        if val:
            where.append(f"{col} = ?")
            args.append(val)
    if beats_bh_only:
        where.append("beats_bh = 1")
    clause = (" WHERE " + " AND ".join(where)) if where else ""

    with _conn() as c:
        total = c.execute(f"SELECT COUNT(*) FROM runs{clause}", args).fetchone()[0]
        tally = {r["verdict"] or "ungraded": r["n"] for r in c.execute(
            f"SELECT verdict, COUNT(*) AS n FROM runs{clause} GROUP BY verdict", args)}
        beat_n = c.execute(
            f"SELECT COUNT(*) FROM runs{clause}{' AND' if clause else ' WHERE'} beats_bh = 1",
            args).fetchone()[0]
        rows = [dict(r) for r in c.execute(
            f"SELECT symbol,timeframe,strategy,trades,return_pct,bh_return_pct,"
            f"vs_bh_pct,beats_bh,verdict,bars,source FROM runs{clause} "
            f"ORDER BY vs_bh_pct DESC LIMIT ?", args + [limit])]

    return {"tested": total > 0, "matchCount": total, "verdictTally": tally,
            "beatsBHCount": beat_n, "rows": rows,
            "note": ("Cached results from earlier runs. Each row records the bar "
                     "window it was computed over; re-run if the data has moved on.")}


def stats() -> Dict[str, Any]:
    with _conn() as c:
        total = c.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
        tally = {r["verdict"] or "ungraded": r["n"] for r in c.execute(
            "SELECT verdict, COUNT(*) AS n FROM runs GROUP BY verdict")}
        syms = c.execute("SELECT COUNT(DISTINCT symbol) FROM runs").fetchone()[0]
    return {"runs": total, "symbols": syms, "verdictTally": tally,
            "db": str(DB_PATH)}


# ---------------------------------------------------------------------------
# Ingesting what already exists
# ---------------------------------------------------------------------------

# A script that saved its results without a `strategy` field left the strategy
# only in its own filename. That omission is not hypothetical -- it broke a
# cross-reference during this project's research -- so the name is used as the
# fallback rather than dropping 154 graded verdicts on the floor.
_STRATEGY_FROM_FILENAME = {
    "mean_reversion": "MEAN_REVERSION",
    "hold_until_broken": "HOLD_UNTIL_BROKEN",
    "wide_trail": "HOLD_UNTIL_BROKEN",
}


def _strategy_for(path: str) -> Optional[str]:
    stem = os.path.basename(path).lower()
    for needle, name in _STRATEGY_FROM_FILENAME.items():
        if stem.startswith(needle):
            return name
    return None


def _unprefix_full(r: Dict) -> Dict:
    """Flatten the `fullTrades`/`fullVsBH`/... shape one script wrote."""
    return {
        "symbol": r.get("symbol"), "timeframe": r.get("timeframe"),
        "strategy": r.get("strategy"), "trades": r.get("fullTrades"),
        "returnPct": r.get("fullReturnPct"), "bhReturnPct": r.get("fullBhReturnPct"),
        "vsBHPct": r.get("fullVsBHPct"), "bars": r.get("bars"),
        "beatsBH": bool(r.get("fullBeatsBH", r.get("beatsBH", False))),
    }


def _rows_from_json(path: str) -> List[Dict]:
    try:
        data = json.load(open(path, encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    fallback = _strategy_for(path)
    out = []
    for r in data:
        if not isinstance(r, dict):
            continue
        # Three shapes appear in this project's saved output: validate()
        # records nesting metrics under "full", measure() records that are
        # flat, and one script's `full*`-prefixed variant.
        if "full" in r and isinstance(r["full"], dict):
            flat = dict(r["full"])
            flat.setdefault("symbol", r.get("symbol"))
            flat.setdefault("timeframe", r.get("timeframe"))
            out.append({"result": flat,
                        "strategy": r.get("strategy") or flat.get("strategy") or fallback,
                        "verdict": r.get("verdict"),
                        "trail": r.get("trailing_stop_atr")})
        elif "fullTrades" in r:
            flat = _unprefix_full(r)
            out.append({"result": flat, "strategy": flat.get("strategy") or fallback,
                        "verdict": r.get("verdict"), "trail": None})
        elif "returnPct" in r or "trades" in r:
            out.append({"result": r, "strategy": r.get("strategy") or fallback,
                        "verdict": r.get("verdict"), "trail": r.get("trail")})
    return out


def _rows_from_csv(path: str) -> List[Dict]:
    out = []
    try:
        for row in csv.DictReader(open(path, newline="", encoding="utf-8")):
            def num(k):
                try:
                    return float(row[k])
                except (KeyError, TypeError, ValueError):
                    return None
            res = {"symbol": row.get("symbol"), "timeframe": row.get("timeframe"),
                   "strategy": row.get("strategy"), "trades": int(num("trades") or 0),
                   "returnPct": num("returnPct"), "bhReturnPct": num("bhReturnPct"),
                   "vsBHPct": num("vsBHPct"),
                   "beatsBH": str(row.get("beatsBH", "")).lower() == "true",
                   "bars": int(num("bars") or 0)}
            if res["symbol"] and res["strategy"]:
                out.append({"result": res, "strategy": res["strategy"],
                            "verdict": None, "trail": None})
    except Exception:
        return []
    return out


def ingest_existing(verbose: bool = False) -> Dict[str, Any]:
    """Pull every result this project has already written into the index.

    Tolerant by design: the saved files come from a dozen ad-hoc scripts with
    several different shapes, and a file that can't be parsed is counted and
    skipped rather than aborting the ingest.
    """
    sources = sorted(glob.glob(str(REPO / "study" / "lab" / "*.json"))) + \
        sorted(glob.glob(str(REPO / "*.csv")))
    ingested, skipped, per_file = 0, 0, {}
    for path in sources:
        name = os.path.basename(path)
        if name == os.path.basename(str(DB_PATH)):
            continue
        rows = _rows_from_csv(path) if path.endswith(".csv") else _rows_from_json(path)
        n = 0
        for item in rows:
            if record(item["result"], strategy=item.get("strategy"),
                      verdict=item.get("verdict"), source=name,
                      trailing_stop_atr=item.get("trail")):
                n += 1
            else:
                skipped += 1
        ingested += n
        if n:
            per_file[name] = n
        if verbose:
            print(f"{name:<48} {n:>5} rows")
    return {"ingested": ingested, "skippedUnusable": skipped,
            "files": per_file, **stats()}


if __name__ == "__main__":
    print(json.dumps(ingest_existing(verbose=True), indent=2))
