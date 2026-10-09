"""The research output, read from the store — the one source for every view.

The web dashboard (``web.py``), the Excel workbook (``export.py``) and the
Google Sheet (``sheets.py``) all call this module, so the three can never
disagree: there is no second query, no second verdict policy, no cached copy.
Verdicts, robustness labels and reasons come from ``ranking.leaderboard``;
screening rows come straight from the ``screens`` table.

Five views, matching the five report tabs:

    summary          stocks scanned/passed, shortlisted, strategies tested,
                     backtests, Recommended/Watch/Rejected, settings, recent runs
    timeframes       the 10 configured timeframes (config.TIMEFRAMES): history used,
                     tests, wins vs Buy & Hold, verdicts and best result on each
    leaderboard      best strategy x timeframe per stock, ranked
    strategy_tests   every stock x strategy x timeframe tested (validated result when there is one)
    screening        every stock screened, PASS/FAIL with the exact reason
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from research_agent import ranking
from research_agent.config import SCREEN_TIMEFRAME, TIMEFRAME_LABELS, Settings
from research_agent.quality import model as quality_model
from research_agent.store import Store

GLOSSARY = [
    ("Timeframe", "Bar size the strategy was tested on. Every strategy runs on every stock on each of the 10 "
                  "configured timeframes; Buy & Hold is measured on the same bars, so each row compares like with like."),
    ("Return", "Strategy profit over the whole test window, % of starting capital ($100,000)."),
    ("Buy & Hold", "What simply buying the stock on day one and holding it to the end returned, same window."),
    ("Outperformance", "Strategy return minus Buy & Hold return, in percentage points (+130% vs +80% = 50 pts)."),
    ("Robustness", "Lab grade from 5 stress tests: out-of-sample, higher costs, removing the best trades, "
                   "5 separate time periods, nearby parameter values. FAILED < FRAGILE < MODERATE < ROBUST."),
    ("Recommended", "Validated, grade MODERATE or better, profitable, and beats Buy & Hold by the target."),
    ("Watch", "Profitable and beats Buy & Hold by the target, but not robust enough (or not yet stress-tested)."),
    ("Rejected", "Too few trades, lost money, trails Buy & Hold, or beats it by less than the target."),
    ("Screen PASS", "Liquid (median $ volume), price above the floor, daily range (ATR %) inside the band, "
                    "and enough history to test out-of-sample."),
]


def _params(p: Optional[Dict]) -> str:
    return ", ".join(f"{k}={v}" for k, v in (p or {}).items())


def _r(v: Any, n: int = 2) -> Any:
    return round(v, n) if isinstance(v, float) else v


# ---------------------------------------------------------------- summary
def summary(settings: Settings) -> Dict[str, Any]:
    store = Store(settings.db_path)
    active = set(store.active_symbols())
    with store.conn() as c:
        scr = c.execute("""SELECT COUNT(*), SUM(passed=1), SUM(error IS NOT NULL), MAX(screened_at), MAX(as_of)
                           FROM screens""").fetchone()
        runs = [dict(r) for r in c.execute(
            "SELECT run_id, kind, started_at, finished_at, summary_json FROM runs ORDER BY started_at DESC LIMIT 8")]
        tested_kinds = {r[0]: r[1] for r in c.execute(
            """SELECT s.kind, COUNT(DISTINCT t.strategy) FROM tests t
               LEFT JOIN strategies s ON s.name = t.strategy GROUP BY s.kind""")}
    rows = ranking.leaderboard(settings, limit=1_000_000)
    verdicts = ranking.summary(rows)["verdicts"]
    tfs = timeframes(settings, rows)
    tests = store.test_count()
    shortlist: List[str] = []
    for r in runs:
        s = json.loads(r.pop("summary_json") or "{}")
        r["summary"] = {k: s.get(k) for k in ("seconds", "summary", "turns", "cost_usd", "error", "tool_calls",
                                              "strategies_proposed") if s.get(k) is not None}
        if not shortlist and s.get("shortlist"):
            shortlist = s["shortlist"]
    tested_symbols = sorted({r["Stock"] for r in rows})
    return {
        "universe": len(active),
        "scanned": scr[0] or 0,
        "passed": scr[1] or 0,
        "failed": (scr[0] or 0) - (scr[1] or 0),
        "data_errors": scr[2] or 0,
        "last_screened_at": scr[3],
        "shortlisted": len(tested_symbols),
        "shortlist": shortlist or tested_symbols,
        "strategies_tested": len({r["Strategy"] for r in rows}),
        "timeframes": tfs,
        "timeframe_list": list(settings.timeframes),
        "screen_timeframe": SCREEN_TIMEFRAME,
        "strategies_by_source": {k or "builtin": v for k, v in tested_kinds.items()},
        "strategies_available": {k: len(store.strategies(kinds=[k])) for k in ("builtin", "team", "ai")},
        "backtests": tests.get("screen", 0),
        "validations": tests.get("validated", 0),
        "pairs": len(rows),
        "beat_buy_and_hold": sum(1 for r in rows if (r["Outperformance (pts)"] or 0) > 0),
        "verdicts": verdicts,
        "data_as_of": max((r["Data as of"] or "" for r in rows), default=None) or scr[4],
        "settings": {"target_pts": settings.target_outperformance_pts, "promote_grade": settings.promote_grade,
                     "bars": settings.bars, "years": round(settings.bars / 252, 1),
                     "costs": f"${settings.commission_per_unit}/share + {settings.slippage_ticks:g} tick slippage",
                     "min_dollar_volume_m": settings.min_dollar_volume / 1e6, "min_price": settings.min_price,
                     "atr_band": [settings.min_atr_pct, settings.max_atr_pct],
                     "min_history_bars": settings.min_history_bars},
        "runs": runs,
        "glossary": [{"term": t, "meaning": m} for t, m in GLOSSARY],
    }


# ---------------------------------------------------------------- timeframes
def history_note(settings: Settings, tf: str) -> str:
    n = settings.history_bars(tf)
    return f"last {n:,} bars" if n else "all available history"


def timeframes(settings: Settings, rows: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """One entry per configured timeframe, in config order — even when nothing has run on it yet."""
    rows = rows if rows is not None else ranking.leaderboard(settings, limit=1_000_000)
    out = []
    for tf in settings.timeframes:
        mine = [r for r in rows if r["Timeframe"] == tf]
        v = ranking.summary(mine)["verdicts"]
        best = max(mine, key=lambda r: r["Outperformance (pts)"] if r["Outperformance (pts)"] is not None else -1e9,
                   default=None)
        years = sorted(r["Years"] for r in mine if r.get("Years"))
        out.append({
            "Timeframe": tf, "Label": TIMEFRAME_LABELS.get(tf, tf), "History": history_note(settings, tf),
            "Typical window (years)": years[len(years) // 2] if years else None,
            "Tests": len(mine), "Beat Buy & Hold": sum(1 for r in mine if (r["Outperformance (pts)"] or 0) > 0),
            "Recommended": v["Recommended"], "Watch": v["Watch"], "Rejected": v["Rejected"],
            "Best result": (f"{best['Stock']} · {best['Strategy']} · {best['Outperformance (pts)']:+.1f} pts"
                            if best and best["Outperformance (pts)"] is not None else None),
        })
    return out


TIMEFRAME_COLS = ["Timeframe", "Label", "History", "Typical window (years)", "Tests", "Beat Buy & Hold",
                  "Recommended", "Watch", "Rejected", "Best result"]


# ---------------------------------------------------------------- tables
LEADERBOARD_COLS = ["Rank", "Stock", "Company", "Best Strategy", "Timeframe", "Return %", "Buy & Hold %",
                    "Outperformance (pts)", "Robustness", "Verdict", "Reason", "Trades", "Max DD %",
                    "Window (years)", "Combinations tried", "Data as of"]


def leaderboard(settings: Settings, limit: int = 1000) -> List[Dict[str, Any]]:
    """Best strategy x timeframe per stock, in ranking order."""
    all_rows = ranking.leaderboard(settings, limit=1_000_000)
    tried: Dict[str, int] = {}
    for r in all_rows:
        tried[r["Stock"]] = tried.get(r["Stock"], 0) + 1
    out = []
    for r in ranking.leaderboard(settings, limit=limit, best_per_stock=True):
        out.append({"Rank": r["Rank"], "Stock": r["Stock"], "Company": r["Company"], "Best Strategy": r["Strategy"],
                    "Timeframe": r["Timeframe"],
                    "Source": r["Source"], "Return %": _r(r["Return %"]), "Buy & Hold %": _r(r["Buy & Hold %"]),
                    "Outperformance (pts)": _r(r["Outperformance (pts)"]), "Robustness": r["Robustness"],
                    "Verdict": r["Verdict"], "Reason": r["Reason"], "Trades": r["Trades"],
                    "Max DD %": _r(r["Max DD %"]), "Window (years)": r["Years"],
                    "Combinations tried": tried.get(r["Stock"], 0),
                    "Data as of": r["Data as of"], "Parameters": _params(r["Parameters"])})
    return out


TEST_COLS = ["Symbol", "Strategy", "Timeframe", "Source", "Parameters", "Return %", "Buy & Hold %",
             "Outperformance (pts)", "Trades", "Max DD %", "Sharpe", "Win %", "Robustness", "Verdict",
             "Window (years)", "Data as of"]


def strategy_tests(settings: Settings, symbol: Optional[str] = None,
                   timeframe: Optional[str] = None) -> List[Dict[str, Any]]:
    """Every stock x strategy x timeframe: the validated result when there is one, else the screen backtest."""
    rows = ranking.leaderboard(settings, limit=1_000_000, symbols=[symbol.upper()] if symbol else None)
    if timeframe:
        rows = [r for r in rows if r["Timeframe"] == timeframe]
    order = {tf: i for i, tf in enumerate(settings.timeframes)}
    out = [{"Symbol": r["Stock"], "Company": r["Company"], "Strategy": r["Strategy"], "Timeframe": r["Timeframe"],
            "Source": r["Source"], "Window (years)": r["Years"], "_tf": order.get(r["Timeframe"], 99),
            "Parameters": _params(r["Parameters"]), "Return %": _r(r["Return %"]),
            "Buy & Hold %": _r(r["Buy & Hold %"]), "Outperformance (pts)": _r(r["Outperformance (pts)"]),
            "Trades": r["Trades"], "Max DD %": _r(r["Max DD %"]), "Sharpe": _r(r["Sharpe"]),
            "Win %": _r(r["Win %"]), "Robustness": r["Robustness"], "Verdict": r["Verdict"],
            "Reason": r["Reason"], "Data as of": r["Data as of"]} for r in rows]
    out.sort(key=lambda x: (x["Symbol"], -(x["Outperformance (pts)"] if x["Outperformance (pts)"] is not None else -1e9),
                            x["_tf"]))
    for x in out:
        x.pop("_tf")
    return out


SCREEN_COLS = ["Symbol", "Company", "Result", "Score", "Price", "Liquidity ($M/day)", "ATR %", "Trend",
               "1Y Return %", "Gap p95 %", "Reason", "Screened at"]


def screening(settings: Settings, status: str = "all") -> List[Dict[str, Any]]:
    """Every screened stock with PASS/FAIL and the exact reason. Passers first, by score."""
    store = Store(settings.db_path)
    where = {"pass": "WHERE s.passed=1", "fail": "WHERE s.passed=0"}.get(status, "")
    with store.conn() as c:
        rows = [dict(r) for r in c.execute(
            f"""SELECT s.*, u.name FROM screens s LEFT JOIN universe u ON u.symbol = s.symbol {where}
                ORDER BY s.passed DESC, s.score DESC, s.dollar_volume DESC""")]
    out = []
    for r in rows:
        trend = None if r["trend_up"] is None or r["price"] is None else ("Uptrend" if r["trend_up"] else "Downtrend")
        reason = r["reason"] or ""
        if r["error"]:
            reason = f"{reason} ({r['error'][:120]})" if reason else r["error"][:160]
        out.append({"Symbol": r["symbol"], "Company": (r["name"] or "").split(" - ")[0],
                    "Result": "PASS" if r["passed"] else "FAIL", "Score": _r(r["score"], 1) if r["passed"] else None,
                    "Price": _r(r["price"]),
                    "Liquidity ($M/day)": round(r["dollar_volume"] / 1e6, 1) if r["dollar_volume"] is not None else None,
                    "ATR %": _r(r["atr_pct"]), "Trend": trend, "1Y Return %": _r(r["return_1y"], 1),
                    "Gap p95 %": _r(r["gap_p95"]), "Reason": reason, "Screened at": r["screened_at"]})
    return out


def summary_table(s: Dict[str, Any]) -> List[List[Any]]:
    """The summary as label/value rows for a spreadsheet tab."""
    v = s["verdicts"]
    st = s["settings"]
    rows = [["FTMO AI Research — run summary", ""], ["Data as of", s["data_as_of"]],
            ["Last screened (UTC)", s["last_screened_at"]], ["", ""],
            ["SCREENING", ""], ["NASDAQ common stocks in universe", s["universe"]],
            ["Stocks scanned", s["scanned"]], ["Passed screen", s["passed"]], ["Failed screen", s["failed"]],
            ["Shortlisted for backtesting", s["shortlisted"]], ["Shortlist", ", ".join(s["shortlist"])], ["", ""],
            ["TIMEFRAMES TESTED (10)", ", ".join(s["timeframe_list"])],
            ["  history", f"daily: last {s['settings']['bars']:,} bars (~{s['settings']['years']} years); every other "
                          "timeframe: all the history Yahoo serves — see the Timeframes tab for each window"],
            ["  screening", f"on {s['screen_timeframe']} bars (a property of the stock, not the strategy)"],
            ["", ""],
            ["STRATEGIES & BACKTESTS", ""], ["Strategies tested", s["strategies_tested"]],
            ["  by source", ", ".join(f"{k}: {n}" for k, n in s["strategies_by_source"].items())],
            ["Backtests (stock x strategy x timeframe)", s["backtests"]],
            ["Robustness validations", s["validations"]],
            ["Pairs that beat Buy & Hold", s["beat_buy_and_hold"]], ["", ""],
            ["VERDICTS", ""], ["Recommended", v.get("Recommended", 0)], ["Watch", v.get("Watch", 0)],
            ["Rejected", v.get("Rejected", 0)], ["", ""],
            ["SETTINGS", ""], ["Outperformance target", f"{st['target_pts']:g} pts vs Buy & Hold"],
            ["Robustness needed for Recommended", st["promote_grade"]],
            ["Backtest window", f"daily: {st['bars']} bars (~{st['years']} years); other timeframes: all history"],
            ["Costs", st["costs"]],
            ["Screen: liquidity / price / ATR band", f">= ${st['min_dollar_volume_m']:g}M/day, >= ${st['min_price']:g}, "
                                                     f"{st['atr_band'][0]}-{st['atr_band'][1]}%"],
            ["", ""], ["HOW TO READ THIS", ""]]
    rows += [[g["term"], g["meaning"]] for g in s["glossary"]]
    rows += [["", ""], ["Research only", "Backtests on historical daily bars. No orders are placed by this system."]]
    return rows


def report_tables(settings: Settings) -> Dict[str, Dict[str, Any]]:
    """Every report tab as columns + rows — what Excel and Google Sheets both write."""
    s = summary(settings)
    def grid(cols, dicts):
        return {"columns": cols, "rows": [[d.get(c) for c in cols] for d in dicts]}
    return {
        "Summary": {"columns": None, "rows": summary_table(s)},
        "Timeframes": grid(TIMEFRAME_COLS, s["timeframes"]),
        "Leaderboard": grid(LEADERBOARD_COLS, leaderboard(settings)),
        "Strategy Tests": grid(TEST_COLS, strategy_tests(settings)),
        "Screening": grid(SCREEN_COLS, screening(settings)),
    }


# ---------------------------------------------------------------- quality x tested edge
QUALITY_STRONG = 70.0          # Rahul's "Medium" bucket floor: Medium or High counts as strong quality
EDGE_VERDICTS = ("Recommended", "Watch")   # a tested rule beat Buy & Hold by the target

GRID = {
    (True, True): ("Best trading candidate", "Strong company and a tested rule that beat holding it."),
    (True, False): ("Good company, no trading edge", "Quality is strong but no rule beat simply holding it."),
    (False, True): ("Edge exists, weaker company", "A rule beat holding it, but quality is weaker: use tighter risk."),
    (False, False): ("Skip", "Weaker quality and no tested trading edge."),
}


def _ftmo_status(settings: Settings, store: Store, symbol: str, best: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    if symbol in best:
        r = best[symbol]
        return {"tested": True, "verdict": r["Verdict"], "strategy": r["Best Strategy"], "timeframe": r["Timeframe"],
                "return_pct": r["Return %"], "bh_pct": r["Buy & Hold %"], "outperformance": r["Outperformance (pts)"],
                "robustness": r["Robustness"], "reason": r["Reason"]}
    scr = store.screen(symbol)
    if symbol not in set(store.active_symbols()):
        why = "Not in our NASDAQ universe (listed on another exchange), so not backtested yet."
    elif scr and not scr.get("passed"):
        why = f"Not backtested: failed our screen ({scr.get('reason')})."
    else:
        why = "Not backtested yet."
    return {"tested": False, "verdict": None, "reason": why}


def quality(settings: Settings) -> Dict[str, Any]:
    """Quality scores (Rahul's lenses on FMP) next to FTMO's tested edge, placed on the decision grid."""
    from research_agent.quality import build
    store = Store(settings.db_path)
    best = {r["Stock"]: r for r in leaderboard(settings)}
    rows = []
    for q in build.load(settings):
        sym = q["symbol"]
        ftmo = _ftmo_status(settings, store, sym, best)
        strong = (q["composite"] or 0) >= QUALITY_STRONG
        if ftmo["tested"]:
            quadrant, meaning = GRID[(strong, ftmo["verdict"] in EDGE_VERDICTS)]
        else:
            quadrant, meaning = "Not tested yet", ftmo["reason"]
        val = (q["inputs"] or {}).get("valuation") or {}
        fund = ((q["inputs"] or {}).get("fundamentals") or {}).get("summary") or {}
        tech = (q["inputs"] or {}).get("technicals") or {}
        rows.append({
            "Stock": sym, "Company": (store.universe_name(sym) or "").split(" - ")[0], "Sector": (q["inputs"] or {}).get("sector"),
            "Quality": q["composite"], "Bucket": q["bucket"], "Lenses": q["lenses"], "Why": q["why"],
            "Key": {"revenue_growth_pct": fund.get("yoy_revenue_growth_pct"), "net_margin_pct": fund.get("latest_net_margin_pct"),
                    "operating_margin_pct": fund.get("latest_operating_margin_pct"), "fcf_b": fund.get("latest_fcf_b"),
                    "latest_quarter": fund.get("latest_quarter"),
                    "forward_pe": (val.get("target") or {}).get("PE_FY1"),
                    "peer_median_pe": ((val.get("peer_aggregates") or {}).get("median") or {}).get("PE_FY1"),
                    "peers": [p["ticker"] for p in val.get("peers") or []], "peer_source": (q["inputs"] or {}).get("peer_source"),
                    "price": tech.get("spot"), "vs_200d_pct": tech.get("pct_above_sma200_pct"),
                    "rsi14": tech.get("rsi14"), "change_3m_pct": tech.get("price_90d_change_pct"),
                    "change_1y_pct": tech.get("price_1y_change_pct")},
            "FTMO": ftmo, "Grid": quadrant, "Grid meaning": meaning,
            "As of": q["as_of"], "Source": q["source"], "Notes": q["notes"],
        })
    return {"rows": rows, "strong_threshold": QUALITY_STRONG, "edge_verdicts": list(EDGE_VERDICTS),
            "grid": [{"key": f"{int(s)}{int(e)}", "quality_strong": s, "edge": e, "label": GRID[(s, e)][0],
                      "meaning": GRID[(s, e)][1]} for s in (True, False) for e in (True, False)],
            "lens_weights": quality_model.WEIGHTS}
