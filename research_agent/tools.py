"""The research agent's entire reach: these tools and nothing else.

Each tool is a plain handler ``(ctx, args) -> dict`` (unit-testable without the
SDK), wrapped into an in-process MCP server by ``build_server``. What is
deliberately absent: any broker, order, account or position call; shell; file
reads; arbitrary file writes; web access beyond the fixed NASDAQ listing URL.
The two tools that write files write only into this agent's own
``state/reports`` and ``state/exports`` folders, with names they choose.

Every call is bounded (symbols, strategies, jobs per call, and a per-run job
budget) so one tool call stays short and one run stays affordable.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

from research_agent import backtester, catalog, export, pipeline, ranking, screener
from research_agent import strategy_spec as ss
from research_agent.config import SCREEN_TIMEFRAME, TIMEFRAME_LABELS, TIMEFRAMES, Settings
from research_agent.store import Store

SERVER = "research"
MAX_SCREEN_BATCH = 600
MAX_SYMBOLS = 30
MAX_STRATEGIES = 30
MAX_JOBS_PER_CALL = 1_500                    # ~5 stocks x 30 strategies x 10 timeframes
MAX_REPORT_CHARS = 60_000
MAX_RAG_QUESTIONS = 8                        # per stock per run


@dataclass
class ToolContext:
    settings: Settings
    run_id: str
    job_budget: int = 12_000                   # backtests this run may start (validated count x20)
    max_new_strategies: int = 6
    jobs_used: int = 0
    strategies_proposed: int = 0
    calls: List[Dict[str, Any]] = field(default_factory=list)
    log: Optional[Callable[[Dict[str, Any]], None]] = None
    fixed_symbols: Optional[List[str]] = None  # fixed-universe run: no other ticker can be touched
    stats: Dict[str, Any] = field(default_factory=dict)

    def record(self, name: str, args: Dict, result: Dict, seconds: float) -> None:
        brief = {k: v for k, v in result.items()
                 if isinstance(v, (int, float, str, bool)) and len(str(v)) <= 200}
        entry = {"tool": name, "args": args, "seconds": round(seconds, 2),
                 "ok": "error" not in result, "result_keys": sorted(result)[:20],
                 "brief": dict(list(brief.items())[:10])}
        self.calls.append(entry)
        if self.log:
            self.log({"type": "tool_call", **entry})

    def progress(self, tool: str, message: str) -> None:
        """Live progress from inside a long tool call (e.g. screening 600 stocks)."""
        if self.log:
            self.log({"type": "progress", "tool": tool, "message": message})


class ToolError(ValueError):
    pass


def _symbols(args: Dict, key: str = "symbols", cap: int = MAX_SYMBOLS,
             ctx: Optional["ToolContext"] = None) -> List[str]:
    raw = args.get(key) or []
    syms = [str(s).strip().upper() for s in ([raw] if isinstance(raw, str) else raw) if str(s).strip()]
    if not syms:
        raise ToolError(f"{key} is required")
    if len(syms) > cap:
        raise ToolError(f"at most {cap} symbols per call")
    bad = [s for s in syms if not re.match(r"^[A-Z0-9][A-Z0-9\-]{0,9}$", s)]
    if bad:
        raise ToolError(f"not ticker symbols: {bad}")
    _in_scope(ctx, syms)
    return syms


def _in_scope(ctx: Optional["ToolContext"], syms: List[str]) -> None:
    if ctx is not None and ctx.fixed_symbols:
        outside = [s for s in syms if s not in ctx.fixed_symbols]
        if outside:
            raise ToolError(f"{outside} are outside this run's fixed universe {ctx.fixed_symbols}")


def _tally(ctx: "ToolContext", phase: str, res: Dict[str, Any]) -> None:
    t = ctx.stats.setdefault(phase, {"requested": 0, "new": 0, "reused_cached": 0, "failed": 0})
    t["requested"] += res.get("unique", 0)
    t["new"] += res.get("ran", 0)
    t["reused_cached"] += res.get("skipped_known", 0)
    t["failed"] += res.get("errors", 0)


def _charge(ctx: ToolContext, jobs: int) -> None:
    if jobs > MAX_JOBS_PER_CALL:
        raise ToolError(f"{jobs} backtests in one call; the limit is {MAX_JOBS_PER_CALL} — split it")
    if ctx.jobs_used + jobs > ctx.job_budget:
        raise ToolError(f"run budget: {ctx.job_budget - ctx.jobs_used} backtests left, {jobs} requested")
    ctx.jobs_used += jobs


# ---------------------------------------------------------------- handlers
def refresh_universe(ctx: ToolContext, args: Dict) -> Dict:
    return pipeline.ensure_universe(ctx.settings, max_age_hours=0 if args.get("force") else 20)


def screen_universe(ctx: ToolContext, args: Dict) -> Dict:
    limit = int(args.get("limit") or MAX_SCREEN_BATCH)
    if not 1 <= limit <= MAX_SCREEN_BATCH:
        raise ToolError(f"limit must be 1–{MAX_SCREEN_BATCH}")
    res = screener.run(ctx.settings, limit=limit, force=bool(args.get("force")),
                       progress=lambda m: ctx.progress("screen_universe", m))
    res["totals"] = Store(ctx.settings.db_path).screen_counts()
    return res


def get_shortlist(ctx: ToolContext, args: Dict) -> Dict:
    n = min(int(args.get("limit") or ctx.settings.shortlist_size), 50)
    rows = screener.shortlist(ctx.settings, n * 3 if args.get("uptrend_only") else n)
    if args.get("uptrend_only"):
        rows = [r for r in rows if r.get("trend_up")][:n]
    store = Store(ctx.settings.db_path)
    keep = ("symbol", "score", "price", "dollar_volume", "atr_pct", "trend_up", "return_1y", "gap_p95", "as_of")
    return {"count": len(rows), "totals": store.screen_counts(),
            "stocks": [{**{k: r.get(k) for k in keep},
                        "name": (store.universe_name(r["symbol"]) or "").split(" - ")[0]} for r in rows]}


def market_snapshot(ctx: ToolContext, args: Dict) -> Dict:
    """Recent behaviour of a few stocks: what a strategy designer looks at first."""
    from ftmo_mt5.signals import indicators as ind
    from systematic.data import load_completed, use_daily_cache
    use_daily_cache(ctx.settings.state_dir)
    out = {}
    for sym in _symbols(args, cap=10, ctx=ctx):
        try:
            c = load_completed(sym, SCREEN_TIMEFRAME, ctx.settings.bars)     # snapshot = daily picture
        except Exception as e:                                   # noqa: BLE001
            out[sym] = {"error": f"{type(e).__name__}: {str(e)[:120]}"}
            continue
        closes = [x.close for x in c]
        last = closes[-1]
        def ret(n):
            return round((last / closes[-n - 1] - 1) * 100, 2) if len(closes) > n else None
        sma50, sma200 = ind.sma(closes, 50)[-1], ind.sma(closes, 200)[-1]
        atr = ind.atr(c, 14)[-1]
        peak = max(closes[-252:])
        out[sym] = {"bars": len(c), "last_close": round(last, 2), "ret_1m": ret(21), "ret_3m": ret(63),
                    "ret_1y": ret(252), "ret_5y": ret(1260),
                    "vs_sma50_pct": round((last / sma50 - 1) * 100, 2) if sma50 else None,
                    "vs_sma200_pct": round((last / sma200 - 1) * 100, 2) if sma200 else None,
                    "rsi14": round(ind.rsi(closes, 14)[-1] or 0, 1), "atr_pct": round(atr / last * 100, 2) if atr else None,
                    "off_1y_high_pct": round((last / peak - 1) * 100, 2),
                    "screen": Store(ctx.settings.db_path).screen(sym)}
    return {"snapshots": out}


def _timeframes(ctx: ToolContext) -> List[Dict[str, Any]]:
    return [{"timeframe": tf, "label": TIMEFRAME_LABELS.get(tf, tf),
             "history": "last %d bars" % ctx.settings.history_bars(tf) if ctx.settings.history_bars(tf)
             else "all available history"} for tf in ctx.settings.timeframes]


def list_strategies(ctx: ToolContext, args: Dict) -> Dict:
    rows = catalog.catalog(ctx.settings, kinds=args.get("kinds") or None)
    return {"count": len(rows), "timeframes": _timeframes(ctx),
            "strategies": [{k: r.get(k) for k in ("name", "kind", "description", "params", "rationale")}
                           for r in rows]}


def strategy_schema(ctx: ToolContext, args: Dict) -> Dict:
    return {"contract": ss.__doc__, "indicators": {"price": sorted(ss.PRICE), "windowed": sorted(ss.WINDOWED)},
            "ops": sorted(ss.OPS), "limits": {"params": ss.MAX_PARAMS, "comparisons": ss.MAX_CONDITIONS,
                                              "depth": ss.MAX_DEPTH},
            "proposals_left_this_run": ctx.max_new_strategies - ctx.strategies_proposed,
            "timeframes": _timeframes(ctx),
            "note": "Every strategy is backtested on every configured timeframe. Periods are counted in bars of "
                    "the timeframe being tested (a 20-bar SMA is 20 days on 1d and 100 minutes on 5m)."}


def propose_strategy(ctx: ToolContext, args: Dict) -> Dict:
    if ctx.strategies_proposed >= ctx.max_new_strategies:
        raise ToolError(f"this run may propose at most {ctx.max_new_strategies} new strategies")
    spec = args.get("spec")
    if isinstance(spec, str):
        try:
            spec = json.loads(spec)
        except json.JSONDecodeError as e:
            raise ToolError(f"spec is not valid JSON: {e}") from e
    ctx.strategies_proposed += 1
    res = catalog.propose(ctx.settings, spec, str(args.get("rationale") or ""), author=f"agent:{ctx.run_id}")
    res["proposals_left_this_run"] = ctx.max_new_strategies - ctx.strategies_proposed
    return res


def run_backtests(ctx: ToolContext, args: Dict) -> Dict:
    """Screen phase: each strategy on its default parameters, on each stock, on every configured timeframe."""
    syms = _symbols(args, ctx=ctx)
    names = args.get("strategies") or []
    if names == "all" or names == ["all"]:
        # "all" is bounded by the per-call job limit (stocks x strategies x timeframes), not the name cap
        names = [s["name"] for s in catalog.catalog(ctx.settings)]
    elif not names or len(names) > MAX_STRATEGIES:
        raise ToolError(f"strategies: 1–{MAX_STRATEGIES} names, or \"all\"")
    _charge(ctx, _new_jobs(ctx, syms, list(names)))      # only what has not been tested before costs budget
    res = pipeline.test_strategies(ctx.settings, syms, list(names), run_id=ctx.run_id)
    _tally(ctx, "screen", res)
    store = Store(ctx.settings.db_path)
    fps = set(res["fingerprints"])
    rows = [t for t in store.tests(phase="screen", limit=100_000) if t["fingerprint"] in fps]
    rows.sort(key=lambda t: -(t["metrics"].get("vsBHPct") or -1e9))
    res.pop("fingerprints")
    res["best"] = [_brief(t) for t in rows[:15]]
    res["beat_buy_and_hold"] = sum(1 for t in rows if t["metrics"].get("beatsBH"))
    res["timeframes"] = list(ctx.settings.timeframes)
    res["beat_buy_and_hold_by_timeframe"] = {
        tf: sum(1 for t in rows if t["timeframe"] == tf and t["metrics"].get("beatsBH")) for tf in ctx.settings.timeframes}
    res["budget_left"] = ctx.job_budget - ctx.jobs_used
    return res


def _new_jobs(ctx: ToolContext, syms: List[str], names: List[str]) -> int:
    cat = {s["name"]: s for s in catalog.catalog(ctx.settings)}
    unknown = [n for n in names if n not in cat]
    if unknown:
        raise ToolError(f"unknown strategies: {unknown} — call list_strategies")
    jobs = backtester.plan(ctx.settings, [{"symbol": s, "strategy": n, "params": cat[n]["params"]}
                                          for s in syms for n in names], "screen")
    store = Store(ctx.settings.db_path)
    return sum(1 for j in jobs if not store.has_test(j["fingerprint"]))


def validate_top(ctx: ToolContext, args: Dict) -> Dict:
    """Validated phase: full robustness grading of each stock's best screen results."""
    syms = _symbols(args, ctx=ctx)
    top_n = min(int(args.get("top_n") or ctx.settings.validate_top_n), 5)
    picked = backtester.pick_for_validation(ctx.settings, syms, top_n)
    _charge(ctx, len(picked) * 20)                    # one validation ~ 20 backtests
    res = backtester.run(ctx.settings, picked, "validated", run_id=ctx.run_id)
    _tally(ctx, "validated", res)
    store = Store(ctx.settings.db_path)
    fps = set(res.pop("fingerprints"))
    res["results"] = [_brief(t) for t in store.tests(phase="validated", limit=100_000) if t["fingerprint"] in fps]
    res["budget_left"] = ctx.job_budget - ctx.jobs_used
    return res


def lookup_results(ctx: ToolContext, args: Dict) -> Dict:
    limit = min(int(args.get("limit") or 25), 100)
    tf = args.get("timeframe") or None
    if tf and tf not in ctx.settings.timeframes:
        raise ToolError(f"timeframe must be one of {list(ctx.settings.timeframes)}")
    if ctx.fixed_symbols:
        if not args.get("symbol"):
            raise ToolError(f"symbol is required in a fixed-universe run ({ctx.fixed_symbols})")
        _in_scope(ctx, [str(args["symbol"]).upper()])
    rows = Store(ctx.settings.db_path).tests(symbol=(args.get("symbol") or "").upper() or None,
                                             strategy=args.get("strategy") or None,
                                             phase=args.get("phase") or None, limit=limit, timeframe=tf)
    return {"count": len(rows), "tests": [_brief(t) for t in rows],
            "totals": Store(ctx.settings.db_path).test_count()}


def get_leaderboard(ctx: ToolContext, args: Dict) -> Dict:
    rows = ranking.leaderboard(ctx.settings, limit=min(int(args.get("limit") or 25), 100),
                               best_per_stock=bool(args.get("best_per_stock")),
                               verdicts=args.get("verdicts") or None, symbols=ctx.fixed_symbols)
    full = ranking.leaderboard(ctx.settings, limit=1_000_000, symbols=ctx.fixed_symbols)
    return {"summary": ranking.summary(full),
            "rows": [{k: v for k, v in r.items() if not k.startswith("_") and k not in ("Tested at",)} for r in rows]}


def export_leaderboard(ctx: ToolContext, args: Dict) -> Dict:
    rows = ranking.leaderboard(ctx.settings, limit=min(int(args.get("limit") or 500), 5000),
                               best_per_stock=bool(args.get("best_per_stock")))
    files = export.export(ctx.settings, rows, ranking.summary(rows), name=f"leaderboard-{ctx.run_id}")
    return {"rows": len(rows), "files": files}


def write_report(ctx: ToolContext, args: Dict) -> Dict:
    title = str(args.get("title") or "").strip()
    body = str(args.get("markdown") or "")
    if not title or not body:
        raise ToolError("title and markdown are required")
    if len(body) > MAX_REPORT_CHARS:
        raise ToolError(f"report is {len(body)} chars; the limit is {MAX_REPORT_CHARS}")
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60] or "report"
    path = ctx.settings.reports_dir / f"{ctx.run_id}-{slug}.md"           # fixed folder, our name
    ctx.settings.reports_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# {title}\n\n{body}\n")
    return {"path": str(path), "chars": len(body)}


# ---------------------------------------------------------------- fixed-universe run + company research (RAG)
def screen_symbols(ctx: ToolContext, args: Dict) -> Dict:
    """Data context for the fixed stocks: the deterministic screen (price, liquidity, ATR, history) on daily bars."""
    syms = _symbols(args, ctx=ctx) if args.get("symbols") else list(ctx.fixed_symbols or [])
    if not syms:
        raise ToolError("symbols is required")
    res = screener.run(ctx.settings, symbols=syms, progress=lambda m: ctx.progress("screen_symbols", m))
    store = Store(ctx.settings.db_path)
    keep = ("symbol", "as_of", "passed", "reason", "price", "dollar_volume", "atr_pct", "history_bars",
            "trend_up", "return_1y", "error")
    return {**res, "note": "A failed screen does not stop backtests in a fixed-universe run; report it as context.",
            "stocks": [{k: (store.screen(s) or {}).get(k) for k in keep} for s in syms]}


def company_coverage(ctx: ToolContext, args: Dict) -> Dict:
    """Deterministic inventory of the company evidence the RAG holds for one stock, and what is missing."""
    from research_agent import company
    sym = _symbols({"symbols": [args.get("symbol")]}, ctx=ctx)[0]
    return company.coverage(sym)


def company_research(ctx: ToolContext, args: Dict) -> Dict:
    """One question to the read-only company RAG. Cited, number-checked; 'Not found in the available data.' if absent."""
    from research_agent import company
    sym = _symbols({"symbols": [args.get("symbol")]}, ctx=ctx)[0]
    q = str(args.get("question") or "").strip()
    if not q:
        raise ToolError("question is required")
    n = ctx.stats.setdefault("rag_questions", {}).get(sym, 0)
    if n >= MAX_RAG_QUESTIONS:
        raise ToolError(f"at most {MAX_RAG_QUESTIONS} company_research questions per stock per run")
    out = company.research(ctx.settings, ctx.run_id, sym, q)
    ctx.stats["rag_questions"][sym] = n + 1
    ctx.stats["rag_cost_usd"] = round(ctx.stats.get("rag_cost_usd", 0.0) + (out.get("cost_usd") or 0.0), 4)
    return out


def record_company_context(ctx: ToolContext, args: Dict) -> Dict:
    """Store the company-context write-up for one stock. It never changes the deterministic verdict."""
    from research_agent import company
    sym = _symbols({"symbols": [args.get("symbol")]}, ctx=ctx)[0]
    missing = [k for k in company.FIELDS if not str(args.get(k) or "").strip()]
    if missing:
        raise ToolError(f"missing fields: {missing} (write 'Not found in the available data.' where evidence is absent)")
    return company.save_context(ctx.settings, ctx.run_id, sym, args)


def finalize_research(ctx: ToolContext, args: Dict) -> Dict:
    """Write the final report (markdown), CSV leaderboard and XLSX from the store + recorded company context."""
    from research_agent import fixed_report
    if not ctx.fixed_symbols:
        raise ToolError("finalize_research is for fixed-universe runs")
    stats = {"backtests": {k: v for k, v in ctx.stats.items() if k in ("screen", "validated")},
             "strategies_proposed": ctx.strategies_proposed, "rag_questions": ctx.stats.get("rag_questions", {}),
             "rag_cost_usd": ctx.stats.get("rag_cost_usd", 0.0), "timeframes": list(ctx.settings.timeframes)}
    out = fixed_report.build(ctx.settings, ctx.run_id, ctx.fixed_symbols, stats,
                             str(args.get("rag_assessment") or ""))
    ctx.stats["final"] = out["paths"]
    return out


def _brief(t: Dict) -> Dict:
    m = t["metrics"]
    return {"symbol": t["symbol"], "strategy": t["strategy"], "timeframe": t["timeframe"],
            "phase": t["phase"], "grade": t.get("grade"),
            "returnPct": m.get("returnPct"), "bhReturnPct": m.get("bhReturnPct"), "vsBHPct": m.get("vsBHPct"),
            "maxDDPct": m.get("maxDDPct"), "sharpe": m.get("sharpe"), "trades": m.get("trades"),
            "oosVsBHPct": m.get("oosVsBHPct"), "folds": f"{m['foldsWon']}/{m['foldsRun']}" if m.get("foldsRun") else None,
            "why": (t.get("why") or [])[:2], "as_of": t["as_of"]}


# ---------------------------------------------------------------- registry
_SYM = {"type": "array", "items": {"type": "string"}, "maxItems": MAX_SYMBOLS}

TOOLS: Dict[str, Dict[str, Any]] = {
    "refresh_universe": {"fn": refresh_universe, "blocking": True,
        "description": "Refresh the NASDAQ common-stock universe from NASDAQ Trader's daily listing (skips if refreshed in the last 20h unless force).",
        "schema": {"type": "object", "properties": {"force": {"type": "boolean"}}}},
    "screen_universe": {"fn": screen_universe, "blocking": True,
        "description": f"Screen up to {MAX_SCREEN_BATCH} not-yet-screened-today NASDAQ stocks in parallel (liquidity, price, ATR%, history, gaps). Call repeatedly until remaining is 0.",
        "schema": {"type": "object", "properties": {"limit": {"type": "integer"}, "force": {"type": "boolean"}}}},
    "get_shortlist": {"fn": get_shortlist, "blocking": False,
        "description": "Top screened stocks by score (liquidity 45%, volatility fit 35%, gap safety 20%).",
        "schema": {"type": "object", "properties": {"limit": {"type": "integer"}, "uptrend_only": {"type": "boolean"}}}},
    "market_snapshot": {"fn": market_snapshot, "blocking": True,
        "description": "Recent behaviour of up to 10 stocks: returns 1m/3m/1y/5y, distance from SMA50/200, RSI14, ATR%, drawdown from 1y high.",
        "schema": {"type": "object", "properties": {"symbols": _SYM}, "required": ["symbols"]}},
    "list_strategies": {"fn": list_strategies, "blocking": False,
        "description": "Every testable strategy: builtin (hand-written FTMO rules), team (JSON specs), ai (accepted proposals), with default params.",
        "schema": {"type": "object", "properties": {"kinds": {"type": "array", "items": {"type": "string", "enum": ["builtin", "team", "ai"]}}}}},
    "strategy_schema": {"fn": strategy_schema, "blocking": False,
        "description": "The JSON contract a new strategy must follow (indicators, operators, limits, naming rules).",
        "schema": {"type": "object", "properties": {}}},
    "propose_strategy": {"fn": propose_strategy, "blocking": False,
        "description": "Submit a NEW strategy as a JSON spec plus a rationale. It is validated against the contract, named AI_<NAME>_<hash>, logged, and becomes testable. Rejections return every error.",
        "schema": {"type": "object", "properties": {"spec": {"type": "object"}, "rationale": {"type": "string"}},
                   "required": ["spec", "rationale"]}},
    "run_backtests": {"fn": run_backtests, "blocking": True,
        "description": f"Screen-phase backtests on the project engine: each strategy (default params) on each stock on EVERY configured timeframe ({', '.join(TIMEFRAMES)}), each vs Buy & Hold on the same bars, US-stock costs. At most {MAX_JOBS_PER_CALL} backtests per call (stocks x strategies x {len(TIMEFRAMES)} timeframes) — with 'all' strategies that is one stock per call. Already-tested combinations are skipped.",
        "schema": {"type": "object", "properties": {"symbols": _SYM,
                   "strategies": {"type": "array", "items": {"type": "string"}, "maxItems": MAX_STRATEGIES}},
                   "required": ["symbols", "strategies"]}},
    "validate_top": {"fn": validate_top, "blocking": True,
        "description": "Full robustness grading (out-of-sample, cost ladder, top-trade removal, walk-forward, parameter sensitivity) of each stock's best strategy x timeframe screen results (all timeframes compete for the top_n slots). Grades FAILED/FRAGILE/MODERATE/ROBUST.",
        "schema": {"type": "object", "properties": {"symbols": _SYM, "top_n": {"type": "integer"}}, "required": ["symbols"]}},
    "lookup_results": {"fn": lookup_results, "blocking": False,
        "description": "Stored test results (any run), filtered by symbol/strategy/timeframe/phase. Use before testing to avoid repeats.",
        "schema": {"type": "object", "properties": {"symbol": {"type": "string"}, "strategy": {"type": "string"},
                   "timeframe": {"type": "string", "enum": list(TIMEFRAMES)},
                   "phase": {"type": "string", "enum": ["screen", "validated"]}, "limit": {"type": "integer"}}}},
    "get_leaderboard": {"fn": get_leaderboard, "blocking": False,
        "description": "Ranked leaderboard: Stock, Strategy, Timeframe, Return, Buy & Hold, Outperformance, Robustness, Verdict (Recommended/Watch/Rejected, fixed policy) and Reason. best_per_stock=true gives the best strategy x timeframe per stock.",
        "schema": {"type": "object", "properties": {"limit": {"type": "integer"}, "best_per_stock": {"type": "boolean"},
                   "verdicts": {"type": "array", "items": {"type": "string", "enum": ["Recommended", "Watch", "Rejected"]}}}}},
    "export_leaderboard": {"fn": export_leaderboard, "blocking": False,
        "description": "Write the leaderboard to CSV and Excel in the research exports folder.",
        "schema": {"type": "object", "properties": {"limit": {"type": "integer"}, "best_per_stock": {"type": "boolean"}}}},
    "write_report": {"fn": write_report, "blocking": False,
        "description": "Save the final research report (markdown) to the research reports folder. Numbers must come from tool results.",
        "schema": {"type": "object", "properties": {"title": {"type": "string"}, "markdown": {"type": "string"}},
                   "required": ["title", "markdown"]}},
}

_STOCK = {"type": "string"}
TOOLS.update({
    "screen_symbols": {"fn": screen_symbols, "blocking": True,
        "description": "FIXED-UNIVERSE RUNS: market/data context for the run's stocks — the deterministic daily screen (data date, price, liquidity, ATR%, history, 1y return, pass/fail reason). Omit symbols for all of them.",
        "schema": {"type": "object", "properties": {"symbols": _SYM}}},
    "company_coverage": {"fn": company_coverage, "blocking": True,
        "description": "Deterministic inventory of the company evidence in the read-only RAG for one stock (statements, earnings, filing text, refused FMP endpoints) with evidence quality Strong/Partial/Missing and an explicit list of MISSING evidence.",
        "schema": {"type": "object", "properties": {"symbol": _STOCK}, "required": ["symbol"]}},
    "company_research": {"fn": company_research, "blocking": True,
        "description": f"Ask the read-only company RAG one question about one stock (financial results, risks, guidance, changes vs the previous period, management information). Answers come only from stored FMP data / filing text, with citations and number checks; absent evidence returns 'Not found in the available data.'. At most {MAX_RAG_QUESTIONS} per stock.",
        "schema": {"type": "object", "properties": {"symbol": _STOCK, "question": {"type": "string"}},
                   "required": ["symbol", "question"]}},
    "record_company_context": {"fn": record_company_context, "blocking": False,
        "description": "Record the company-context write-up for one stock, using ONLY company_research / company_coverage results. Fields: financial_condition, risks, guidance, management, company_context (one line for the leaderboard), final_reason (combines quantitative and company evidence; must not change the deterministic verdict). Write 'Not found in the available data.' for anything without evidence.",
        "schema": {"type": "object", "properties": {"symbol": _STOCK, "financial_condition": {"type": "string"},
                   "risks": {"type": "string"}, "guidance": {"type": "string"}, "management": {"type": "string"},
                   "company_context": {"type": "string"}, "final_reason": {"type": "string"}},
                   "required": ["symbol", "financial_condition", "risks", "guidance", "management",
                                "company_context", "final_reason"]}},
    "finalize_research": {"fn": finalize_research, "blocking": True,
        "description": "FIXED-UNIVERSE RUNS, last step: write the final markdown report, CSV leaderboard and XLSX. Ranks, numbers, verdicts and coverage come from code; you supply rag_assessment: does RAG add useful company context here, with evidence from this run only.",
        "schema": {"type": "object", "properties": {"rag_assessment": {"type": "string"}}, "required": ["rag_assessment"]}},
})

UNIVERSE_ONLY = frozenset({"refresh_universe", "screen_universe", "get_shortlist", "export_leaderboard", "write_report"})
FIXED_ONLY = frozenset({"screen_symbols", "company_coverage", "company_research", "record_company_context",
                        "finalize_research"})
ALLOWED = frozenset(f"mcp__{SERVER}__{n}" for n in TOOLS)


def tool_names(ctx: "ToolContext") -> List[str]:
    """The tools this run's session gets: universe runs never see the fixed-run tools and vice versa."""
    drop = UNIVERSE_ONLY if ctx.fixed_symbols else FIXED_ONLY
    return [n for n in TOOLS if n not in drop]


def allowed_names(ctx: "ToolContext") -> frozenset:
    return frozenset(f"mcp__{SERVER}__{n}" for n in tool_names(ctx))


async def call(ctx: ToolContext, name: str, args: Dict) -> Dict:
    """Run one handler; errors come back as data the agent can read and correct."""
    spec = TOOLS[name]
    t0 = time.time()
    try:
        if spec["blocking"]:
            result = await asyncio.to_thread(spec["fn"], ctx, args or {})
        else:
            result = spec["fn"](ctx, args or {})
    except (ToolError, KeyError, ValueError) as e:
        result = {"error": str(e)}
    except Exception as e:                                       # noqa: BLE001
        result = {"error": f"{type(e).__name__}: {str(e)[:300]}"}
    ctx.record(name, args, result, time.time() - t0)
    return result


def build_server(ctx: ToolContext):
    """The in-process MCP server the SDK session sees. Nothing else is registered."""
    from claude_agent_sdk import create_sdk_mcp_server, tool

    def make(name: str, spec: Dict[str, Any]):
        @tool(name, spec["description"], spec["schema"])
        async def _handler(args: Dict[str, Any]) -> Dict[str, Any]:
            result = await call(ctx, name, args)
            return {"content": [{"type": "text", "text": json.dumps(result, default=str)[:60_000]}],
                    **({"is_error": True} if "error" in result else {})}
        return _handler

    return create_sdk_mcp_server(name=SERVER, version="1.0.0",
                                 tools=[make(n, TOOLS[n]) for n in tool_names(ctx)])
