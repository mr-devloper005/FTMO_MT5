"""Verdict policy and the leaderboard.

Every tested (stock, strategy) pair gets exactly one verdict and one plain
reason. The policy is fixed and in code, so the same evidence always earns the
same word — the agent reports verdicts, it never chooses them.

    Recommended   validated, robustness grade >= Settings.promote_grade (MODERATE),
                  profitable, and beats Buy & Hold by >= target_outperformance_pts
    Watch         profitable and beats the target, but not yet validated or not
                  robust enough
    Rejected      too few trades, lost money, trails Buy & Hold, or beats it by
                  less than the target

Outperformance = strategy return − Buy & Hold return over the same bars, in
percentage points of starting capital ("50" means 50 points better, e.g. +130%
vs +80%). The target is a Setting, not a hidden constant.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from research_agent.config import GRADES, VERDICTS, Settings
from research_agent.store import Store


def verdict_for(row: Dict[str, Any], settings: Settings) -> Dict[str, str]:
    """(verdict, reason) for one stored test row. Pure."""
    m = row["metrics"]
    ret, bh, vs = m.get("returnPct") or 0.0, m.get("bhReturnPct") or 0.0, m.get("vsBHPct") or 0.0
    trades, grade, target = m.get("trades") or 0, row.get("grade"), settings.target_outperformance_pts
    head = f"{ret:+.1f}% vs Buy & Hold {bh:+.1f}%"
    if trades < settings.min_trades:
        return {"verdict": "Rejected", "reason": f"Only {trades} trades — too few to tell a rule from luck ({head})."}
    if ret <= 0:
        return {"verdict": "Rejected", "reason": f"Lost money: {head}."}
    if vs < 0:
        return {"verdict": "Rejected", "reason": f"Trails Buy & Hold by {-vs:.1f} pts ({head}) — holding the stock was better."}
    if vs < target:
        return {"verdict": "Rejected", "reason": f"Beats Buy & Hold by {vs:.1f} pts ({head}); target is {target:.0f} pts."}
    if row["phase"] != "validated" or grade is None:
        return {"verdict": "Watch", "reason": f"Beats Buy & Hold by {vs:.1f} pts ({head}); not yet robustness-tested."}
    if GRADES.index(grade) < GRADES.index(settings.promote_grade):
        why = (row.get("why") or ["did not pass the robustness gates"])[0]
        return {"verdict": "Watch", "reason": f"Beats Buy & Hold by {vs:.1f} pts ({head}) but grades {grade}: {why}"}
    parts = [f"Beats Buy & Hold by {vs:.1f} pts ({head})", f"grade {grade}"]
    if m.get("oosVsBHPct") is not None:
        parts.append(f"out-of-sample {m['oosVsBHPct']:+.1f} pts")
    if m.get("foldsRun"):
        parts.append(f"wins {m['foldsWon']}/{m['foldsRun']} walk-forward folds")
    if m.get("survivesTicks"):
        parts.append(f"survives {m['survivesTicks']:.0f}-tick slippage")
    return {"verdict": "Recommended", "reason": "; ".join(parts) + "."}


def _robustness(row: Dict[str, Any]) -> str:
    if row["phase"] != "validated" or not row.get("grade"):
        return "Not tested"
    s = row["metrics"].get("robustnessScore")
    return f"{row['grade']} ({s:.0f}/100)" if s is not None else row["grade"]


def leaderboard(settings: Settings, limit: int = 100, best_per_stock: bool = False,
                verdicts: Optional[List[str]] = None, symbols: Optional[List[str]] = None,
                current_only: bool = True) -> List[Dict[str, Any]]:
    """Ranked rows: verdict, then grade, then robustness score, then outperformance.

    One row per (stock, strategy, timeframe): the validated result when there is
    one, else the screen result. ``current_only`` keeps each stock's latest data date, so
    a leaderboard never mixes yesterday's numbers with today's.
    """
    store = Store(settings.db_path)
    kinds = {s["name"]: s["kind"] for s in store.strategies()}
    # Only the configured timeframes count; a result from a timeframe since removed
    # from config.TIMEFRAMES stays in the store but leaves the leaderboard.
    rows = [r for r in store.tests(limit=1_000_000) if r["timeframe"] in settings.timeframes]
    if symbols:
        wanted = set(symbols)
        rows = [r for r in rows if r["symbol"] in wanted]
    if current_only:
        latest: Dict[str, str] = {}
        for r in rows:
            latest[r["symbol"]] = max(latest.get(r["symbol"], ""), r["as_of"] or "")
        rows = [r for r in rows if (r["as_of"] or "") == latest[r["symbol"]]]

    chosen: Dict[tuple, Dict] = {}
    for r in rows:
        key = (r["symbol"], r["strategy"], r["timeframe"])
        cur = chosen.get(key)
        if cur is None or (r["phase"] == "validated" and cur["phase"] != "validated") or \
                (r["phase"] == cur["phase"] and (r["created_at"] or "") > (cur["created_at"] or "")):
            chosen[key] = r

    names = {}
    out = []
    for r in chosen.values():
        v = verdict_for(r, settings)
        m = r["metrics"]
        if r["symbol"] not in names:
            names[r["symbol"]] = store.universe_name(r["symbol"]) or ""
        out.append({
            "Stock": r["symbol"],
            "Company": _short_name(names[r["symbol"]]),
            "Strategy": r["strategy"],
            "Timeframe": r["timeframe"],
            "Source": kinds.get(r["strategy"], "builtin"),
            "Return %": m.get("returnPct"),
            "Buy & Hold %": m.get("bhReturnPct"),
            "Outperformance (pts)": m.get("vsBHPct"),
            "Robustness": _robustness(r),
            "Verdict": v["verdict"],
            "Reason": v["reason"],
            "CAGR %": m.get("cagrPct"),
            "B&H CAGR %": m.get("bhCagrPct"),
            "Max DD %": m.get("maxDDPct"),
            "B&H Max DD %": m.get("bhDDPct"),
            "Sharpe": m.get("sharpe"),
            "Trades": m.get("trades"),
            "Win %": m.get("winRate"),
            "Profit factor": m.get("pf"),
            "OOS vs B&H (pts)": m.get("oosVsBHPct"),
            "Folds won": f"{m['foldsWon']}/{m['foldsRun']}" if m.get("foldsRun") else None,
            "Survives slippage (ticks)": m.get("survivesTicks"),
            "Param variations held %": m.get("variationsHeldPct"),
            "Years": _calendar_years(m),
            "Parameters": r["params"],
            "Data as of": r["as_of"],
            "Tested at": r["created_at"],
            "_grade_rank": GRADES.index(r["grade"]) if r.get("grade") else -1,
            "_score": m.get("robustnessScore") or 0.0,
            "_fingerprint": r["fingerprint"],
        })
    out.sort(key=lambda x: (-VERDICTS.index(x["Verdict"]), -x["_grade_rank"], -x["_score"],
                            -(x["Outperformance (pts)"] or -1e9)))
    if verdicts:
        out = [x for x in out if x["Verdict"] in verdicts]
    if best_per_stock:
        seen, best = set(), []
        for x in out:
            if x["Stock"] not in seen:
                seen.add(x["Stock"])
                best.append(x)
        out = best
    for i, x in enumerate(out, 1):
        x["Rank"] = i
    return out[:limit]


def summary(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    tally = {v: 0 for v in reversed(VERDICTS)}
    for r in rows:
        tally[r["Verdict"]] += 1
    beat = sum(1 for r in rows if (r["Outperformance (pts)"] or 0) > 0)
    return {"rows": len(rows), "verdicts": tally, "beatBuyAndHold": beat,
            "stocks": len({r["Stock"] for r in rows}), "strategies": len({r["Strategy"] for r in rows}),
            "timeframes": len({r["Timeframe"] for r in rows})}


def _short_name(name: str) -> str:
    """'Apple Inc. - Common Stock' -> 'Apple Inc.'"""
    return name.split(" - ")[0].strip() if name else ""


def _calendar_years(m: Dict[str, Any]) -> Optional[float]:
    """The real span of the bars tested, from the first and last bar dates.

    The Lab's own `windowYears` divides bars by a futures calendar (e.g. 5,716
    hourly bars a year); US stocks trade 6.5 hours a day, so on intraday
    timeframes it understates the window ~3x. Dates do not have that problem.
    """
    if m.get("from") and m.get("to"):
        return round((m["to"] - m["from"]) / (365.25 * 86400), 2)
    return m.get("windowYears")
