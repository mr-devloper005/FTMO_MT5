"""python -m research_agent <command>

    universe      refresh the NASDAQ common-stock universe
    screen        screen what is not yet screened today (parallel)
    watch         keep the universe and screen current, on a timer (continuous)
    strategies    list every testable strategy
    propose       submit a strategy spec (JSON file) through the contract
    backtest      backtest symbols x strategies (screen or validated phase)
    research      the whole workflow, deterministic, existing strategies only
    agent         the whole workflow driven by the Claude research agent
    leaderboard   print the ranked leaderboard
    export        write the leaderboard to CSV + Excel
    status        counts: universe, screens, strategies, tests, runs
    ui            the AI Research dashboard + Agent Control Center (also at /research in the FTMO hub)
    worker        the 24/7 background worker (normally started from the Control Center)
    control       start | pause | stop | run-now | status  — same commands as the Control Center
    sheets-sync   push the four report tabs to the configured Google Sheet
    quality       score an FMP snapshot with Rahul's lenses and store it (quality import FILE --as-of DATE)
    mcs           management credibility from guidance ledgers (mcs score [SYMBOL ...] | mcs extract FILE)

  operations (24/7 PC):
    health        research.db, worker, RAG database, disk, model cache (exit 0 ok / 1 degraded / 2 down)
    maintenance   prune old daily price caches (keep 3 days) and rotate the worker log
    backup        consistent backup (research.db, state, RAG schema dump) + checksum manifest
    restore       restore a backup / transfer folder (refuses to overwrite data without --replace)
    transfer-export  everything a new PC needs: git bundle + data + embedding model (+ wheels)

Research only: nothing here can place an order.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from research_agent.config import DEFAULT_STATE, Settings


def _settings(a) -> Settings:
    kw = {"state_dir": Path(a.state)}
    for k in ("workers", "bars", "target_outperformance_pts", "promote_grade"):
        v = getattr(a, k, None)
        if v is not None:
            kw[k] = v
    s = Settings(**kw)
    s.ensure_dirs()
    return s


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, default=str))


def _table(rows, cols) -> None:
    def fmt(v):
        if isinstance(v, float):
            return f"{v:,.1f}"
        return "" if v is None else str(v)
    widths = {c: min(max(len(c), *(len(fmt(r.get(c))) for r in rows)), 60) for c in cols} if rows else {}
    print("  ".join(c.ljust(widths[c]) for c in cols))
    for r in rows:
        print("  ".join(fmt(r.get(c))[:60].ljust(widths[c]) for c in cols))


def main(argv=None) -> int:
    from research_agent.oscompat import utf8_stdio
    utf8_stdio()                                   # Windows consoles / redirected logs: research text is UTF-8
    from research_agent.ops import load_research_env
    load_research_env()                            # services have no login shell: research keys from .env
    ap = argparse.ArgumentParser(prog="python -m research_agent", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--state", default=str(DEFAULT_STATE), help="state folder (db, cache, exports, reports)")
    ap.add_argument("--workers", type=int, help="parallel processes (default 8)")
    ap.add_argument("--bars", type=int, help="daily bars per backtest (default 2500 ≈ 10y)")
    ap.add_argument("--target-pts", dest="target_outperformance_pts", type=float,
                    help="outperformance vs Buy & Hold, in %% points, needed for Recommended (default 50)")
    ap.add_argument("--promote-grade", choices=["FRAGILE", "MODERATE", "ROBUST"])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("universe"); p.add_argument("--force", action="store_true")
    p = sub.add_parser("screen"); p.add_argument("--limit", type=int, default=0); p.add_argument("--force", action="store_true")
    p = sub.add_parser("watch")
    p.add_argument("--interval-min", type=float, default=30.0); p.add_argument("--rounds", type=int, default=0,
                                                                            help="0 = forever")
    p = sub.add_parser("strategies"); p.add_argument("--kinds", nargs="*")
    p = sub.add_parser("propose"); p.add_argument("file"); p.add_argument("--rationale", required=True)
    p = sub.add_parser("backtest")
    p.add_argument("--symbols", required=True); p.add_argument("--strategies", default="all")
    p.add_argument("--validate", action="store_true", help="also run full robustness on the best per stock")
    p = sub.add_parser("research")
    p.add_argument("--screen-limit", type=int, default=0, help="screen at most N stocks this run (0 = all)")
    p.add_argument("--shortlist", type=int)
    p = sub.add_parser("agent")
    p.add_argument("--shortlist", type=int, default=15); p.add_argument("--max-new", type=int, default=4)
    p.add_argument("--model", default=None); p.add_argument("--max-turns", type=int, default=60)
    p.add_argument("--budget-usd", type=float, default=5.0); p.add_argument("--job-budget", type=int, default=12_000)
    p.add_argument("--focus", default="")
    p.add_argument("--symbols", default="", help="fixed-universe run, e.g. MU,SNDK,TSLA,GS,MSFT (no screening)")
    p = sub.add_parser("leaderboard")
    p.add_argument("--limit", type=int, default=25); p.add_argument("--best-per-stock", action="store_true")
    p.add_argument("--verdict", nargs="*", choices=["Recommended", "Watch", "Rejected"])
    p = sub.add_parser("export"); p.add_argument("--best-per-stock", action="store_true"); p.add_argument("--limit", type=int, default=5000)
    sub.add_parser("status")
    p = sub.add_parser("ui"); p.add_argument("--port", type=int, default=8602)
    sub.add_parser("worker")
    p = sub.add_parser("control"); p.add_argument("action", choices=["start", "pause", "stop", "run-now", "status"])
    p = sub.add_parser("quality"); p.add_argument("action", choices=["import", "show"])
    p.add_argument("file", nargs="?"); p.add_argument("--as-of", default=None)
    p = sub.add_parser("mcs"); p.add_argument("action", choices=["score", "extract"])
    p.add_argument("args", nargs="*", help="score: symbols (default: every ledger) · extract: transcript FILE")
    p.add_argument("--period", help="extract: the period the guidance is for, e.g. 'FQ1 FY27'")
    p.add_argument("--delivery-weight", type=float, default=0.6, help="score: weight on delivery (rest: accuracy)")
    p.add_argument("--json", action="store_true", help="score: full JSON instead of the table")
    p = sub.add_parser("sheets-sync"); p.add_argument("--sheet", help="Google Sheet URL or ID to save first")
    p = sub.add_parser("health"); p.add_argument("--min-free-gb", type=float, default=5.0)
    p = sub.add_parser("maintenance"); p.add_argument("--keep-cache-days", type=int, default=3)
    p = sub.add_parser("backup"); p.add_argument("--dest", required=True); p.add_argument("--keep", type=int, default=14)
    p.add_argument("--no-rag-db", action="store_true", help="skip the PostgreSQL dump")
    p = sub.add_parser("restore"); p.add_argument("--from", dest="src", required=True)
    p.add_argument("--replace", action="store_true", help="overwrite existing data (it is backed up first)")
    p.add_argument("--no-rag-db", action="store_true")
    p.add_argument("--rag-state", help="where to unpack rag/state files (default: the live rag/state); use a temp\n"
                   "folder with --state and --no-rag-db for a test restore")
    p = sub.add_parser("transfer-export"); p.add_argument("--out", required=True)
    p.add_argument("--secrets-dir", help="where to write the filtered .env.windows (keep it apart from --out)")
    p.add_argument("--wheels", help="offline Windows wheelhouse to include (deploy/windows/lock_requirements.py)")
    a = ap.parse_args(argv)
    s = _settings(a)

    if a.cmd == "universe":
        from research_agent import pipeline
        _print(pipeline.ensure_universe(s, max_age_hours=0 if a.force else 20))
    elif a.cmd == "screen":
        from research_agent import screener
        _print(screener.run(s, limit=a.limit, force=a.force, progress=print))
    elif a.cmd == "watch":
        from research_agent import pipeline, screener
        n = 0
        while True:
            n += 1
            print(f"[watch] round {n}: universe {pipeline.ensure_universe(s)}", flush=True)
            res = screener.run(s, progress=lambda m: print(f"[watch] {m}", flush=True))
            print(f"[watch] round {n}: {res}", flush=True)
            if a.rounds and n >= a.rounds:
                break
            time.sleep(a.interval_min * 60)
    elif a.cmd == "strategies":
        from research_agent import catalog
        catalog.sync(s)
        _table(catalog.catalog(s, a.kinds), ["name", "kind", "description"])
    elif a.cmd == "propose":
        from research_agent import catalog
        _print(catalog.propose(s, json.loads(Path(a.file).read_text(encoding="utf-8")), a.rationale, author="human"))
    elif a.cmd == "backtest":
        from research_agent import pipeline
        syms = [x.strip().upper() for x in a.symbols.split(",") if x.strip()]
        names = None if a.strategies == "all" else [x.strip() for x in a.strategies.split(",")]
        res = pipeline.test_strategies(s, syms, names, progress=print)
        res.pop("fingerprints")
        _print(res)
        if a.validate:
            res = pipeline.validate_best(s, syms, progress=print)
            res.pop("fingerprints")
            _print(res)
    elif a.cmd == "research":
        from research_agent import pipeline
        _print(pipeline.research(s, screen_limit=a.screen_limit, shortlist_size=a.shortlist, progress=print))
    elif a.cmd == "agent":
        from research_agent import agent
        _print(agent.run(s, shortlist_size=a.shortlist, max_new_strategies=a.max_new,
                         model=a.model or agent.DEFAULT_MODEL, max_turns=a.max_turns,
                         max_budget_usd=a.budget_usd, job_budget=a.job_budget, focus=a.focus,
                         symbols=[x for x in a.symbols.split(",") if x.strip()] or None))
    elif a.cmd == "leaderboard":
        from research_agent import ranking
        rows = ranking.leaderboard(s, limit=a.limit, best_per_stock=a.best_per_stock, verdicts=a.verdict)
        _print(ranking.summary(ranking.leaderboard(s, limit=10**6)))
        _table(rows, ["Rank", "Stock", "Strategy", "Timeframe", "Return %", "Buy & Hold %", "Outperformance (pts)",
                      "Robustness", "Verdict", "Reason"])
    elif a.cmd == "export":
        from research_agent import export, ranking
        rows = ranking.leaderboard(s, limit=a.limit, best_per_stock=a.best_per_stock)
        _print(export.export(s, rows, ranking.summary(rows)))
    elif a.cmd == "status":
        from research_agent.store import Store
        st = Store(s.db_path)
        with st.conn() as c:
            runs = [dict(r) for r in c.execute("SELECT run_id, kind, started_at, finished_at FROM runs ORDER BY started_at DESC LIMIT 5")]
            kinds = {r[0]: r[1] for r in c.execute("SELECT kind, COUNT(*) FROM strategies WHERE status='active' GROUP BY kind")}
        _print({"universe_active": len(st.active_symbols()), "screens": st.screen_counts(),
                "strategies": kinds, "tests": st.test_count(), "recent_runs": runs, "state": str(s.state_dir)})
    elif a.cmd == "ui":
        import uvicorn
        from research_agent.web import standalone_app
        print(f"AI Research dashboard: http://127.0.0.1:{a.port}/research")
        uvicorn.run(standalone_app(), host="127.0.0.1", port=a.port, log_level="warning")
    elif a.cmd == "worker":
        from research_agent import worker
        worker.run(s)
    elif a.cmd == "control":
        from research_agent.control import Control
        ctl = Control(s)
        fn = {"start": ctl.start, "pause": ctl.pause, "stop": ctl.stop, "run-now": ctl.run_now,
              "status": ctl.status}[a.action]
        _print(fn())
    elif a.cmd == "quality":
        from research_agent import views
        from research_agent.quality import build
        if a.action == "import":
            if not a.file:
                ap.error("quality import needs the snapshot FILE")
            _print(build.import_snapshot(s, Path(a.file), as_of=a.as_of or "unknown"))
        else:
            _table(views.quality(s)["rows"], ["Stock", "Quality", "Bucket", "Grid"])
    elif a.cmd == "mcs":
        from research_agent.quality import mcs, mcs_extract
        if a.action == "extract":
            if not a.args:
                ap.error("mcs extract needs a transcript FILE (saved FMP response or plain text)")
            _print(mcs_extract.draft_period(mcs_extract.load_transcript(Path(a.args[0])), a.period or "?"))
        else:
            w = {"delivery": a.delivery_weight, "accuracy": round(1 - a.delivery_weight, 4)}
            results = [mcs.score_ledger(mcs.load_ledger(sym), w) for sym in (a.args or mcs.available())]
            if a.json:
                _print(results)
            for r in ([] if a.json else results):
                print(f"\n{r['symbol']}  MCS {r['average']} ({r['rating']}), 95% interval {r['interval_95']}, "
                      f"latest 4 quarters {r['latest_4q']}")
                _table([{"Quarter": p["period"], "Delivery": p.get("delivery"), "Accuracy": p.get("accuracy"),
                         "MCS": p["mcs"], "Rating": p.get("rating"), "Rolling 4Q": p.get("rolling_4q"),
                         "Flags": "; ".join(p.get("flags", []))} for p in r["periods"]],
                       ["Quarter", "Delivery", "Accuracy", "MCS", "Rating", "Rolling 4Q", "Flags"])
    elif a.cmd == "sheets-sync":
        from research_agent import sheets
        if a.sheet:
            sheets.set_sheet(s, a.sheet)
        _print(sheets.sync(s))
    elif a.cmd in ("health", "maintenance", "backup", "restore", "transfer-export"):
        from research_agent import ops
        if a.cmd == "health":
            h = ops.health(s, min_free_gb=a.min_free_gb)
            _print(h)
            return {"ok": 0, "degraded": 1}.get(h["status"], 2)
        if a.cmd == "maintenance":
            _print(ops.maintenance(s, a.keep_cache_days))
        elif a.cmd == "backup":
            _print(ops.backup(Path(a.dest), s, keep=a.keep, include_rag_db=not a.no_rag_db))
        elif a.cmd == "restore":
            try:
                r = ops.restore(Path(a.src), s, replace=a.replace, restore_rag_db=not a.no_rag_db,
                                rag_state_dir=Path(a.rag_state) if a.rag_state else None)
            except ops.RefusedRestore as e:
                print(f"refused: {e}", file=sys.stderr)
                return 3
            _print(r)
            return 0 if r["ok"] else 1
        else:
            _print(ops.transfer_export(Path(a.out), s, secrets_dir=Path(a.secrets_dir) if a.secrets_dir else None,
                                       wheel_dir=Path(a.wheels) if a.wheels else None))
    return 0


if __name__ == "__main__":
    sys.exit(main())
