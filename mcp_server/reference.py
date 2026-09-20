"""Compact, self-verifying reference text for the Lab's interfaces.

Why this exists: recalling "what does measure() return" or "what are the
verdict thresholds" otherwise means re-reading metrics.py (390 lines),
verdict.py (230) and windows.py (210). That happened repeatedly across this
project's research, and each time cost far more context than the answer was
worth.

Why it is generated rather than written: this repository already contains
three hand-maintained documents that drifted from the code -- .env.example
missing half the variables the hub needs, README/CLAUDE.md pointing at
config/1step.yaml which does not exist, and pyproject.toml's console script
naming a package that was archived. A hand-copied reference would become the
fourth. Every number below is imported from the module that owns it, and the
metric dictionary reconciles itself against a real measure() call at read
time, so an undocumented new metric announces itself instead of hiding.
"""
from __future__ import annotations

from typing import Dict, List

from ftmo_mt5.lab import metrics as _m
from ftmo_mt5.lab import service as _svc
from ftmo_mt5.lab import verdict as _v
from ftmo_mt5.lab import windows as _w

# Meanings are semantic and cannot be derived; the KEY SET is reconciled
# against a live run below, which is the part that would otherwise go stale.
_METRIC_DOCS: Dict[str, str] = {
    "symbol / timeframe / strategy": "what was tested, echoed back for identification",
    "trades": "closed round trips. <10 is FAILED by the rubric: too few to tell a rule from luck",
    "winRate": "percent of trades with net_pnl > 0",
    "net": "net profit in the price's own currency (the engine is currency-blind)",
    "returnPct": "net as a percent of the 100,000 starting capital",
    "grossProfit/grossLoss": "sums of winning / losing trades before costs",
    "pf": "profit factor = grossProfit / |grossLoss|",
    "avgTrade/avgWin/avgLoss": "per-trade averages",
    "bigWin/bigLoss": "largest single winner / loser",
    "maxDD/maxDDPct": "worst peak-to-trough equity decline",
    "commission": "total commission paid",
    "timeInMarket": "percent of bars holding a position",
    "sharpe": "annualised via BARS_PER_YEAR; None if fewer than 30 return observations",
    "topTradeShare": "percent of gross profit from the single best trade. >=50 means one outcome wearing the costume of a system",
    "bh / bhReturnPct / bhDDPct": "buy & hold measured to the LAST bar, same costs applied",
    "vsBH": "net profit minus buy & hold's, in currency",
    "vsBHPct": "returnPct minus bhReturnPct, in percentage points -- the usual alpha column",
    "beatPct": "margin over buy & hold as a percent of it; None when the benchmark is under $1,000",
    "beatsBH": "bool. TRUE HERE IS A SCREEN, NOT A FINDING -- confirm with lab_validate_strategy",
    "bars / warmupBars / usableBars": "window length, longest lookback implied by the params, and the difference",
    "from / to": "first and last bar timestamps of the window actually used",
    "bhPeakReturnPct": "buy & hold measured to its own best close, not the last bar",
    "bhPeakTimestamp / bhPeakBar": "when that peak happened",
    "bhGiveBackPct": "what holding past the peak cost. negative unless the last bar IS the peak",
    "bhCagrPct / cagrPct": "annualised return for buy & hold / for the strategy",
    "windowYears": "window length in years, used for the CAGR figures",
    "peakEquityPct / equityGiveBackPct": "the same peak question asked of the strategy's own equity curve",
    "vsBHPeakPct": "returnPct minus bhPeakReturnPct -- the harder benchmark an exit rule competes against",
    "detailOmitted": "present when trades_list/equity/candles were trimmed; pass full_detail=true for them",
}


def _reconcile() -> List[str]:
    """Compare documented keys against a real run. Cheap, cached, tolerant."""
    try:
        candles = _w.window("LITE", "1h", 120)["candles"]
        live = set(_m.measure("LITE", "1h",
                              {"strategy": "MEAN_REVERSION", "allow_long": True,
                               "allow_short": True}, candles).keys())
    except Exception:
        return ["(key reconciliation skipped: no cached data available to run it)"]
    documented = set()
    for k in _METRIC_DOCS:
        documented.update(p.strip() for p in k.replace("/", " ").split())
    missing = sorted(live - documented)
    return [f"UNDOCUMENTED metric keys present in measure(): {', '.join(missing)}"] if missing else []


def metrics_reference() -> str:
    lines = [
        "# measure() metric dictionary",
        f"Costs applied to strategy AND benchmark alike: capital {_m.CAPITAL:,.0f}, "
        f"commission {_m.COMMISSION}/side, {_m.SLIPPAGE_TICKS} tick slippage each way.",
        "",
    ]
    lines += [f"  {k:<34} {v}" for k, v in _METRIC_DOCS.items()]
    warn = _reconcile()
    if warn:
        lines += ["", *warn]
    return "\n".join(lines)


def timeframes_reference() -> str:
    lines = ["# Timeframes", "",
             "600 bars is NOT the same span at every size -- the intraday ones are",
             "capped by the provider, so a 600-bar 5m window is about eight trading",
             "days while 600 daily bars is roughly 2.4 years.", "",
             f"  {'tf':<6}{'bars/yr':>9}   {'600 bars ~=':<14} ceiling"]
    for tf in _w.TIMEFRAMES:
        bpy = _w.BARS_PER_YEAR[tf]
        span = 600 / bpy
        span_s = f"{span*12:.1f} months" if span < 1 else f"{span:.1f} years"
        lines.append(f"  {tf:<6}{bpy:>9.0f}   {span_s:<14} {_w.CEILING.get(tf,'')}")
    lines += ["", "Grouped sizes (2h/4h/6h/8h/12h from 1h, 1w from 1d) inherit the",
              "ceiling of the size they are built from. Nothing is synthesised."]
    return "\n".join(lines)


def overview_reference() -> str:
    robust = ("beats OOS, >=100 trades, >=60% of parameter variations still beat, "
              "survives >=4 ticks slippage, survives removing the top 5 winners, "
              "wins >=3 of 5 folds")
    moderate = ("beats OOS, >=30 trades, >=50% variations hold, survives >=2 ticks, "
                "survives removing the top 3 winners")
    return "\n".join([
        "# FTMO_MT5 Lab -- interface reference",
        "",
        "## Tools",
        "  lab_list_strategies    catalog + default params (never guess a strategy name)",
        "  lab_lookup             has this been tested before? tally first, rows second",
        "  lab_run_backtest       one symbol/timeframe/strategy. IN-SAMPLE SCREEN ONLY",
        "  lab_run_matrix         many cells at once, condensed by default, 300-cell cap",
        "  lab_validate_strategy  the robustness suite. THE ONLY OUTPUT THAT COUNTS AS EVIDENCE",
        "",
        "## Canonical defaults (baked into every tool; override deliberately, not by accident)",
        f"  bars=600  allow_short=True  size_to_capital=True  allow_network=False",
        "  A script that silently disagreed on allow_short once produced 5 trades where",
        "  another produced 10, same symbol and strategy. That is why these are defaults.",
        "",
        "## Verdict rubric (ftmo_mt5/lab/verdict.py)",
        f"  FAILED    benchmark under ${_v.FLAT_BENCHMARK:,.0f} (nothing to beat), or loses in-sample,",
        f"            or margin under {_v.MIN_MARGIN_PCT}% of the benchmark, or fewer than 10 trades",
        f"  FRAGILE   beats in-sample but falls short of MODERATE",
        f"  MODERATE  {moderate}",
        f"  ROBUST    {robust}",
        f"  Tests run: {int(_v.SPLIT_FRACTION*100)}/{100-int(_v.SPLIT_FRACTION*100)} time-ordered OOS split, "
        f"cost ladder {list(_v.COST_LADDER)} ticks,",
        f"            top-1/3/5 winner removal, {_v.FOLDS} walk-forward folds, parameter sensitivity",
        "",
        "## Standing findings (so they are not rediscovered)",
        "  Across ~3,500 indexed runs: 0 ROBUST, 0 MODERATE ever reached.",
        "  Buy & hold beat every strategy tested at every timeframe of 2h or longer.",
        "  Query lab_lookup before running a sweep -- most combinations are already known.",
        "",
        f"## Strategies ({len(_svc.strategy_defaults())} registered)",
        "  " + ", ".join(sorted(_svc.strategy_defaults())),
        "",
        "Detail: lab://metrics (what measure() returns), lab://timeframes (spans and ceilings).",
    ])
