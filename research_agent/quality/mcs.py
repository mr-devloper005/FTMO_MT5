"""Management Credibility Score (MCS v2): did management deliver what it guided, and could you plan on it?

Replaces the dashboard's "Beat = 1, Hit = 0.5, Miss = 0" score, which ranks a sandbagged guide above an
accurate one and, for 88 of its 93 stocks, graded synthetic "consensus-implied" guidance instead of
anything management said.

    ledger (JSON, one per company, every number sourced)  ->  score_period()  ->  score_ledger()

A ledger holds, per graded quarter, the guidance items given on call N and the actuals reported on call
N+1, plus forward commitments logged when they were made (a quote, a measurable target, a due date) and
graded only when due. Two scores per quarter, combined:

    Delivery  (default 60%)  did they do at least what they said?  1 kept / 0.5 partly / 0 not kept
    Accuracy  (default 40%)  how close was the guide?  100 inside the range, minus a penalty per % outside

Changes from v1 (the hand calculation of 2026-10-11):
  1. Market-adjusted accuracy. When management attributes a surprise to price (and says so on the call),
     only the part beyond VOLUME_TOLERANCE_PCT is treated as market-driven; at most MAX_MARKET_CREDIT of
     the penalty is forgiven. Management is still expected to read its own market.
  2. EPS is shown but not scored: it is revenue x margin - opex - tax, so scoring it counts one surprise
     three times. Scored items: revenue, gross margin, opex, tax rate, capex.
  3. Asymmetric: a shortfall costs DOWNSIDE_MULTIPLIER times the penalty of an equal overshoot.
  4. Discretionary spending decided after the guide (one-off bonuses, donations) is taken out of opex
     before grading and reported separately, so it is visible but not mistaken for forecasting error.
  5. Commitments come from a log written at call time; pending ones are never scored.

Pure functions, no I/O except load_ledger(). Research only: nothing here touches a broker.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

LEDGER_DIR = Path(__file__).resolve().parent / "ledgers"

WEIGHTS = {"delivery": 0.6, "accuracy": 0.4}
ITEM_WEIGHT = {"revenue": 1.0, "gross_margin": 1.0, "opex": 1.0, "tax_rate": 0.5, "capex": 0.5, "eps": 0.0}
HIGHER_IS_BETTER = {"revenue", "gross_margin", "eps"}
LOWER_IS_BETTER = {"opex", "tax_rate"}
NEUTRAL = {"capex"}                              # a plan: overspend and underspend both count as off-plan
PCT_UNITS = {"pct"}

APPROX_BAND_PCT = 2.5                            # "approximately $X" -> X +/- 2.5%
APPROX_BAND_PCT_CAPEX = 5.0                      # capex is lumpier
APPROX_BAND_PTS = 0.5                            # "approximately 81%" -> +/- 0.5 points
PENALTY_PER_PCT = 4.0                            # accuracy points lost per 1% beyond the band edge
PENALTY_PER_PT = 10.0                            # ... per 1 percentage point, for margins and tax rates
DOWNSIDE_MULTIPLIER = 2.0
OVERSPEND_PARTIAL_PCT = 5.0                      # opex up to 5% over the top of the range = partly kept
VOLUME_TOLERANCE_PCT = 3.0                       # volume error management is assumed able to forecast
MAX_MARKET_CREDIT = 0.9
STATUS_POINTS = {"kept": 1.0, "partial": 0.5, "missed": 0.0}
RATINGS = [(85.0, "High"), (70.0, "Good"), (55.0, "Mixed"), (0.0, "Low")]


def load_ledger(symbol: str, directory: Optional[Path] = None) -> Dict[str, Any]:
    path = Path(directory or LEDGER_DIR) / f"{symbol.upper()}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def available(directory: Optional[Path] = None) -> List[str]:
    d = Path(directory or LEDGER_DIR)
    return sorted(p.stem for p in d.glob("*.json")) if d.exists() else []


def rating(score: Optional[float]) -> Optional[str]:
    if score is None:
        return None
    return next(name for floor, name in RATINGS if score >= floor)


def band(item: Dict[str, Any]) -> tuple[float, float]:
    """The range a result has to land in to count as accurate."""
    kind, mid = item.get("kind", "range"), item.get("mid")
    low, high = item.get("low"), item.get("high")
    if kind == "floor":
        return float(low), math.inf
    if kind == "ceiling":
        return -math.inf, float(high)
    if kind == "approx":
        if item.get("unit") in PCT_UNITS:
            return mid - APPROX_BAND_PTS, mid + APPROX_BAND_PTS
        pct = APPROX_BAND_PCT_CAPEX if item["metric"] == "capex" else APPROX_BAND_PCT
        return mid * (1 - pct / 100), mid * (1 + pct / 100)
    return float(low), float(high)


def _reference(item: Dict[str, Any], lo: float, hi: float) -> float:
    mid = item.get("mid")
    if mid is not None:
        return float(mid)
    return lo if math.isfinite(lo) else hi


def graded_actual(item: Dict[str, Any]) -> Optional[float]:
    """The actual used for grading: opex excludes discretionary spending decided after the guide."""
    a = item.get("actual")
    if a is None:
        return None
    if item["metric"] == "opex":
        return float(a) - float(item.get("discretionary") or 0.0)
    return float(a)


def outcome(item: Dict[str, Any]) -> Optional[str]:
    """beat / hit / miss for the result-type items; under / hit / over for spend; on-plan / off-plan for capex."""
    a = graded_actual(item)
    if a is None:
        return None
    lo, hi = band(item)
    if lo <= a <= hi:
        return "on-plan" if item["metric"] in NEUTRAL else "hit"
    if item["metric"] in NEUTRAL:
        return "off-plan"
    if item["metric"] in LOWER_IS_BETTER:
        return "under" if a < lo else "over"
    return "beat" if a > hi else "miss"


def beyond_edge(item: Dict[str, Any]) -> float:
    """Distance outside the band: % of the guide for money, percentage points for margins and tax."""
    a = graded_actual(item)
    lo, hi = band(item)
    if a is None or lo <= a <= hi:
        return 0.0
    edge = hi if a > hi else lo
    if item.get("unit") in PCT_UNITS:
        return abs(a - edge)
    return abs(a - edge) / abs(_reference(item, lo, hi)) * 100


def favourable(item: Dict[str, Any]) -> Optional[bool]:
    o = outcome(item)
    if o in ("beat", "under"):
        return True
    if o in ("miss", "over"):
        return False
    return None


def surprise_pct(item: Dict[str, Any]) -> Optional[float]:
    """Actual vs. the middle of the guide, in % (used for the market-credit calculation)."""
    a = graded_actual(item)
    if a is None:
        return None
    lo, hi = band(item)
    ref = _reference(item, lo, hi)
    return (a - ref) / abs(ref) * 100 if ref else None


def market_credit(period: Dict[str, Any], item: Dict[str, Any]) -> float:
    """Share of the accuracy penalty forgiven because the surprise came from market prices.

    Only for revenue and gross margin, only when management attributed the surprise to price on the record,
    and only for the part of the revenue surprise beyond what volume error could explain.
    """
    pv = period.get("price_volume") or {}
    if item["metric"] not in ("revenue", "gross_margin") or not pv.get("price_attributed"):
        return 0.0
    rev = next((i for i in period.get("items", []) if i["metric"] == "revenue"), None)
    s = surprise_pct(rev) if rev else None
    if not s:
        return 0.0
    tol = float(pv.get("volume_tolerance_pct", VOLUME_TOLERANCE_PCT))
    return max(0.0, min(MAX_MARKET_CREDIT, 1 - tol / abs(s)))


def accuracy(period: Dict[str, Any], item: Dict[str, Any]) -> Optional[float]:
    if graded_actual(item) is None:
        return None
    d = beyond_edge(item)
    if d == 0:
        return 100.0
    slope = PENALTY_PER_PT if item.get("unit") in PCT_UNITS else PENALTY_PER_PCT
    if favourable(item) is False:
        slope *= DOWNSIDE_MULTIPLIER
    penalty = d * slope * (1 - market_credit(period, item))
    return round(max(0.0, 100.0 - penalty), 1)


def delivery(item: Dict[str, Any]) -> Optional[float]:
    a = graded_actual(item)
    if a is None:
        return None
    lo, hi = band(item)
    m = item["metric"]
    if m in HIGHER_IS_BETTER:
        return 1.0 if a >= lo else 0.0
    if m in NEUTRAL:
        return 1.0 if lo <= a <= hi else 0.5
    if a <= hi:
        return 1.0
    over = (a - hi) / abs(_reference(item, lo, hi)) * 100 if item.get("unit") not in PCT_UNITS else a - hi
    return 0.5 if over <= OVERSPEND_PARTIAL_PCT else 0.0


def _commitments_for(ledger: Dict[str, Any], period: Dict[str, Any], prev_reported: Optional[str]) -> List[Dict]:
    """Commitments graded in this period: graded_on in (previous report date, this report date]."""
    out = []
    for c in ledger.get("commitments", []):
        g = c.get("graded_on")
        if not g or c.get("status") not in STATUS_POINTS:
            continue
        if g <= period["reported_on"] and (prev_reported is None or g > prev_reported):
            out.append(c)
    return out


def score_period(period: Dict[str, Any], commitments: Iterable[Dict[str, Any]] = (),
                 weights: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
    w = weights or WEIGHTS
    rows, d_pts, a_num, a_den = [], [], 0.0, 0.0
    for item in period.get("items", []):
        if item.get("comparable") is False or item.get("actual") is None:
            continue
        acc, dlv, iw = accuracy(period, item), delivery(item), ITEM_WEIGHT.get(item["metric"], 0.0)
        rows.append({"metric": item["metric"], "outcome": outcome(item), "guide": _fmt_guide(item),
                     "actual": item["actual"], "discretionary": item.get("discretionary") or 0,
                     "surprise_pct": None if surprise_pct(item) is None else round(surprise_pct(item), 1),
                     "market_credit": round(market_credit(period, item), 2), "accuracy": acc,
                     "delivery": dlv, "scored": iw > 0})
        if iw > 0:
            d_pts.append(dlv)
            a_num += acc * iw
            a_den += iw
    kept = list(commitments)
    d_pts += [STATUS_POINTS[c["status"]] for c in kept]
    if not d_pts or not a_den:
        return {"period": period["period"], "mcs": None, "items": rows, "commitments": kept}
    dsc = 100 * sum(d_pts) / len(d_pts)
    asc = a_num / a_den
    mcs = w["delivery"] * dsc + w["accuracy"] * asc
    flags = [f"{r['metric']}: ${r['discretionary']:,.0f}M discretionary spend decided after the guide"
             for r in rows if r["discretionary"]]
    n_fin = sum(1 for r in rows if r["scored"])
    if n_fin < 3:
        flags.append(f"thin evidence: only {n_fin} comparable financial item(s) this quarter")
    return {"period": period["period"], "guided_on": period.get("guided_on"),
            "reported_on": period.get("reported_on"), "delivery": round(dsc, 1), "accuracy": round(asc, 1),
            "mcs": round(mcs, 1), "rating": rating(mcs), "n_items": len(d_pts), "items": rows,
            "commitments": [{k: c.get(k) for k in ("target", "status", "made_on", "graded_on")} for c in kept],
            "flags": flags}


def score_ledger(ledger: Dict[str, Any], weights: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
    graded = sorted((p for p in ledger["periods"] if p.get("reported_on")), key=lambda p: p["reported_on"])
    out, prev = [], None
    for p in graded:
        out.append(score_period(p, _commitments_for(ledger, p, prev), weights))
        prev = p["reported_on"]
    scored = [r for r in out if r["mcs"] is not None]
    rolling = [round(sum(r["mcs"] for r in scored[max(0, i - 3):i + 1]) / len(scored[max(0, i - 3):i + 1]), 1)
               for i in range(len(scored))]
    for r, roll in zip(scored, rolling):
        r["rolling_4q"] = roll
    avg = round(sum(r["mcs"] for r in scored) / len(scored), 1) if scored else None
    spread = _interval(scored)
    pending = [c for c in ledger.get("commitments", []) if c.get("status") == "pending"]
    return {"symbol": ledger["symbol"], "periods": out, "average": avg, "rating": rating(avg),
            "interval_95": spread, "latest_4q": rolling[-1] if rolling else None,
            "pending_commitments": [{k: c.get(k) for k in ("target", "due", "made_on")} for c in pending],
            "weights": weights or WEIGHTS}


def management_lens(symbol: str, directory: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """The quality engine's Management lens: latest 4-quarter MCS, or None when no ledger exists."""
    try:
        res = score_ledger(load_ledger(symbol, directory))
    except FileNotFoundError:
        return None
    if res["latest_4q"] is None:
        return None
    last = [p for p in res["periods"] if p["mcs"] is not None][-4:]
    quarters = ", ".join("%s %s" % (p["period"], p["mcs"]) for p in last)
    why = [f"MCS v2 {res['latest_4q']} over the last {len(last)} graded quarters ({quarters})",
           f"Delivery {sum(p['delivery'] for p in last) / len(last):.0f} · "
           f"accuracy {sum(p['accuracy'] for p in last) / len(last):.0f} (market-adjusted)",
           f"→ M = {res['latest_4q']}"]
    return {"score": res["latest_4q"], "why": why}


def _interval(scored: List[Dict[str, Any]]) -> Optional[List[float]]:
    """Rough 95% interval for the average: +/- 1.96 standard errors of the quarterly scores."""
    if len(scored) < 2:
        return None
    xs = [r["mcs"] for r in scored]
    m = sum(xs) / len(xs)
    se = (sum((x - m) ** 2 for x in xs) / (len(xs) - 1)) ** 0.5 / len(xs) ** 0.5
    return [round(m - 1.96 * se, 1), round(m + 1.96 * se, 1)]


def _fmt_guide(item: Dict[str, Any]) -> str:
    unit, kind = item.get("unit"), item.get("kind", "range")
    f = (lambda v: f"{v:g}%") if unit in PCT_UNITS else (lambda v: f"${v:,.2f}") if unit == "usd" \
        else (lambda v: f"${v / 1000:,.2f}B")
    if kind == "approx":
        return f"~{f(item['mid'])}"
    if kind == "floor":
        return f">{f(item['low'])}"
    if kind == "ceiling":
        return f"<{f(item['high'])}"
    return f"{f(item['low'])}–{f(item['high'])}"
