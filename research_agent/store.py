"""SQLite store: the research agent's memory.

Five tables. ``tests`` is keyed by a fingerprint of everything that decides a
result (stock, strategy, parameters, bars, data date, costs, phase), so a test
that has already run is looked up, not re-run.

One writer at a time: worker processes compute, the parent writes.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional

from research_agent.config import DAILY

SCHEMA = """
CREATE TABLE IF NOT EXISTS universe (
    symbol TEXT PRIMARY KEY, name TEXT, market_category TEXT,
    first_seen TEXT, last_seen TEXT, active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS screens (
    symbol TEXT PRIMARY KEY, screened_at TEXT, as_of TEXT, passed INTEGER,
    score REAL, price REAL, dollar_volume REAL, atr_pct REAL, history_bars INTEGER,
    trend_up INTEGER, return_1y REAL, gap_p95 REAL, reason TEXT, error TEXT
);
CREATE TABLE IF NOT EXISTS strategies (
    name TEXT PRIMARY KEY, kind TEXT NOT NULL, spec_json TEXT, params_json TEXT,
    rationale TEXT, author TEXT, created_at TEXT, status TEXT NOT NULL DEFAULT 'active'
);
CREATE TABLE IF NOT EXISTS tests (
    fingerprint TEXT PRIMARY KEY, symbol TEXT NOT NULL, strategy TEXT NOT NULL,
    params_json TEXT, phase TEXT NOT NULL, as_of TEXT, bars INTEGER,
    metrics_json TEXT NOT NULL, grade TEXT, why_json TEXT, run_id TEXT, created_at TEXT,
    timeframe TEXT NOT NULL DEFAULT '__DAILY__'
);
CREATE INDEX IF NOT EXISTS tests_symbol ON tests(symbol);
CREATE INDEX IF NOT EXISTS tests_strategy ON tests(strategy);
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY, kind TEXT, started_at TEXT, finished_at TEXT, summary_json TEXT
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.conn() as c:
            c.executescript(SCHEMA.replace("__DAILY__", DAILY))
            # Migration: tests recorded before multi-timeframe research were all daily.
            cols = {r[1] for r in c.execute("PRAGMA table_info(tests)")}
            if "timeframe" not in cols:
                c.execute(f"ALTER TABLE tests ADD COLUMN timeframe TEXT NOT NULL DEFAULT '{DAILY}'")
            c.execute("CREATE INDEX IF NOT EXISTS tests_timeframe ON tests(timeframe)")

    @contextmanager
    def conn(self) -> Iterator[sqlite3.Connection]:
        c = sqlite3.connect(self.path, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        try:
            yield c
            c.commit()
        finally:
            c.close()

    # ---- universe --------------------------------------------------------
    def upsert_universe(self, rows: Iterable[Dict[str, Any]]) -> Dict[str, int]:
        """Mark the listing's symbols active and everything else inactive."""
        rows = list(rows)
        stamp = now()
        listed = {r["symbol"] for r in rows}
        with self.conn() as c:
            before = {r["symbol"]: r["active"] for r in c.execute("SELECT symbol, active FROM universe")}
            for r in rows:
                c.execute("""INSERT INTO universe(symbol, name, market_category, first_seen, last_seen, active)
                             VALUES(?,?,?,?,?,1)
                             ON CONFLICT(symbol) DO UPDATE SET name=excluded.name,
                               market_category=excluded.market_category, last_seen=excluded.last_seen, active=1""",
                          (r["symbol"], r.get("name"), r.get("market_category"), stamp, stamp))
            gone = [s for s, a in before.items() if a and s not in listed]
            for s in gone:
                c.execute("UPDATE universe SET active=0 WHERE symbol=?", (s,))
        added = [s for s in listed if s not in before or not before[s]]
        return {"active": len(listed), "added": len(added), "removed": len(gone)}

    def active_symbols(self) -> List[str]:
        with self.conn() as c:
            return [r[0] for r in c.execute("SELECT symbol FROM universe WHERE active=1 ORDER BY symbol")]

    def universe_name(self, symbol: str) -> Optional[str]:
        with self.conn() as c:
            r = c.execute("SELECT name FROM universe WHERE symbol=?", (symbol,)).fetchone()
            return r[0] if r else None

    # ---- screens ---------------------------------------------------------
    def save_screen(self, row: Dict[str, Any]) -> None:
        cols = ["symbol", "screened_at", "as_of", "passed", "score", "price", "dollar_volume", "atr_pct",
                "history_bars", "trend_up", "return_1y", "gap_p95", "reason", "error"]
        with self.conn() as c:
            c.execute(f"INSERT OR REPLACE INTO screens({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
                      [row.get(k) for k in cols])

    def screened_on(self, day: str) -> set:
        """Symbols already screened on a given date (UTC date prefix of screened_at)."""
        with self.conn() as c:
            return {r[0] for r in c.execute("SELECT symbol FROM screens WHERE substr(screened_at,1,10)=?", (day,))}

    def screen(self, symbol: str) -> Optional[Dict[str, Any]]:
        with self.conn() as c:
            r = c.execute("SELECT * FROM screens WHERE symbol=?", (symbol,)).fetchone()
            return dict(r) if r else None

    def shortlist(self, limit: int) -> List[Dict[str, Any]]:
        with self.conn() as c:
            return [dict(r) for r in c.execute(
                "SELECT * FROM screens WHERE passed=1 ORDER BY score DESC LIMIT ?", (limit,))]

    def screen_counts(self) -> Dict[str, int]:
        with self.conn() as c:
            total, passed, errors = c.execute(
                "SELECT COUNT(*), SUM(passed=1), SUM(error IS NOT NULL) FROM screens").fetchone()
            return {"screened": total or 0, "passed": passed or 0, "errors": errors or 0}

    # ---- strategies ------------------------------------------------------
    def save_strategy(self, name: str, kind: str, spec: Optional[Dict], params: Optional[Dict],
                      rationale: str, author: str, status: str = "active") -> None:
        with self.conn() as c:
            c.execute("""INSERT INTO strategies(name, kind, spec_json, params_json, rationale, author, created_at, status)
                         VALUES(?,?,?,?,?,?,?,?)
                         ON CONFLICT(name) DO UPDATE SET status=excluded.status""",
                      (name, kind, json.dumps(spec) if spec else None, json.dumps(params) if params else None,
                       rationale, author, now(), status))

    def strategies(self, kinds: Optional[List[str]] = None, status: str = "active") -> List[Dict[str, Any]]:
        q, args = "SELECT * FROM strategies WHERE status=?", [status]
        if kinds:
            q += f" AND kind IN ({','.join('?' * len(kinds))})"
            args += kinds
        with self.conn() as c:
            out = []
            for r in c.execute(q + " ORDER BY created_at", args):
                d = dict(r)
                d["spec"] = json.loads(d.pop("spec_json")) if d.get("spec_json") else None
                d["params"] = json.loads(d.pop("params_json")) if d.get("params_json") else None
                out.append(d)
            return out

    # ---- tests -----------------------------------------------------------
    def has_test(self, fingerprint: str) -> bool:
        with self.conn() as c:
            return c.execute("SELECT 1 FROM tests WHERE fingerprint=?", (fingerprint,)).fetchone() is not None

    def save_test(self, row: Dict[str, Any]) -> None:
        with self.conn() as c:
            c.execute("""INSERT OR REPLACE INTO tests(fingerprint, symbol, strategy, params_json, phase, as_of, bars,
                                                      metrics_json, grade, why_json, run_id, created_at, timeframe)
                         VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                      (row["fingerprint"], row["symbol"], row["strategy"], json.dumps(row.get("params") or {}),
                       row["phase"], row.get("as_of"), row.get("bars"), json.dumps(row["metrics"]),
                       row.get("grade"), json.dumps(row.get("why") or []), row.get("run_id"), now(),
                       row["timeframe"]))

    def tests(self, symbol: Optional[str] = None, strategy: Optional[str] = None,
              phase: Optional[str] = None, limit: int = 10_000,
              timeframe: Optional[str] = None) -> List[Dict[str, Any]]:
        q, args = "SELECT * FROM tests WHERE 1=1", []
        for col, val in (("symbol", symbol), ("strategy", strategy), ("phase", phase), ("timeframe", timeframe)):
            if val:
                q += f" AND {col}=?"
                args.append(val)
        q += " ORDER BY created_at DESC LIMIT ?"
        args.append(limit)
        with self.conn() as c:
            out = []
            for r in c.execute(q, args):
                d = dict(r)
                d["params"] = json.loads(d.pop("params_json") or "{}")
                d["metrics"] = json.loads(d.pop("metrics_json"))
                d["why"] = json.loads(d.pop("why_json") or "[]")
                out.append(d)
            return out

    def test_count(self) -> Dict[str, int]:
        with self.conn() as c:
            return {r[0]: r[1] for r in c.execute("SELECT phase, COUNT(*) FROM tests GROUP BY phase")}

    def test_count_by_timeframe(self) -> Dict[str, Dict[str, int]]:
        with self.conn() as c:
            out: Dict[str, Dict[str, int]] = {}
            for tf, phase, n in c.execute("SELECT timeframe, phase, COUNT(*) FROM tests GROUP BY timeframe, phase"):
                out.setdefault(tf, {})[phase] = n
            return out

    # ---- runs ------------------------------------------------------------
    def start_run(self, run_id: str, kind: str) -> None:
        with self.conn() as c:
            c.execute("INSERT OR REPLACE INTO runs(run_id, kind, started_at) VALUES(?,?,?)", (run_id, kind, now()))

    def finish_run(self, run_id: str, summary: Dict[str, Any]) -> None:
        with self.conn() as c:
            c.execute("UPDATE runs SET finished_at=?, summary_json=? WHERE run_id=?",
                      (now(), json.dumps(summary, default=str), run_id))
