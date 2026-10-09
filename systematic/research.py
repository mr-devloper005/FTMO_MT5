"""Screen -> validate -> rank: which (symbol, strategy) pairs deserve paper capital.

Every number here comes from the Lab's own functions — ``metrics.measure`` for
the screen and ``verdict.validate`` for the grade — the same code the MCP tools
(``lab_run_matrix``, ``lab_validate_strategy``) wrap. Results are written to
the shared results index, so ``lab_lookup`` sees them too.

Honesty rule: a candidate is promoted only if its *grade* clears ``min_grade``.
A high score never substitutes for a grade. If nothing clears, the portfolio is
empty and the report says so — the Lab's standing result is that most rules do
not beat buy & hold, and this pipeline must not paper over that.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional

from ftmo_mt5.lab import metrics, service, verdict

from systematic.config import GRADES, Settings
from systematic.data import load_completed

Progress = Optional[Callable[[str], None]]

# The benchmark itself, not a candidate: it holds from the first bar, so "beating
# buy & hold" with it is a rounding error, and it would crowd out real rules.
NOT_CANDIDATES = {"BUY_AND_HOLD_BASELINE"}


def spec_for(strategy: str, params: Dict, settings: Settings, position_size: float) -> Dict:
    """The Lab's canonical spec (mcp_server/server.py `_spec`), sized to capital.

    Cost overrides travel in the spec, so research grades a rule under exactly
    the costs the paper broker will charge it. Unset, both use the Lab's own.
    """
    spec = {"strategy": strategy, "strategy_parameters": dict(params or {}),
            "allow_long": True, "allow_short": settings.allow_short,
            "position_size": float(position_size)}
    if settings.commission_per_unit is not None:
        spec["commission"] = float(settings.commission_per_unit)
    if settings.slippage_ticks is not None:
        spec["slippage_ticks"] = float(settings.slippage_ticks)
    return spec


def _record(result: Dict, strategy: str, grade: Optional[str] = None) -> None:
    """Best effort: the shared index is a convenience, never a reason to fail a run."""
    try:
        from mcp_server import results_index
        results_index.record(result, strategy=strategy, verdict=grade, source="systematic")
    except Exception:                                   # noqa: BLE001
        pass


def screen(symbols: List[str], settings: Settings, progress: Progress = None) -> Dict[str, List[Dict]]:
    """Every strategy on its own defaults, per symbol and timeframe.

    Returns, per symbol, every measured row with a `passesScreen` flag. The
    screen is the Lab's own "qualified" test (see ftmo_mt5/lab/predict.py):
    beats buy & hold, enough trades, not one trade carrying it, and a
    benchmark worth beating.
    """
    defaults = {k: v for k, v in service.strategy_defaults().items() if k not in NOT_CANDIDATES}
    out: Dict[str, List[Dict]] = {}
    for symbol in symbols:
        rows: List[Dict] = []
        for tf in settings.timeframes:
            try:
                candles = load_completed(symbol, tf, settings.bars)
            except Exception as e:                          # noqa: BLE001
                rows.append({"symbol": symbol, "timeframe": tf, "error": f"data: {type(e).__name__}: {e}"})
                continue
            if len(candles) < 100:
                rows.append({"symbol": symbol, "timeframe": tf,
                             "error": f"only {len(candles)} completed bars; need at least 100"})
                continue
            size = service.size_for(symbol, candles, metrics.CAPITAL)
            for name, params in defaults.items():
                spec = spec_for(name, params, settings, size)
                try:
                    r = metrics.measure(symbol, tf, spec, candles)
                except Exception as e:                      # noqa: BLE001
                    rows.append({"symbol": symbol, "timeframe": tf, "strategy": name,
                                 "error": f"{type(e).__name__}: {e}"})
                    continue
                r["strategy_parameters"] = dict(params)
                r["positionSize"] = size
                r["passesScreen"] = bool(
                    r["beatsBH"] and r["trades"] >= settings.min_trades
                    and r["topTradeShare"] < settings.max_top_trade_share
                    and abs(r["bh"]) >= verdict.FLAT_BENCHMARK)
                _record(r, name)
                rows.append(r)
        out[symbol] = rows
        if progress:
            passed = sum(1 for r in rows if r.get("passesScreen"))
            progress(f"screened {symbol}: {len([r for r in rows if 'error' not in r])} runs, {passed} passed the screen")
    return out


def shortlist(rows: List[Dict], settings: Settings) -> List[Dict]:
    """What goes to full validation for one symbol.

    Screen-passers first. If none pass, the best few by outperformance still go,
    so the report can say *why* each fell short instead of showing a blank.
    """
    ok = [r for r in rows if "error" not in r and r["trades"] > 0]
    passing = [r for r in ok if r["passesScreen"]]
    pool = passing or ok
    return sorted(pool, key=lambda r: -r["vsBHPct"])[: settings.candidates_per_symbol]


def validate_candidates(screened: Dict[str, List[Dict]], settings: Settings,
                        progress: Progress = None) -> List[Dict]:
    """Full robustness grading for each shortlisted rule."""
    out: List[Dict] = []
    for symbol, rows in screened.items():
        for r in shortlist(rows, settings):
            tf, name = r["timeframe"], r["strategy"]
            candles = load_completed(symbol, tf, settings.bars)
            spec = spec_for(name, r["strategy_parameters"], settings, r["positionSize"])
            try:
                v = verdict.validate(symbol, tf, spec, candles)
            except Exception as e:                          # noqa: BLE001
                out.append({"symbol": symbol, "timeframe": tf, "strategy": name,
                            "verdict": "FAILED", "why": [f"validation crashed: {type(e).__name__}: {e}"],
                            "error": True})
                continue
            _record(v["full"], name, v["verdict"])
            out.append(_summarise(symbol, tf, name, r, spec, v))
            if progress:
                progress(f"validated {symbol} {tf} {name}: {v['verdict']}")
    return out


def _summarise(symbol: str, tf: str, name: str, screen_row: Dict, spec: Dict, v: Dict) -> Dict:
    full, oos = v["full"], v["oos"]
    return {
        "symbol": symbol, "timeframe": tf, "strategy": name,
        "strategy_parameters": spec["strategy_parameters"], "spec": spec,
        "verdict": v["verdict"], "why": v.get("why", []),
        "trades": full["trades"], "returnPct": full["returnPct"],
        "bhReturnPct": full["bhReturnPct"], "vsBHPct": full["vsBHPct"],
        "sharpe": full["sharpe"], "maxDDPct": full["maxDDPct"], "winRate": full["winRate"],
        "pf": full["pf"], "topTradeShare": full["topTradeShare"],
        "oosVsBHPct": oos["vsBHPct"], "oosBeatsBH": oos["beatsBH"],
        "foldsWon": v["foldsWon"], "foldsRun": v["foldsRun"],
        "survivesTicks": v["survivesTicks"], "variationsHeldPct": v["variationsHeldPct"],
        "passedScreen": screen_row.get("passesScreen", False),
        "from": full.get("from"), "to": full.get("to"),
    }


def _clip(x: Optional[float], lo: float, hi: float) -> float:
    return max(lo, min(hi, float(x or 0.0)))


def robustness_score(c: Dict) -> float:
    """0-100, robustness first. Orders candidates *within* a grade; never across.

    Consistency across eras (walk-forward folds) carries the most weight,
    because the goal is consistent profitability, not the best single window.
    """
    folds = (c["foldsWon"] / c["foldsRun"]) if c.get("foldsRun") else 0.0
    held = (c["variationsHeldPct"] or 0.0) / 100.0
    ticks = _clip(c["survivesTicks"] / 8.0, 0, 1)
    oos = (_clip(c["oosVsBHPct"] / 20.0, -1, 1) + 1) / 2
    sharpe = (_clip(c["sharpe"] / 2.0, -1, 1) + 1) / 2
    dd = _clip(c["maxDDPct"] / 30.0, 0, 1)
    score = 35 * folds + 20 * held + 15 * ticks + 15 * oos + 15 * sharpe - 10 * dd
    return round(max(0.0, score), 2)


def rank(candidates: List[Dict], settings: Settings) -> Dict:
    """Best rule per symbol, ordered grade-first; promote what clears min_grade."""
    for c in candidates:
        c["score"] = 0.0 if c.get("error") else robustness_score(c)
        c["gradeRank"] = GRADES.index(c["verdict"])

    best: Dict[str, Dict] = {}
    for c in candidates:
        cur = best.get(c["symbol"])
        if cur is None or (c["gradeRank"], c["score"]) > (cur["gradeRank"], cur["score"]):
            best[c["symbol"]] = c
    ranked = sorted(best.values(), key=lambda c: (-c["gradeRank"], -c["score"]))

    floor = GRADES.index(settings.min_grade)
    eligible = [c for c in ranked if c["gradeRank"] >= floor]
    promoted = eligible[: settings.max_positions]
    for i, c in enumerate(ranked, 1):
        c["rank"] = i
        c["promoted"] = c in promoted

    return {
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "minGrade": settings.min_grade,
        "exploratory": floor < GRADES.index("MODERATE"),
        "ranked": ranked,
        "promoted": [{k: c[k] for k in ("symbol", "timeframe", "strategy", "strategy_parameters",
                                         "spec", "verdict", "score")} for c in promoted],
        "gradeTally": {g: sum(1 for c in candidates if c["verdict"] == g) for g in GRADES},
    }
