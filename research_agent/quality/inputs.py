"""FMP data -> the input shapes Rahul's lenses expect (replaces his FactSet / file inputs).

Each function takes raw FMP responses (stable-API field names) and builds
exactly the dicts `lenses.py` reads. Definitions follow his pipeline
(add_ticker.py @ 3b81a2a) so a score means the same thing it meant there:

    fundamentals  latest QUARTER: yoy revenue vs the same quarter a year earlier,
                  net and operating margin, free cash flow ($B), long-term
                  debt / equity; TTM revenue = sum of the last four quarters
    valuation     forward P/E = price / analyst EPS for the nearest fiscal year
                  not yet reported (FactSet "FY1"); peer median of the same;
                  FCF yield = 1 / (price / FCF TTM); dividend yield TTM
    technicals    FMP quote fields (price, 50/200-day averages, 52-week high),
                  FMP 3-month / 1-year change, FMP RSI(14)

Where FMP's own number differs slightly from Rahul's formula it is listed in
TECHNICAL_NOTES, so nobody mistakes a data-source difference for a model change.
"""
from __future__ import annotations

import statistics
from datetime import date
from typing import Any, Dict, List, Optional

TECHNICAL_NOTES = [
    "52-week high: FMP yearHigh uses intraday highs (Rahul: highest close).",
    "90-day momentum: FMP 3-month change (~63 sessions; Rahul: 60 sessions).",
    "RSI(14): FMP's indicator (Rahul: simple-average RSI on closes).",
    "50/200-day averages: FMP priceAvg50 / priceAvg200.",
]


def _num(v: Any) -> Optional[float]:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def fy1_eps(estimates: List[Dict[str, Any]], today: Optional[date] = None) -> Optional[Dict[str, Any]]:
    """Analyst EPS for the nearest fiscal year whose end date is today or later."""
    today = today or date.today()
    future = [e for e in estimates or [] if e.get("date") and date.fromisoformat(e["date"][:10]) >= today]
    if not future:
        return None
    nearest = min(future, key=lambda e: e["date"])
    return {"fiscal_year_end": nearest["date"][:10], "eps": _num(nearest.get("epsAvg"))}


def forward_pe(price: Optional[float], eps: Optional[float]) -> Optional[float]:
    """Price / forward EPS; None when EPS is missing or not positive (a loss has no P/E)."""
    if not price or eps is None or eps <= 0:
        return None
    return round(price / eps, 2)


def company(raw: Dict[str, Any], sector: str) -> Dict[str, Any]:
    """Income / cash-flow / balance-sheet quarters -> Rahul's `co` dict."""
    inc = sorted(raw.get("income_q") or [], key=lambda r: r["date"])
    summary: Dict[str, Any] = {}
    quarter: Dict[str, Any] = {}
    if inc:
        latest = inc[-1]
        rev = _num(latest.get("revenue"))
        summary["latest_quarter"] = f"FY{latest.get('fiscalYear')} {latest.get('period')} ({latest['date']})"
        if rev:
            summary["latest_revenue_b"] = round(rev / 1e9, 2)
            ni, oi = _num(latest.get("netIncome")), _num(latest.get("operatingIncome"))
            if ni is not None:
                summary["latest_net_margin_pct"] = round(ni / rev * 100, 2)
            if oi is not None:
                summary["latest_operating_margin_pct"] = round(oi / rev * 100, 2)
        if len(inc) >= 5 and _num(inc[-5].get("revenue")):
            summary["yoy_revenue_growth_pct"] = round((rev / _num(inc[-5]["revenue"]) - 1) * 100, 1)
        if len(inc) >= 4:
            summary["ttm_revenue_b"] = round(sum(_num(q.get("revenue")) or 0 for q in inc[-4:]) / 1e9, 2)
    cf = (raw.get("cashflow_q") or [{}])[0]
    if _num(cf.get("freeCashFlow")) is not None:
        summary["latest_fcf_b"] = round(_num(cf["freeCashFlow"]) / 1e9, 2)
    bs = (raw.get("balance_q") or [{}])[0]
    ltd, eq = _num(bs.get("longTermDebt")), _num(bs.get("totalStockholdersEquity"))
    if ltd is not None and eq and eq > 0:
        quarter["long_debt_to_equity"] = round(ltd / eq, 2)
    return {"sector": sector, "fundamentals": {"summary": summary, "quarters": [quarter] if quarter else []}}


def peer_comp(raw: Dict[str, Any], peers: Dict[str, Dict[str, Any]], today: Optional[date] = None) -> Dict[str, Any]:
    """Forward P/E, FCF and dividend yield, and the peer median -> Rahul's `fs` dict.

    `peers` maps symbol -> {"price": float, "estimates": [...]}. Peers with no
    positive forward EPS are left out of the median (as a P/E of a loss is undefined).
    """
    price = _num((raw.get("quote") or {}).get("price"))
    fy1 = fy1_eps(raw.get("estimates"), today) or {}
    ratios = raw.get("ratios_ttm") or {}
    pfcf = _num(ratios.get("priceToFreeCashFlowRatioTTM"))
    div = _num(ratios.get("dividendYieldTTM"))
    target = {
        "price": price, "EPS_FY1": fy1.get("eps"), "FY1_end": fy1.get("fiscal_year_end"),
        "PE_FY1": forward_pe(price, fy1.get("eps")),
        "FCF_Yield_pct": round(100 / pfcf, 2) if pfcf else None,       # negative when FCF is negative
        "Div_Yield_pct": round(div * 100, 2) if div is not None else None,
    }
    rows = []
    for sym, p in peers.items():
        e = fy1_eps(p.get("estimates"), today) or {}
        rows.append({"ticker": sym, "price": _num(p.get("price")), "EPS_FY1": e.get("eps"),
                     "PE_FY1": forward_pe(_num(p.get("price")), e.get("eps"))})
    pes = [r["PE_FY1"] for r in rows if r["PE_FY1"] is not None]
    median = {"PE_FY1": round(statistics.median(pes), 2)} if pes else {}
    return {"target": target, "peers": rows, "peer_aggregates": {"median": median}, "_source": "FMP"}


def technicals(raw: Dict[str, Any]) -> Dict[str, Any]:
    """FMP quote + price change + RSI -> Rahul's `ta` dict."""
    q = raw.get("quote") or {}
    ch = raw.get("quote_change") or {}
    spot, sma50, sma200, high = (_num(q.get(k)) for k in ("price", "priceAvg50", "priceAvg200", "yearHigh"))
    pct = lambda a, b: round((a / b - 1) * 100, 2) if a and b else None   # noqa: E731
    return {
        "spot": spot, "sma50": sma50, "sma200": sma200, "rsi14": _num(raw.get("rsi14")),
        "high_52w": high, "drawdown_from_52w_high_pct": pct(spot, high),
        "pct_above_sma50_pct": pct(spot, sma50), "pct_above_sma200_pct": pct(spot, sma200),
        "price_90d_change_pct": _num(ch.get("3M")), "price_1y_change_pct": _num(ch.get("1Y")),
        "_source": "FMP",
    }
