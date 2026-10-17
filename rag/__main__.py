"""python -m rag db-init|db-check|ingest|fmp|search|ask|stats|eval|migrate|verify-migration|retire-sqlite
(research only; no broker access)

  fmp ingest --symbol TSLA | --all   Financial Modeling Prep -> PostgreSQL (incremental)
  fmp status [--symbol TSLA]         what is stored, what the FMP plan refuses
  fmp facts --symbol TSLA            stored statement figures (no LLM)"""
from __future__ import annotations

import argparse
import json
import sys
import textwrap

from rag.config import PILOT, MissingCredential, Settings


def _symbols(arg: str):
    return list(PILOT) if arg.lower() == "pilot" else [s.strip().upper() for s in arg.split(",") if s.strip()]


def _filters(p: argparse.ArgumentParser) -> None:
    p.add_argument("--symbol", required=True)
    p.add_argument("--doc-type", action="append", dest="doc_types",
                   help="10-K | 10-Q | 8-K (earnings release) | transcript; repeatable")
    p.add_argument("--year", type=int, dest="fiscal_year", help="fiscal year")
    p.add_argument("--quarter", dest="fiscal_quarter", choices=["Q1", "Q2", "Q3", "Q4", "FY"])
    p.add_argument("-k", type=int, default=8)
    p.add_argument("--no-auto-period", action="store_true",
                   help="do not read 'Q3 FY2026' / 'latest' from the question")
    p.add_argument("--json", action="store_true")


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):        # Windows consoles default to a legacy code page
        if hasattr(stream, "reconfigure") and (stream.encoding or "").lower().replace("-", "") != "utf8":
            stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="python -m rag", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("ingest", help="download SEC filings and index them")
    p.add_argument("--symbol", required=True, help="ticker, comma list, or 'pilot'")
    p.add_argument("--force", action="store_true", help="re-chunk and re-embed even if unchanged")
    p = sub.add_parser("search", help="retrieve chunks (no LLM)")
    p.add_argument("--query", required=True)
    _filters(p)
    p = sub.add_parser("ask", help="answer with citations")
    p.add_argument("--question", required=True)
    _filters(p)
    sub.add_parser("stats", help="documents and chunks per company")
    sub.add_parser("db-init", help="create the rag schema in PostgreSQL (idempotent)")
    sub.add_parser("db-check", help="test the PostgreSQL connection, pgvector and row counts")
    p = sub.add_parser("migrate", help="copy the retired SQLite store into PostgreSQL (safe to re-run)")
    p.add_argument("--sqlite", help="SQLite file (default rag/state/rag.db)")
    p.add_argument("--reembed", action="store_true", help="recompute embeddings that are missing/invalid in SQLite")
    p.add_argument("--dry-run", action="store_true")
    sub.add_parser("verify-migration", help="compare SQLite with PostgreSQL row by row")
    sub.add_parser("retire-sqlite", help="rename rag.db after verification passes (never deletes)")
    fmp = sub.add_parser("fmp", help="Financial Modeling Prep data").add_subparsers(dest="fmp_cmd", required=True)
    p = fmp.add_parser("ingest", help="fetch FMP data for a symbol (only what is new)")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--symbol", help="ticker or comma list")
    g.add_argument("--all", action="store_true", help="the 5 pilot stocks")
    p.add_argument("--force", action="store_true", help="re-download even if stored / fresh")
    p.add_argument("--refresh-hours", type=float, default=12.0, help="snapshot freshness window")
    p.add_argument("--reports", type=int, default=5, help="latest 10-Q/10-K note sets to embed")
    p.add_argument("--transcripts", type=int, default=4, help="latest earnings-call transcripts to embed")
    p = fmp.add_parser("status", help="stored FMP data and unsupported endpoints")
    p.add_argument("--symbol")
    p = fmp.add_parser("facts", help="stored financial statement figures")
    p.add_argument("--symbol", required=True)
    p.add_argument("--metric", action="append", help="FMP field, e.g. revenue (repeatable)")
    p.add_argument("--annual", action="store_true")
    p.add_argument("--latest", type=int, default=5)
    p = sub.add_parser("eval", help="run the A (FMP+Claude) vs B (RAG+Claude) evaluation")
    p.add_argument("--only", help="comma list of question ids")
    p.add_argument("--systems", default="A,B")
    a = ap.parse_args(argv)
    s = Settings()

    if a.cmd in ("db-init", "db-check", "migrate", "verify-migration", "retire-sqlite"):
        from rag import db, migrate
        try:
            if a.cmd == "db-init":
                out = db.init_db(s.pg_config())
            elif a.cmd == "db-check":
                out = {"database": repr(s.pg_config()), **db.check(s.pg_config())}
            elif a.cmd == "migrate":
                out = migrate.migrate(s, sqlite_path=a.sqlite, reembed=a.reembed, dry_run=a.dry_run)
            elif a.cmd == "verify-migration":
                out = migrate.verify(s.sqlite_path, s.pg_config())
            else:
                out = migrate.retire_sqlite(s)
        except (MissingCredential, db.DatabaseError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        print(json.dumps(out, indent=1, default=str))
        return 0 if out.get("ok", True) else 1

    if a.cmd == "fmp":
        return _fmp(a, s)

    if a.cmd == "ingest":
        from rag.ingest import ingest
        try:
            for sym in _symbols(a.symbol):
                print(f"{sym}:")
                print("  " + json.dumps(ingest(sym, s, force=a.force)))
        except MissingCredential as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        return 0

    if a.cmd == "stats":
        from rag.store import Store
        with Store(s.pg_config()) as st:
            rows = st.stats()
        for r in rows:
            print(f"{r['symbol']:5} {r['form']:5} docs={r['docs']:3} chunks={r['chunks']:5}  "
                  f"filed {r['first_filed']} .. {r['last_filed']}")
        print(f"total docs={sum(r['docs'] for r in rows)} chunks={sum(r['chunks'] or 0 for r in rows)}")
        return 0

    if a.cmd == "eval":
        from rag.evaluate import run
        print(json.dumps(run(s, only=a.only, systems=a.systems), indent=1, default=str)[:4000])
        return 0

    kw = dict(symbol=a.symbol.upper(), doc_types=a.doc_types, fiscal_year=a.fiscal_year,
              fiscal_quarter=a.fiscal_quarter, k=a.k, auto_period=not a.no_auto_period)
    if a.cmd == "search":
        from rag.retrieve import cite, search
        r = search(a.query, s, **kw)
        if a.json:
            print(json.dumps(r, indent=1, default=str))
            return 0
        print(f"filters: {r['filters']}  ({r['candidates']} chunks searched, {r['ms']} ms)\n")
        for n, h in enumerate(r["hits"], 1):
            print(f"[{n}] score={h['score']} sim={h['similarity']} kw#={h['keyword_rank']}  {cite(h)}")
            print(textwrap.indent(textwrap.shorten(h["text"], 420), "    "), "\n")
        return 0

    from rag.answer import ask
    r = ask(a.question, s, **kw)
    if a.json:
        print(json.dumps({k: v for k, v in r.items() if k != "hits"}, indent=1, default=str))
        return 0
    print(r["answer"], "\n")
    for c in r["citations"]:
        print(f"  [{c['n']}] ({c['kind']}) {c['source']}")
    print(f"\n  route: structured={r['route']['structured']} documents={r['route']['documents']} "
          f"({r['route']['reason']})")
    fc = r["factcheck"]
    print(f"  database check: {fc['checked']} figure(s) compared, {len(fc['conflicts'])} conflict(s)")
    g = r["guard"]
    if g["unverified"]:
        print(f"\n  WARNING: figures not found in the evidence: {', '.join(g['unverified'])}")
    if g["computed"]:
        print(f"  derived figures (check the arithmetic): {', '.join(g['computed'])}")
    print(f"\n  filters={r['filters']}  retrieval {r['retrieval_ms']} ms, total {r['seconds']} s, "
          f"cost ${r.get('cost_usd') or 0:.4f}")
    return 0


def _fmp(a, s: Settings) -> int:
    from rag.fmp.client import FmpAuthError, FmpQuotaExceeded
    try:
        if a.fmp_cmd == "ingest":
            from rag.fmp.ingest import ingest_all
            syms = list(PILOT) if a.all else _symbols(a.symbol)
            out = ingest_all(syms, settings=s, force=a.force, refresh_hours=a.refresh_hours, reports=a.reports,
                              transcripts=a.transcripts)
            for r in out:
                print(json.dumps({k: v for k, v in r.items() if k != "skipped"}, default=str))
                if r.get("skipped"):
                    print(f"  skipped (already stored / fresh): {len(r['skipped'])}")
            return 1 if any(r.get("errors") for r in out) else 0
        from rag.fmp import repo
        from rag.store import Store
        with Store(s.pg_config()) as st:
            if a.fmp_cmd == "status":
                print(repo.as_json(repo.status(st.db, a.symbol)))
                return 0
            rows = repo.statements(st.db, a.symbol, "annual" if a.annual else "quarter", latest=a.latest)
        fields = a.metric or ["revenue", "grossProfit", "operatingIncome", "netIncome", "epsDiluted",
                              "operatingCashFlow", "freeCashFlow", "totalDebt", "cashAndCashEquivalents"]
        by: dict = {}
        for r in rows:
            for f in fields:
                if f in r["data"]:
                    by.setdefault((r["period"], str(r["period_end"]), str(r["filing_date"])), {})[f] = r["data"][f]
        for (period, end, filed), vals in by.items():
            print(f"{a.symbol.upper()} {period} (ended {end}, filed {filed}): "
                  + ", ".join(f"{k}={v:,}" if isinstance(v, (int, float)) else f"{k}={v}" for k, v in vals.items()))
        return 0
    except (MissingCredential, FmpAuthError, FmpQuotaExceeded) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
