"""Rahul's five quality lenses — ported VERBATIM from the Research & Ranking Dashboard.

Source: github.com/Rahulchili/Research-and-Ranking-Dashboard-V1
        investment-dashboard/refresh_all_lens_scores.py @ 3b81a2a (2026-05-25)
Author: Rahul Yalamanchili (with Sreeni). Lens owner: Rahul.

Every function below is copied unchanged (score + "why this score" rationale +
sector benchmarks), so the same inputs give exactly the same scores as his
dashboard — `tests/test_quality.py` proves it against his stored results.
Only the INPUTS change: FactSet exports are replaced by FMP (see inputs.py).
Do not edit the logic here; change it upstream with Rahul and re-port.

Input shapes (as in the original):
    co   company:  {"sector", "fundamentals": {"summary": {...}, "quarters": [...]}, "mcs_simple", "subscores"}
    fs   peer comp: {"target": {"PE_FY1", "FCF_Yield_pct", "Div_Yield_pct"}, "peer_aggregates": {"median": {"PE_FY1"}}}
    ta   technicals: {"spot", "sma50", "sma200", "rsi14", "drawdown_from_52w_high_pct", "price_90d_change_pct",
                      "pct_above_sma50_pct", "pct_above_sma200_pct", "price_1y_change_pct"}
    opt  options:   {"atm_iv_front_pct", "pcr_oi", "skew_atm_pct"}
"""
from __future__ import annotations

import json
import re
from pathlib import Path


def clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


SECTOR_NM_BENCHMARKS = {
    # (target_nm_for_strong, target_nm_for_acceptable) — sector-relative
    "Information Technology":    (18, 8),
    "Communication Services":    (15, 8),
    "Health Care":               (15, 8),
    "Industrials":               (10, 5),
    "Consumer Discretionary":    (8, 4),
    "Consumer Staples":          (4, 2),     # WMT 3.4% is normal here
    "Energy":                    (10, 5),
    "Financials":                (25, 15),   # banks have higher NM
}

SECTOR_REV_BENCHMARKS = {
    # (strong_yoy, acceptable_yoy)
    "Information Technology":    (15, 5),
    "Communication Services":    (10, 3),
    "Health Care":               (10, 3),
    "Industrials":               (8, 3),
    "Consumer Discretionary":    (8, 3),
    "Consumer Staples":          (5, 2),     # staples = slower growth norm
    "Energy":                    (5, -5),    # energy is cyclical; even flat = OK
    "Financials":                (6, 2),
}


def fundamentals_score(co: dict) -> float:
    """Sector-aware fundamentals score 0-100.

    Margin and revenue thresholds adjust to sector norms — Walmart's 3.4% net
    margin is best-in-class for staples; AT&T's 13% net margin is mediocre
    for telecom. Energy revenue decline is cyclical, not a structural break.
    """
    fund = co.get("fundamentals", {}) or {}
    summary = fund.get("summary", {}) or {}
    yoy = summary.get("yoy_revenue_growth_pct")
    nm  = summary.get("latest_net_margin_pct")
    om  = summary.get("latest_operating_margin_pct")
    fcf_b = summary.get("latest_fcf_b")
    ttm = summary.get("ttm_revenue_b") or 0
    sector = co.get("sector") or "Information Technology"
    nm_strong, nm_ok = SECTOR_NM_BENCHMARKS.get(sector, (15, 8))
    rev_strong, rev_ok = SECTOR_REV_BENCHMARKS.get(sector, (10, 3))

    parts: list[tuple[float, float]] = []  # (score, weight)

    if yoy is not None:
        if yoy >= rev_strong * 1.5:    s = 90
        elif yoy >= rev_strong:        s = 75
        elif yoy >= rev_ok:            s = 55 + (yoy - rev_ok) * 1.5
        elif yoy >= 0:                 s = 35 + yoy * 2
        else:                          s = max(15, 35 + yoy * 1.0)
        parts.append((clamp(s), 3))

    if nm is not None:
        if nm >= nm_strong * 1.5:      s = 95
        elif nm >= nm_strong:          s = 80
        elif nm >= nm_ok:              s = 60 + (nm - nm_ok) * 1.5
        elif nm >= 0:                  s = 35 + nm * 3
        else:                          s = max(10, 30 + nm)
        parts.append((clamp(s), 2.5))

    if om is not None:
        # Sector-aware operating margin (rough 2× net margin proxy)
        if om >= nm_strong * 2:        s = 90
        elif om >= nm_strong:          s = 70
        elif om >= nm_ok:              s = 55
        else:                          s = max(25, 30 + om * 2)
        parts.append((clamp(s), 1.5))

    if fcf_b is not None:
        # Reward positive FCF; for big-scale companies, bigger absolute FCF
        # gets the higher tier
        if fcf_b > 15:     s = 90
        elif fcf_b > 5:    s = 80
        elif fcf_b > 0:    s = 65
        elif fcf_b > -5:   s = 45
        else:              s = 25
        parts.append((clamp(s), 1.5))

    # Debt/leverage penalty — value-trap or balance-sheet stress
    qs = fund.get("quarters") or []
    if qs:
        latest = qs[-1]
        d2e = latest.get("long_debt_to_equity")
        if d2e is not None:
            # 0.5 = healthy, 1.0 = elevated, 2.0 = high
            if d2e >= 2.0:
                parts.append((25, 1.0))   # high leverage warning
            elif d2e >= 1.2:
                parts.append((45, 0.5))   # elevated

    if not parts:
        return 60.0

    total_w = sum(w for _, w in parts)
    base = sum(s * w for s, w in parts) / total_w
    # Scale bonus
    if ttm >= 200:
        base += 4
    elif ttm >= 100:
        base += 2
    return round(clamp(base), 1)


def management_score(co: dict) -> float:
    """MCS-based score. mcs_simple is already 0-1, scale to 0-100."""
    mcs = co.get("mcs_simple")
    if mcs is None:
        # Fall back to subscores blob
        sub = co.get("subscores", {})
        if isinstance(sub, dict):
            ratio = sub.get("strict_beat_rate_pct") or sub.get("guide_accuracy_pct")
            if ratio is not None:
                return round(clamp(float(ratio)), 1)
        return 50.0  # neutral when no claims tracked
    return round(clamp(mcs * 100), 1)


def valuation_score(co: dict, fs: dict, ta: dict | None = None) -> float:
    """Sector-relative valuation with value-trap detection.

    Discount to peer median = good — UNLESS combined with weak technicals
    AND weak fundamentals (the classic value-trap setup), in which case
    the discount is the market's verdict, not an opportunity.

    Defensive premium (cheap-looking via PE but strong technicals + positive
    growth) is treated as deserved (capped softer)."""
    t = fs.get("target", {}) or {}
    pe = t.get("PE_FY1")
    peer_agg = (fs.get("peer_aggregates") or {}).get("median") or {}
    peer_pe = peer_agg.get("PE_FY1")
    fund = co.get("fundamentals", {}) or {}
    summary = fund.get("summary", {}) or {}
    yoy = summary.get("yoy_revenue_growth_pct") or 0
    ta = ta or {}
    spot = ta.get("spot") or ta.get("close")
    sma200 = ta.get("sma200") or ta.get("sma_200")
    above_200 = (spot is not None and sma200 is not None and spot > sma200)
    p1y = ta.get("price_1y_change_pct") or 0

    # Diagnose value-trap candidate: cheap PE + weak technical trend + weak growth
    is_value_trap = False
    if pe is not None and peer_pe and pe < peer_pe * 0.9:
        if not above_200 and yoy < 3 and p1y < 0:
            is_value_trap = True

    # Diagnose deserved-premium candidate: high PE but strong trend + good growth
    is_quality_premium = False
    if pe is not None and peer_pe and pe > peer_pe * 1.3:
        if above_200 and yoy >= 4 and p1y > 10:
            is_quality_premium = True

    if pe is None or peer_pe is None or peer_pe <= 0:
        if pe is None:
            return 60.0
        if pe < 10:    base = 70   # cheap absolute — modest credit
        elif pe < 18:  base = 65
        elif pe < 25:  base = 55
        elif pe < 35:  base = 45
        else:          base = 35
    else:
        delta_pct = (pe / peer_pe - 1) * 100
        if delta_pct <= -25:   base = 78
        elif delta_pct <= -10: base = 70
        elif delta_pct <= 0:   base = 62
        elif delta_pct <= 15:  base = 50
        elif delta_pct <= 30:  base = 42
        else:                  base = 32

    # Apply value-trap penalty — knock down 25 points
    if is_value_trap:
        base = min(base, 45)

    # Apply quality-premium uplift — defensive cash flow deserves a premium
    if is_quality_premium:
        base = max(base, 55)

    # FCF yield bonus
    fcf_y = t.get("FCF_Yield_pct")
    if fcf_y is not None:
        if fcf_y >= 6:   base += 6
        elif fcf_y >= 3: base += 3

    # Dividend yield bonus (especially relevant for staples/energy)
    div_y = t.get("Div_Yield_pct")
    if div_y is not None:
        if div_y >= 3:   base += 3
        elif div_y >= 1.5: base += 1

    return round(clamp(base), 1)


def technicals_score(ta: dict) -> float:
    """Trend + momentum.

    Components:
      - Price vs SMA200 (above = uptrend, below = downtrend)
      - SMA50 vs SMA200 (golden cross / death cross)
      - RSI (30-70 neutral, extremes flagged)
      - Drawdown from 52w high (smaller = better)
      - 90d price change (positive = momentum)
    """
    spot   = ta.get("spot") or ta.get("close") or ta.get("spot_price")
    sma50  = ta.get("sma50") or ta.get("sma_50")
    sma200 = ta.get("sma200") or ta.get("sma_200")
    rsi    = ta.get("rsi14") or ta.get("rsi_14")
    dd_pct = ta.get("drawdown_from_52w_high_pct")
    p90    = ta.get("price_90d_change_pct")
    above_50_pct  = ta.get("pct_above_sma50_pct")
    above_200_pct = ta.get("pct_above_sma200_pct")

    parts: list[tuple[float, float]] = []

    # Trend filter: % above 200-DMA
    if above_200_pct is not None:
        if above_200_pct > 20:   s = 88
        elif above_200_pct > 5:  s = 75
        elif above_200_pct > -5: s = 55
        elif above_200_pct > -15: s = 40
        else:                    s = 25
        parts.append((s, 3))
    elif spot and sma200:
        diff = (spot / sma200 - 1) * 100
        s = 75 if diff > 5 else 55 if diff > -5 else 30
        parts.append((s, 3))

    # Above 50-DMA (near-term momentum)
    if above_50_pct is not None:
        if above_50_pct > 5:   s = 75
        elif above_50_pct > -5: s = 55
        else:                  s = 35
        parts.append((s, 1))

    # Golden cross
    if sma50 and sma200:
        s = 70 if sma50 > sma200 else 40
        parts.append((s, 1))

    # RSI: neutral zone preferred
    if rsi is not None:
        if 40 <= rsi <= 65:    s = 75
        elif 30 <= rsi <= 70:  s = 60
        elif rsi > 70:         s = 45  # overbought
        elif rsi < 30:         s = 50  # oversold (mean-reversion edge)
        else:                  s = 35
        parts.append((s, 1.5))

    # Drawdown from 52w high
    if dd_pct is not None:
        if dd_pct >= -5:    s = 80
        elif dd_pct >= -15: s = 65
        elif dd_pct >= -30: s = 45
        else:               s = 25
        parts.append((s, 1.5))

    # 90d momentum
    if p90 is not None:
        if p90 > 15:    s = 85
        elif p90 > 5:   s = 70
        elif p90 > -5:  s = 55
        elif p90 > -15: s = 40
        else:           s = 25
        parts.append((s, 2))

    if not parts:
        return 60.0

    total_w = sum(w for _, w in parts)
    return round(clamp(sum(s * w for s, w in parts) / total_w), 1)


def options_score(opt: dict, ta: dict | None = None) -> float:
    """Options sentiment aligned with technicals.

    Key insight: a low P/C ratio with a stock in a downtrend isn't bullish —
    it's apathy (nobody bothering to hedge a falling name). Same low P/C
    with a stock in an uptrend is genuine bullish positioning. We require
    technical confirmation before scoring options sentiment as positive.
    """
    iv_front = opt.get("atm_iv_front_pct")
    pcr_oi   = opt.get("pcr_oi") or opt.get("put_call_ratio_oi")
    skew     = opt.get("skew_atm_pct") or opt.get("skew_25d_pp")
    ta = ta or {}
    spot = ta.get("spot") or ta.get("close")
    sma200 = ta.get("sma200") or ta.get("sma_200")
    p90 = ta.get("price_90d_change_pct") or 0
    in_uptrend = (spot is not None and sma200 is not None and spot > sma200) and p90 > -5

    parts: list[tuple[float, float]] = []

    if iv_front is not None:
        v = iv_front
        if v < 25:      s = 70
        elif v < 40:    s = 72
        elif v < 60:    s = 62
        elif v < 100:   s = 50
        else:           s = 35
        parts.append((s, 1))

    if pcr_oi is not None:
        if pcr_oi < 0.7:
            # Call-heavy: bullish IF trend confirms; apathetic otherwise
            s = 75 if in_uptrend else 50
        elif pcr_oi < 1.0:
            s = 65 if in_uptrend else 50
        elif pcr_oi < 1.3:
            # Balanced — institutions engaged on both sides; modestly positive
            s = 55
        elif pcr_oi < 1.8:
            # Put-heavy: active hedging is often a bullish contrarian signal
            # when trend is strong (smart money hedging gains), bearish when trend is weak
            s = 60 if in_uptrend else 40
        else:
            s = 50 if in_uptrend else 32
        parts.append((s, 1.5))

    if skew is not None:
        if skew < -3:    s = 70
        elif skew < 0:   s = 62
        elif skew < 3:   s = 52
        else:            s = 42
        parts.append((s, 1))

    if not parts:
        return 55.0

    total_w = sum(w for _, w in parts)
    return round(clamp(sum(s * w for s, w in parts) / total_w), 1)


def fundamentals_rationale(co: dict, score: float) -> list[str]:
    fund = co.get("fundamentals", {}) or {}
    s = fund.get("summary", {}) or {}
    sector = co.get("sector") or "Information Technology"
    nm_strong, nm_ok = SECTOR_NM_BENCHMARKS.get(sector, (15, 8))
    rev_strong, rev_ok = SECTOR_REV_BENCHMARKS.get(sector, (10, 3))
    bullets = []
    yoy, nm, om, fcf_b, ttm = (s.get("yoy_revenue_growth_pct"), s.get("latest_net_margin_pct"),
                               s.get("latest_operating_margin_pct"), s.get("latest_fcf_b"),
                               s.get("ttm_revenue_b"))
    if yoy is not None:
        tag = "strong" if yoy >= rev_strong else "acceptable" if yoy >= rev_ok else "weak" if yoy >= 0 else "contracting"
        bullets.append(f"Revenue {yoy:+.1f}% YoY ({tag} for {sector}; sector strong-bar {rev_strong}%)")
    if nm is not None:
        tag = "best-in-class" if nm >= nm_strong * 1.5 else "strong" if nm >= nm_strong else "acceptable" if nm >= nm_ok else "thin"
        bullets.append(f"Net margin {nm:.1f}% ({tag} for {sector}; sector strong-bar {nm_strong}%)")
    if om is not None:
        bullets.append(f"Operating margin {om:.1f}%")
    if fcf_b is not None:
        tier = "top-tier" if fcf_b > 15 else "healthy" if fcf_b > 5 else "positive" if fcf_b > 0 else "negative"
        bullets.append(f"Latest-quarter FCF ${fcf_b:.1f}B ({tier})")
    if ttm:
        bullets.append(f"TTM revenue ${ttm:.0f}B")
    qs = fund.get("quarters") or []
    if qs:
        d2e = qs[-1].get("long_debt_to_equity")
        if d2e is not None and d2e >= 1.2:
            bullets.append(f"⚠ Leverage: long-debt/equity {d2e:.2f}× (elevated; penalized)")
    bullets.append(f"→ F = {score} (sector-relative)")
    return bullets


def management_rationale(co: dict, score: float) -> list[str]:
    mcs = co.get("mcs_simple")
    beats, hits, misses, n = co.get("beats", 0), co.get("hits", 0), co.get("misses", 0), co.get("n_claims", 0)
    bullets = []
    if mcs is not None:
        bullets.append(f"MCS {mcs:.2f} ({beats}B / {hits}H / {misses}M on {n} tracked forward claims)")
        tag = "high credibility" if mcs > 0.75 else "average" if mcs > 0.5 else "low credibility"
        bullets.append(f"Track record: {tag}")
    else:
        bullets.append(f"MCS not computed — only {n} claim(s) tracked; no closed claims yet")
        bullets.append("Score defaulted to 50 (neutral)")
    bullets.append(f"→ M = {score}")
    return bullets


def valuation_rationale(co: dict, fs: dict, ta: dict, score: float) -> list[str]:
    t = fs.get("target", {}) or {}
    pe = t.get("PE_FY1")
    peer_pe = (fs.get("peer_aggregates", {}).get("median") or {}).get("PE_FY1")
    yoy = (co.get("fundamentals", {}).get("summary", {}) or {}).get("yoy_revenue_growth_pct") or 0
    spot = ta.get("spot") or ta.get("close")
    sma200 = ta.get("sma200")
    above_200 = (spot and sma200 and spot > sma200)
    p1y = ta.get("price_1y_change_pct") or 0
    bullets = []
    if pe and peer_pe:
        delta = (pe / peer_pe - 1) * 100
        if delta < 0:
            bullets.append(f"PE {pe:.1f}× vs peer median {peer_pe:.1f}× ({abs(delta):.0f}% discount to peers)")
        else:
            bullets.append(f"PE {pe:.1f}× vs peer median {peer_pe:.1f}× ({delta:.0f}% premium to peers)")
        # Value-trap / quality-premium detection
        if pe < peer_pe * 0.9 and not above_200 and yoy < 3 and p1y < 0:
            bullets.append("⚠ Value-trap pattern: cheap PE + below 200-DMA + sub-3% growth + negative 1Y return")
            bullets.append("  → V capped at 45 (cheap for a reason, not opportunity)")
        elif pe > peer_pe * 1.3 and above_200 and yoy >= 4 and p1y > 10:
            bullets.append("✓ Quality-premium pattern: high PE + above 200-DMA + >4% growth + +10% 1Y")
            bullets.append("  → V floored at 55 (premium deserved by defensive cash flow)")
    elif pe:
        bullets.append(f"PE {pe:.1f}× (peer-set not available, absolute scoring)")
    fcf_y = t.get("FCF_Yield_pct")
    div_y = t.get("Div_Yield_pct")
    if fcf_y is not None:
        bullets.append(f"FCF yield {fcf_y:.1f}%" + (" (+6 bonus)" if fcf_y >= 6 else " (+3)" if fcf_y >= 3 else ""))
    if div_y is not None and div_y >= 1.5:
        bullets.append(f"Dividend yield {div_y:.1f}%" + (" (+3)" if div_y >= 3 else " (+1)"))
    bullets.append(f"→ V = {score}")
    return bullets


def technicals_rationale(ta: dict, score: float) -> list[str]:
    spot = ta.get("spot") or ta.get("close")
    sma50, sma200 = ta.get("sma50"), ta.get("sma200")
    rsi = ta.get("rsi14")
    p90, p1y = ta.get("price_90d_change_pct"), ta.get("price_1y_change_pct")
    dd = ta.get("drawdown_from_52w_high_pct")
    bullets = []
    if spot and sma200:
        diff = (spot/sma200 - 1) * 100
        tag = "bullish trend" if diff > 5 else "neutral" if diff > -5 else "bearish trend"
        bullets.append(f"Spot ${spot:.2f} vs SMA200 ${sma200:.2f} ({diff:+.1f}%, {tag})")
    if sma50 and sma200:
        bullets.append("✓ Golden cross (SMA50 > SMA200)" if sma50 > sma200 else "✗ Death cross (SMA50 < SMA200)")
    if rsi is not None:
        zone = "overbought" if rsi > 70 else "oversold" if rsi < 30 else "neutral zone"
        bullets.append(f"RSI(14) {rsi:.1f} ({zone})")
    if p90 is not None:
        bullets.append(f"90-day return: {p90:+.1f}%")
    if p1y is not None:
        bullets.append(f"1-year return: {p1y:+.1f}%")
    if dd is not None:
        bullets.append(f"Drawdown from 52w high: {dd:.1f}%")
    bullets.append(f"→ T = {score}")
    return bullets


def options_rationale(opt: dict, ta: dict, score: float) -> list[str]:
    iv = opt.get("atm_iv_front_pct")
    pcr = opt.get("pcr_oi") or opt.get("put_call_ratio_oi")
    skew = opt.get("skew_atm_pct") or opt.get("skew_25d_pp")
    spot = ta.get("spot"); sma200 = ta.get("sma200")
    p90 = ta.get("price_90d_change_pct") or 0
    in_uptrend = (spot and sma200 and spot > sma200) and p90 > -5
    bullets = []
    if iv is not None:
        tag = "calm" if iv < 25 else "normal" if iv < 40 else "elevated" if iv < 60 else "high" if iv < 100 else "extreme"
        bullets.append(f"Front-month ATM IV {iv:.1f}% ({tag})")
    if pcr is not None:
        if pcr < 0.7:
            bullets.append(f"P/C OI {pcr:.2f} (call-heavy) — " + ("genuine bullish positioning (trend confirms)" if in_uptrend else "apathy (no trend support)"))
        elif pcr < 1.3:
            bullets.append(f"P/C OI {pcr:.2f} (balanced)")
        else:
            bullets.append(f"P/C OI {pcr:.2f} (put-heavy) — " + ("active hedging of gains (contrarian positive)" if in_uptrend else "bearish hedging (trend confirms)"))
    if skew is not None:
        bullets.append(f"ATM skew {skew:+.2f}pp " + ("(call skew)" if skew < 0 else "(put skew)"))
    bullets.append(f"→ O = {score} (trend-aligned)")
    return bullets
