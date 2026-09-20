"""The run library — PostgreSQL, and optional on purpose.

Every run is worth keeping: the point of the tool is that a question answered
once does not have to be re-run to be re-read. One research session produced
1,746 runs, which is a query, not a directory scan — hence a database.

Postgres rather than SQLite because it is already what this team runs, and
because `JSONB` is indexable, so the full result can be queried later without
a migration. The cost is that Postgres needs a server and SQLite would not.
So the database is optional here in the strict sense: when it is unreachable a
backtest still runs and still renders, and only the library is unavailable.
A run never fails because the database is down.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from typing import Dict, List, Optional

log = logging.getLogger("lab.store")

DSN = os.getenv("LAB_DATABASE_URL", "")
SCHEMA = "backtest_lab"

_lock = threading.Lock()
_pool = None
_state = {"ok": False, "error": "not configured" if not DSN else "not connected"}

DDL = f"""
CREATE SCHEMA IF NOT EXISTS {SCHEMA};
CREATE TABLE IF NOT EXISTS {SCHEMA}.runs (
    id           bigserial PRIMARY KEY,
    created_at   timestamptz NOT NULL DEFAULT now(),
    label        text,
    symbol       text NOT NULL,
    timeframe    text NOT NULL,
    strategy     text NOT NULL,
    bars         integer,
    usable_bars  integer,
    trades       integer,
    net          double precision,
    bh           double precision,
    vs_bh        double precision,
    beats_bh     boolean,
    pf           double precision,
    win_rate     double precision,
    max_dd_pct   double precision,
    sharpe       double precision,
    top_trade_share double precision,
    verdict      text,
    parameters   jsonb NOT NULL DEFAULT '{{}}'::jsonb,
    result       jsonb NOT NULL DEFAULT '{{}}'::jsonb
);
CREATE INDEX IF NOT EXISTS runs_symbol_idx    ON {SCHEMA}.runs (symbol);
CREATE INDEX IF NOT EXISTS runs_timeframe_idx ON {SCHEMA}.runs (timeframe);
CREATE INDEX IF NOT EXISTS runs_strategy_idx  ON {SCHEMA}.runs (strategy);
CREATE INDEX IF NOT EXISTS runs_verdict_idx   ON {SCHEMA}.runs (verdict);
CREATE INDEX IF NOT EXISTS runs_created_idx   ON {SCHEMA}.runs (created_at DESC);
"""

SORTABLE = {"created_at", "net", "vs_bh", "pf", "trades", "win_rate",
            "max_dd_pct", "sharpe", "top_trade_share", "bars"}


def _connect():
    """Open the pool once. Failure is recorded, not raised."""
    global _pool
    if _pool is not None or not DSN:
        return _pool
    with _lock:
        if _pool is not None:
            return _pool
        try:
            from psycopg_pool import ConnectionPool
            pool = ConnectionPool(DSN, min_size=0, max_size=4, open=True,
                                  timeout=5.0, check=ConnectionPool.check_connection)
            with pool.connection() as conn:
                conn.execute(DDL)
            _pool, _state["ok"], _state["error"] = pool, True, None
        except Exception as e:                      # noqa: BLE001 — any failure is the same failure
            _state["ok"], _state["error"] = False, f"{type(e).__name__}: {e}"
            log.warning("run library unavailable: %s", _state["error"])
    return _pool


def status() -> Dict:
    _connect()
    return {"configured": bool(DSN), "available": _state["ok"],
            "error": _state["error"], "schema": SCHEMA}


def save(result: Dict, parameters: Dict, label: str = "",
         verdict: Optional[str] = None) -> Optional[int]:
    """Persist one run. Returns its id, or None when the library is down."""
    pool = _connect()
    if pool is None:
        return None
    # The candle array is the window, not the result — it is reproducible from
    # symbol, timeframe and bar count, and it is by far the largest field.
    body = {k: v for k, v in result.items() if k != "candles"}
    try:
        with pool.connection() as conn:
            row = conn.execute(
                f"""INSERT INTO {SCHEMA}.runs
                    (label, symbol, timeframe, strategy, bars, usable_bars, trades,
                     net, bh, vs_bh, beats_bh, pf, win_rate, max_dd_pct, sharpe,
                     top_trade_share, verdict, parameters, result)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    RETURNING id""",
                (label, result.get("symbol"), result.get("timeframe"),
                 result.get("strategy"), result.get("bars"), result.get("usableBars"),
                 result.get("trades"), result.get("net"), result.get("bh"),
                 result.get("vsBH"), result.get("beatsBH"), result.get("pf"),
                 result.get("winRate"), result.get("maxDDPct"), result.get("sharpe"),
                 result.get("topTradeShare"), verdict,
                 json.dumps(parameters or {}), json.dumps(body, default=str)),
            ).fetchone()
            return row[0] if row else None
    except Exception as e:                          # noqa: BLE001
        _state["ok"], _state["error"] = False, f"{type(e).__name__}: {e}"
        log.warning("run not saved: %s", _state["error"])
        return None


def search(symbol: str = "", timeframe: str = "", strategy: str = "",
           verdict: str = "", beats: Optional[bool] = None,
           sort: str = "created_at", desc: bool = True,
           limit: int = 200) -> List[Dict]:
    """Filter and sort the library. Empty list when it is unavailable."""
    pool = _connect()
    if pool is None:
        return []
    where, args = [], []
    for column, value in (("symbol", symbol), ("timeframe", timeframe),
                          ("strategy", strategy), ("verdict", verdict)):
        if value:
            where.append(f"{column} = %s")
            args.append(value)
    if beats is not None:
        where.append("beats_bh = %s")
        args.append(beats)
    order = sort if sort in SORTABLE else "created_at"
    sql = (f"SELECT id, created_at, label, symbol, timeframe, strategy, bars, "
           f"usable_bars, trades, net, bh, vs_bh, beats_bh, pf, win_rate, "
           f"max_dd_pct, sharpe, top_trade_share, verdict, parameters "
           f"FROM {SCHEMA}.runs "
           + (f"WHERE {' AND '.join(where)} " if where else "")
           + f"ORDER BY {order} {'DESC' if desc else 'ASC'} NULLS LAST LIMIT %s")
    keys = ["id", "createdAt", "label", "symbol", "timeframe", "strategy", "bars",
            "usableBars", "trades", "net", "bh", "vsBH", "beatsBH", "pf", "winRate",
            "maxDDPct", "sharpe", "topTradeShare", "verdict", "parameters"]
    try:
        with pool.connection() as conn:
            rows = conn.execute(sql, (*args, int(limit))).fetchall()
        return [{k: (v.isoformat() if hasattr(v, "isoformat") else v)
                 for k, v in zip(keys, row)} for row in rows]
    except Exception as e:                          # noqa: BLE001
        _state["ok"], _state["error"] = False, f"{type(e).__name__}: {e}"
        return []


def get(run_id: int) -> Optional[Dict]:
    pool = _connect()
    if pool is None:
        return None
    try:
        with pool.connection() as conn:
            row = conn.execute(
                f"SELECT id, created_at, label, verdict, parameters, result "
                f"FROM {SCHEMA}.runs WHERE id = %s", (run_id,)).fetchone()
        if not row:
            return None
        return {"id": row[0], "createdAt": row[1].isoformat(), "label": row[2],
                "verdict": row[3], "parameters": row[4], "result": row[5]}
    except Exception:                               # noqa: BLE001
        return None


def delete(run_id: int) -> bool:
    pool = _connect()
    if pool is None:
        return False
    try:
        with pool.connection() as conn:
            return conn.execute(f"DELETE FROM {SCHEMA}.runs WHERE id = %s",
                                (run_id,)).rowcount > 0
    except Exception:                               # noqa: BLE001
        return False
