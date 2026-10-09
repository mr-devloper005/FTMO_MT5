"""PostgreSQL access for the FMP structured tables (schema: rag/migrations/002_fmp.sql).

Upserts report what happened to each row: 'inserted', 'updated' (FMP revised the
numbers) or 'unchanged' (same content hash: nothing written)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from psycopg.types.json import Jsonb

KEYS = {"financial_statements": ("symbol", "statement", "period_type", "period_end"),
        "earnings": ("symbol", "announce_date"),
        "company_data": ("symbol", "dataset", "record_key")}


def upsert(conn, table: str, rows: Iterable[Dict[str, Any]]) -> Dict[str, int]:
    """Insert new rows, update rows whose data_sha changed, leave identical rows alone."""
    key = KEYS[table]
    counts = {"inserted": 0, "updated": 0, "unchanged": 0}
    with conn.transaction():
        for row in rows:
            row = {k: (Jsonb(v) if k == "data" else v) for k, v in row.items()}
            cols = list(row)
            upd = ", ".join(f"{c}=EXCLUDED.{c}" for c in cols if c not in key) + ", fetched_at=now()"
            r = conn.execute(
                f"INSERT INTO rag.{table} ({','.join(cols)}) VALUES ({','.join(['%s'] * len(cols))}) "
                f"ON CONFLICT ({','.join(key)}) DO UPDATE SET {upd} "
                f"WHERE rag.{table}.data_sha IS DISTINCT FROM EXCLUDED.data_sha "
                f"RETURNING (xmax = 0) AS inserted", [row[c] for c in cols]).fetchone()
            counts["unchanged" if r is None else "inserted" if r["inserted"] else "updated"] += 1
    return counts


def log_fetch(conn, symbol: Optional[str], endpoint: str, params: Dict[str, Any], outcome: str,
              http_status: Optional[int] = None, records: Optional[int] = None, added: Optional[int] = None,
              message: str = "") -> None:
    with conn.transaction():
        conn.execute("INSERT INTO rag.fetch_log (symbol, endpoint, params, outcome, http_status, records, added, "
                     "message) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                     (symbol, endpoint, Jsonb({k: v for k, v in params.items() if k.lower() != "apikey"}),
                      outcome, http_status, records, added, message[:500]))


def last_fetch(conn, symbol: str, endpoint: str, params: Optional[Dict[str, Any]] = None,
               outcomes: Sequence[str] = ("ok",)) -> Optional[Dict[str, Any]]:
    sql = ("SELECT outcome, http_status, records, message, fetched_at FROM rag.fetch_log "
           "WHERE symbol=%s AND endpoint=%s AND outcome = ANY(%s)")
    args: List[Any] = [symbol, endpoint, list(outcomes)]
    if params is not None:
        sql += " AND params = %s"
        args.append(Jsonb(params))
    return conn.execute(sql + " ORDER BY fetched_at DESC LIMIT 1", args).fetchone()


def fresh(conn, symbol: str, endpoint: str, params: Dict[str, Any], hours: float) -> bool:
    """Was this exact request answered successfully within ``hours``?"""
    r = last_fetch(conn, symbol, endpoint, params)
    return bool(r) and r["fetched_at"] > datetime.now(timezone.utc) - timedelta(hours=hours)


def known_unsupported(conn, symbol: str, endpoint: str, params: Optional[Dict[str, Any]] = None,
                      hours: float = 24) -> Optional[Dict[str, Any]]:
    """Did the plan refuse this endpoint (with these params, ignoring 'limit') within ``hours``?"""
    sql = ("SELECT outcome, http_status, message, fetched_at FROM rag.fetch_log WHERE symbol=%s AND endpoint=%s "
           "AND outcome='unsupported' AND fetched_at > now() - make_interval(secs => %s)")
    args: List[Any] = [symbol, endpoint, hours * 3600]
    if params:
        sql += " AND params @> %s"
        args.append(Jsonb({k: v for k, v in params.items() if k not in ("limit", "apikey")}))
    return conn.execute(sql + " ORDER BY fetched_at DESC LIMIT 1", args).fetchone()


def stored_periods(conn, symbol: str, period_type: str = "quarter") -> List[Tuple[int, str]]:
    """(fiscal_year, fiscal_quarter) present for ALL three statements."""
    rows = conn.execute("SELECT fiscal_year, fiscal_quarter FROM rag.financial_statements "
                        "WHERE symbol=%s AND period_type=%s GROUP BY fiscal_year, fiscal_quarter "
                        "HAVING count(DISTINCT statement)=3", (symbol, period_type)).fetchall()
    return [(r["fiscal_year"], r["fiscal_quarter"]) for r in rows]


def quarter_ends(conn, symbol: str) -> List[Tuple[str, int, str]]:
    rows = conn.execute("SELECT DISTINCT period_end, fiscal_year, fiscal_quarter FROM rag.financial_statements "
                        "WHERE symbol=%s AND period_type='quarter' AND fiscal_year IS NOT NULL", (symbol,))
    return [(r["period_end"].isoformat(), r["fiscal_year"], r["fiscal_quarter"]) for r in rows]


def filing_date(conn, symbol: str, fiscal_year: int, period: str) -> Optional[str]:
    period_type = "annual" if period == "FY" else "quarter"
    r = conn.execute("SELECT max(filing_date) AS d FROM rag.financial_statements WHERE symbol=%s AND "
                     "period_type=%s AND fiscal_year=%s AND fiscal_quarter=%s",
                     (symbol, period_type, fiscal_year, period)).fetchone()
    return r["d"].isoformat() if r and r["d"] else None


def company_name(conn, symbol: str) -> Optional[str]:
    r = conn.execute("SELECT data->>'companyName' AS n FROM rag.company_data WHERE symbol=%s AND dataset='profile' "
                     "LIMIT 1", (symbol,)).fetchone()
    return r["n"] if r else None


def doc_exists(conn, doc_id: str) -> Optional[str]:
    r = conn.execute("SELECT sha256 FROM rag.documents WHERE doc_id=%s", (doc_id,)).fetchone()
    return r["sha256"].strip() if r and r["sha256"] else ("" if r else None)


# ------------------------------------------------------------------ reads used by answering
def statements(conn, symbol: str, period_type: str = "quarter", fiscal_year: Optional[int] = None,
               fiscal_quarter: Optional[str] = None, latest: int = 0) -> List[Dict[str, Any]]:
    """Statement rows newest first; filter by period, or the ``latest`` N periods."""
    sql = ("SELECT symbol, statement, period_type, period_end, fiscal_year, fiscal_quarter, period, filing_date, "
           "reported_currency, data, source_url FROM rag.financial_statements WHERE symbol=%s AND period_type=%s")
    args: List[Any] = [symbol.upper(), period_type]
    if fiscal_year:
        sql += " AND fiscal_year=%s"
        args.append(fiscal_year)
    if fiscal_quarter:
        sql += " AND fiscal_quarter=%s"
        args.append(fiscal_quarter)
    if latest:
        sql += (" AND period_end IN (SELECT DISTINCT period_end FROM rag.financial_statements WHERE symbol=%s "
                "AND period_type=%s ORDER BY period_end DESC LIMIT %s)")
        args += [symbol.upper(), period_type, latest]
    return conn.execute(sql + " ORDER BY period_end DESC, statement", args).fetchall()


def earnings(conn, symbol: str, limit: int = 4) -> List[Dict[str, Any]]:
    return conn.execute("SELECT * FROM rag.earnings WHERE symbol=%s AND eps_actual IS NOT NULL "
                        "ORDER BY announce_date DESC LIMIT %s", (symbol.upper(), limit)).fetchall()


def company(conn, symbol: str, dataset: str) -> List[Dict[str, Any]]:
    return conn.execute("SELECT record_key, as_of, data, source_url FROM rag.company_data WHERE symbol=%s "
                        "AND dataset=%s ORDER BY record_key", (symbol.upper(), dataset)).fetchall()


def status(conn, symbol: Optional[str] = None) -> Dict[str, Any]:
    w, a = ("WHERE symbol=%s", [symbol.upper()]) if symbol else ("", [])
    q = lambda sql: conn.execute(sql.format(w=w), a).fetchall()                       # noqa: E731
    return {
        "statements": q("SELECT symbol, statement, period_type, count(*) AS periods, min(period) AS oldest_label, "
                        "max(period_end) AS latest_period_end FROM rag.financial_statements {w} "
                        "GROUP BY 1,2,3 ORDER BY 1,2,3"),
        "earnings": q("SELECT symbol, count(*) AS rows, max(announce_date) FILTER (WHERE eps_actual IS NOT NULL) "
                      "AS latest_reported, min(announce_date) FILTER (WHERE eps_actual IS NULL) AS next_scheduled "
                      "FROM rag.earnings {w} GROUP BY 1 ORDER BY 1"),
        "company_data": q("SELECT symbol, dataset, count(*) AS records FROM rag.company_data {w} "
                          "GROUP BY 1,2 ORDER BY 1,2"),
        "documents": q("SELECT symbol, source, form, count(*) AS docs, sum(n_chunks) AS chunks FROM rag.documents "
                       "{w} GROUP BY 1,2,3 ORDER BY 1,2,3"),
        # refused = the latest call to that endpoint was refused (a later success supersedes it)
        "unsupported": q("SELECT * FROM (SELECT DISTINCT ON (symbol, endpoint) symbol, endpoint, outcome, http_status, "
                         "message FROM rag.fetch_log {w}" + (" AND" if symbol else " WHERE") +
                         " outcome IN ('ok', 'unsupported') ORDER BY symbol, endpoint, fetched_at DESC) x "
                         "WHERE outcome='unsupported'"),
    }


def as_json(rows: Any) -> str:
    return json.dumps(rows, default=str, indent=1)
