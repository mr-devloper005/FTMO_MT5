"""Grading the evidence, not just printing the metric.

A number on its own says nothing about whether it will survive contact with
next year. This module runs the tests that separate the two, and grades the
result against a fixed rubric so that the same evidence always earns the same
word — Robust, Moderate, Fragile or Failed.

The rubric is calibrated against three candidates graded by hand in the
research session it replaces. An earlier draft left out walk-forward and
top-N removal, and every candidate that session rejected passed it. Those two
tests are therefore load-bearing, not decoration.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Dict, List, Optional

from ftmo_mt5.lab import metrics

# A benchmark this close to zero is not an opponent. Beating it says the
# market went nowhere, not that the rule works.
FLAT_BENCHMARK = 1_000.0

# A margin this thin is not a win either. The research session it is calibrated
# against rejected a candidate whose whole edge over buy & hold was $1,526 on a
# $212,405 benchmark — seven tenths of one percent, which is inside the noise of
# any single assumption in the run. A margin has to clear both a floor in
# dollars and a fraction of what it is being compared against.
MIN_MARGIN_PCT = 1.0

COST_LADDER = (1.0, 2.0, 4.0, 8.0)
FOLDS = 5
SPLIT_FRACTION = 0.70


def _spec_with(spec: Dict, **overrides) -> Dict:
    out = deepcopy(spec)
    out.update(overrides)
    return out


def _param_spec(spec: Dict, key: str, value) -> Dict:
    out = deepcopy(spec)
    params = dict(out.get("strategy_parameters") or {})
    params[key] = value
    out["strategy_parameters"] = params
    return out


def variations_for(default, count: int = 6) -> List:
    """Neighbouring values around a default, for the sensitivity test.

    A plateau — most neighbours still work — is evidence. An island, where
    only the tested value works, is the shape overfitting makes.
    """
    if not isinstance(default, (int, float)) or isinstance(default, bool) or not default:
        return []
    steps = (0.6, 0.8, 0.9, 1.1, 1.25, 1.5)[:count]
    out = []
    for s in steps:
        v = int(round(default * s)) if isinstance(default, int) else round(default * s, 4)
        if v and v != default and v not in out:
            out.append(v)
    return out


def validate(symbol: str, timeframe: str, spec: Dict, candles: List,
             param_key: Optional[str] = None) -> Dict:
    """Every robustness test, then the grade they add up to."""
    full = metrics.measure(symbol, timeframe, spec, candles, detail=True)

    # --- out of sample -------------------------------------------------
    # Cut once, in time order, never shuffled. Buy & hold is recomputed
    # inside each half, so the comparison never spans two windows.
    cut = int(len(candles) * SPLIT_FRACTION)
    dev = metrics.measure(symbol, timeframe, spec, candles[:cut])
    oos = metrics.measure(symbol, timeframe, spec, candles[cut:])

    # --- cost ladder ---------------------------------------------------
    ladder = []
    for ticks in COST_LADDER:
        r = metrics.measure(symbol, timeframe,
                            _spec_with(spec, slippage_ticks=ticks), candles)
        ladder.append({"ticks": ticks, "net": r["net"], "bh": r["bh"],
                       "vsBH": r["vsBH"], "beats": r["beatsBH"]})
    survives_ticks = max((c["ticks"] for c in ladder if c["beats"]), default=0.0)

    # --- concentration -------------------------------------------------
    removal = []
    for n in (1, 3, 5):
        net = metrics.net_without_top(full, n)
        removal.append({"removed": n, "net": net, "bh": full["bh"],
                        "beats": net is not None and net > full["bh"]})

    # --- walk forward --------------------------------------------------
    # Five contiguous folds, each judged against its own buy & hold. A rule
    # that only works in one era shows up here and nowhere else.
    size = len(candles) // FOLDS
    folds = []
    for i in range(FOLDS):
        chunk = candles[i * size: (i + 1) * size if i < FOLDS - 1 else len(candles)]
        if len(chunk) < 30:
            continue
        r = metrics.measure(symbol, timeframe, spec, chunk)
        folds.append({"fold": i + 1, "bars": r["bars"], "trades": r["trades"],
                      "net": r["net"], "bh": r["bh"], "beats": r["beatsBH"]})
    folds_won = sum(1 for f in folds if f["beats"])

    # --- parameter sensitivity -----------------------------------------
    params = spec.get("strategy_parameters") or {}
    if param_key is None:
        numeric = [k for k, v in params.items()
                   if isinstance(v, (int, float)) and not isinstance(v, bool) and v]
        param_key = numeric[0] if numeric else None
    variations = []
    if param_key:
        for value in variations_for(params.get(param_key)):
            r = metrics.measure(symbol, timeframe,
                                _param_spec(spec, param_key, value), candles)
            variations.append({"value": value, "trades": r["trades"],
                               "net": r["net"], "vsBH": r["vsBH"],
                               "beats": r["beatsBH"]})
    held = (sum(1 for v in variations if v["beats"]) / len(variations) * 100
            if variations else None)

    grade = _grade(full, oos, survives_ticks, removal, folds, folds_won, held)
    return {
        "full": full, "dev": dev, "oos": oos,
        "costLadder": ladder, "survivesTicks": survives_ticks,
        "topRemoval": removal,
        "folds": folds, "foldsWon": folds_won, "foldsRun": len(folds),
        "paramKey": param_key, "variations": variations,
        "variationsHeldPct": None if held is None else round(held, 1),
        **grade,
    }


def _removed(removal: List[Dict], n: int) -> bool:
    return any(r["removed"] == n and r["beats"] for r in removal)


def _grade(full: Dict, oos: Dict, ticks: float, removal: List[Dict],
           folds: List[Dict], folds_won: int, held: Optional[float]) -> Dict:
    """The seven gates, and the reasons behind whichever grade came out."""
    flat = abs(full["bh"]) < FLAT_BENCHMARK
    floor = max(FLAT_BENCHMARK, abs(full["bh"]) * MIN_MARGIN_PCT / 100.0)
    thin = full["beatsBH"] and full["vsBH"] < floor
    gates = {
        "beatsInSample": bool(full["beatsBH"]),
        "benchmarkWorthBeating": not flat,
        "marginWorthKeeping": not thin,
        "marginFloor": round(floor, 2),
        "beatsOutOfSample": bool(oos["beatsBH"]),
        "trades": full["trades"],
        "variationsHeldPct": None if held is None else round(held, 1),
        "survivesTicks": ticks,
        "survivesTop3Removal": _removed(removal, 3),
        "survivesTop5Removal": _removed(removal, 5),
        "foldsWon": folds_won,
        "foldsRun": len(folds),
    }
    why: List[str] = []

    # Failed comes first: these are disqualifications, not weak passes.
    if flat:
        why.append(f"Buy & hold made only ${full['bh']:,.0f} over this window — "
                   "beating it is not evidence of anything.")
    if not full["beatsBH"]:
        why.append("Does not beat buy & hold in-sample.")
    if thin:
        why.append(
            f"Beats buy & hold by ${full['vsBH']:,.0f} on a ${full['bh']:,.0f} "
            f"benchmark — {full['vsBH'] / abs(full['bh']) * 100:.1f}% of it. That "
            "margin is inside the noise of any single assumption in the run.")
    if full["trades"] < 10:
        why.append(f"{full['trades']} trades is too few to distinguish a rule "
                   "from a coincidence.")
    if why:
        return {"verdict": "FAILED", "gates": gates, "why": why}

    robust = (gates["beatsOutOfSample"] and full["trades"] >= 100
              and (held is not None and held >= 60) and ticks >= 4
              and gates["survivesTop5Removal"] and folds_won >= 3)
    if robust:
        return {"verdict": "ROBUST", "gates": gates, "why": [
            "Beats in-sample and out-of-sample, on 100+ trades.",
            f"{held:.0f}% of parameter variations still beat — a plateau, not an island.",
            f"Still beats at {ticks:.0f} ticks of slippage.",
            "Still beats with the five largest winners deleted.",
            f"Beats in {folds_won} of {len(folds)} walk-forward folds.",
        ]}

    moderate = (gates["beatsOutOfSample"] and full["trades"] >= 30
                and (held is not None and held >= 50) and ticks >= 2
                and gates["survivesTop3Removal"])
    if moderate:
        short = []
        if full["trades"] < 100:
            short.append(f"only {full['trades']} trades")
        if held is not None and held < 60:
            short.append(f"{held:.0f}% of variations hold")
        if ticks < 4:
            short.append(f"fails above {ticks:.0f} ticks of slippage")
        if not gates["survivesTop5Removal"]:
            short.append("does not survive removing the top 5 winners")
        if folds_won < 3:
            short.append(f"wins only {folds_won} of {len(folds)} folds")
        return {"verdict": "MODERATE", "gates": gates, "why": [
            "Beats in-sample and out-of-sample.",
            "Short of robust because " + ", ".join(short) + "." if short
            else "Short of robust on the stricter thresholds.",
        ]}

    reasons = []
    if not gates["beatsOutOfSample"]:
        reasons.append("Beats in-sample but fails out-of-sample — the usual shape "
                       "of a rule fitted to its own history.")
    if not gates["survivesTop3Removal"]:
        reasons.append(f"{full['topTradeShare']:.0f}% of gross profit came from one "
                       "trade, and the edge disappears without the top 3.")
    if held is not None and held < 50:
        reasons.append(f"Only {held:.0f}% of parameter variations still beat — "
                       "an island, not a plateau.")
    if ticks < 2:
        reasons.append("Does not survive 2 ticks of slippage.")
    if folds_won < 2:
        reasons.append(f"Wins only {folds_won} of {len(folds)} walk-forward folds.")
    return {"verdict": "FRAGILE", "gates": gates,
            "why": reasons or ["Beats in-sample, but not by enough on the "
                               "robustness tests to call it more than fragile."]}
