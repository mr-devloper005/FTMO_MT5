"""One backtest, fully measured.

Nothing here re-implements execution. The project engine does the filling, the
stops and the buy-and-hold benchmark; this module chooses the window, passes
the configuration through, and collects the numbers the research loop reports —
including the two the engine does not compute because they are judgements
about evidence rather than about money: how many bars the strategy could
actually act on, and how much of the profit came from one trade.
"""
from __future__ import annotations

import math
import statistics
from datetime import date, timedelta
from typing import Dict, List, Optional

from ftmo_mt5.backtest import BacktestConfig, run_backtest
from ftmo_mt5.lab import windows

CAPITAL = 100_000.0
COMMISSION = 2.50        # per contract per side
SLIPPAGE_TICKS = 1.0     # one tick in and one tick out

# Parameter names that shift the first bar a strategy can act on. Used to turn
# `bars` into `usableBars`, which is the number that actually matters when a
# 200-period average is asked to work inside a 250-bar window.
_WARMUP_HINTS = ("period", "length", "lookback", "window", "slow", "bars")


def warmup_bars(parameters: Dict) -> int:
    """Longest lookback the configuration implies, inferred from its names.

    A heuristic, and reported as one: it reads the numeric parameters whose
    names say they are lookbacks. It is the difference between "600 bars" and
    "400 bars this rule could form an opinion about".
    """
    longest = 0
    for key, value in (parameters or {}).items():
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            continue
        if any(hint in key.lower() for hint in _WARMUP_HINTS):
            longest = max(longest, int(value))
    return longest


# Below this the benchmark is not a denominator anyone should divide by.
MIN_BENCHMARK_FOR_RATIO = 1_000.0


def _beat_pct(net: float, benchmark: float) -> Optional[float]:
    """Net profit's margin over buy & hold, as a percentage of buy & hold."""
    if abs(benchmark) < MIN_BENCHMARK_FOR_RATIO:
        return None
    return round((net - benchmark) / abs(benchmark) * 100, 1)


def sharpe(curve, bars_per_year: int) -> Optional[float]:
    values = [v for _, v in curve]
    returns = [(b - a) / a for a, b in zip(values, values[1:]) if a]
    if len(returns) < 30:
        return None
    sd = statistics.pstdev(returns)
    return None if sd == 0 else statistics.fmean(returns) / sd * math.sqrt(bars_per_year)


def peak_stats(candles: List, bars_per_year: int) -> Dict:
    """Where buy & hold peaked, and what holding past it cost.

    Every strategy in this lab is scored against buy-and-hold-measured-to-the-
    last-bar, which quietly assumes the last bar is a fair place to stop. It
    often isn't: TARIL was +2,051% at its 2025-01-03 peak and +945% eighteen
    months later. Scoring only against the endpoint hides that entirely, so
    the peak, its date, and the give-back since are reported alongside it.

    `bhPeakReturnPct` is the second benchmark the endpoint one was hiding:
    what buy & hold would have returned to a seller who left at the top.
    """
    if not candles:
        return {}
    start = candles[0].close
    if start <= 0:
        return {}

    peak_i = max(range(len(candles)), key=lambda i: candles[i].close)
    peak = candles[peak_i].close
    last = candles[-1].close

    span_bars = len(candles)
    years = span_bars / bars_per_year if bars_per_year else 0.0
    total = last / start
    cagr = ((total ** (1 / years) - 1) * 100) if years > 0 and total > 0 else None

    return {
        # Buy & hold measured to its own best close, not just to the last bar.
        "bhPeakReturnPct": round((peak / start - 1) * 100, 2),
        "bhPeakTimestamp": candles[peak_i].timestamp,
        "bhPeakBar": peak_i,
        # What holding past the peak gave back. Negative by construction
        # unless the last bar *is* the peak.
        "bhGiveBackPct": round((last / peak - 1) * 100, 2),
        "bhCagrPct": round(cagr, 2) if cagr is not None else None,
        "windowYears": round(years, 2) if years else None,
    }


def _config(symbol: str, timeframe: str, spec: Dict) -> BacktestConfig:
    today = date.today()
    cfg = dict(
        symbol=symbol, timeframe=windows.ENGINE_TF.get(timeframe, timeframe),
        # The series is passed in whole, so these bound nothing; the engine
        # requires them, and the window is the bar count, not the date range.
        start=today - timedelta(days=1), end=today, data_source="yahoo",
        commission=COMMISSION, slippage_ticks=SLIPPAGE_TICKS,
        initial_capital=CAPITAL, position_size=1.0,
        allow_long=True, allow_short=False,
    )
    cfg.update(spec)
    return BacktestConfig(**cfg)


def measure(symbol: str, timeframe: str, spec: Dict, candles: List,
            detail: bool = False) -> Dict:
    """Run one specification over one window and return every metric.

    With `detail`, the trade list and equity curve come back too — that is the
    difference between a matrix cell and the run the user opened.
    """
    result = run_backtest(_config(symbol, timeframe, spec),
                          windows.series(symbol, timeframe, candles))
    m = result.metrics()
    b = result.benchmark() or {}

    held = sum(t.bars_held for t in result.trades)
    wins = sorted((t.net_pnl for t in result.trades if t.net_pnl > 0), reverse=True)
    gross_profit = m["grossProfit"] or 0.0
    warm = warmup_bars(spec.get("strategy_parameters") or {})

    out = {
        "symbol": symbol, "timeframe": timeframe,
        "strategy": spec.get("strategy"),
        "trades": m["totalTrades"], "winRate": m["winRate"],
        "net": m["netPnl"], "returnPct": m["returnPct"],
        "grossProfit": gross_profit, "grossLoss": m["grossLoss"],
        "pf": m["profitFactor"], "avgTrade": m["averageTrade"],
        "avgWin": m["averageWinner"], "avgLoss": m["averageLoser"],
        "bigWin": m["largestWinner"], "bigLoss": m["largestLoser"],
        "maxDD": m["maxDrawdown"], "maxDDPct": m["maxDrawdownPct"],
        "commission": m["totalCommission"],
        "timeInMarket": round(held / len(candles) * 100, 1) if candles else 0.0,
        "sharpe": sharpe(result.equity_curve, windows.BARS_PER_YEAR[timeframe]),
        # Share of gross profit contributed by the single best trade. High
        # means the result is one outcome wearing the costume of a system.
        "topTradeShare": (round(wins[0] / gross_profit * 100, 1)
                          if wins and gross_profit > 0 else 0.0),
        "bh": b.get("netPnl", 0.0),
        "bhReturnPct": b.get("returnPct", 0.0),
        "bhDDPct": b.get("maxDrawdownPct", 0.0),
        "vsBH": b.get("outperformance", 0.0),
        # Outperformance in percentage points of starting capital, not as a
        # fraction of the benchmark: a benchmark near zero would make that
        # second form explode, and this one stays comparable across
        # instruments whose contract multipliers differ by 50x.
        "vsBHPct": round(m["returnPct"] - b.get("returnPct", 0.0), 2),
        # By how much the net profit beat buy & hold, as a percentage of what
        # buy & hold made. None when the benchmark is too near zero to divide
        # by: $500 turned into $600 is not a 20% edge, it is two small numbers.
        "beatPct": _beat_pct(m["netPnl"], b.get("netPnl", 0.0)),
        "beatsBH": bool(b.get("beatsBuyAndHold", False)),
        "bars": len(candles),
        "warmupBars": warm,
        "usableBars": max(0, len(candles) - warm),
        "from": candles[0].timestamp if candles else None,
        "to": candles[-1].timestamp if candles else None,
    }

    # Buy & hold's peak, its date, and the give-back since -- the second
    # benchmark that scoring-to-the-last-bar hides.
    bpy = windows.BARS_PER_YEAR[timeframe]
    out.update(peak_stats(candles, bpy))

    # The same three questions asked of the strategy's own equity curve, so a
    # rule that exits near the top is visibly rewarded rather than being
    # scored only on where it happened to end.
    curve = [v for _, v in result.equity_curve]
    if curve and curve[0] > 0:
        best = max(curve)
        years = out.get("windowYears") or 0
        grew = curve[-1] / curve[0]
        out["peakEquityPct"] = round((best / curve[0] - 1) * 100, 2)
        out["equityGiveBackPct"] = round((curve[-1] / best - 1) * 100, 2)
        out["cagrPct"] = (round(((grew ** (1 / years) - 1) * 100), 2)
                          if years > 0 and grew > 0 else None)
        # Beating buy & hold measured at *its* peak is the harder, more honest
        # comparison -- and the one the hold-until-broken family exists for.
        if out.get("bhPeakReturnPct") is not None:
            out["vsBHPeakPct"] = round(out["returnPct"] - out["bhPeakReturnPct"], 2)

    if detail:
        out["trades_list"] = [t.as_dict() for t in result.trades]
        out["equity"] = [[t, round(v, 2)] for t, v in result.equity_curve]
        out["candles"] = [[c.timestamp, c.open, c.high, c.low, c.close]
                          for c in candles]
    return out


def net_without_top(result: Dict, n: int) -> Optional[float]:
    """Net profit with the `n` largest winners deleted.

    Needs a detailed run; a matrix cell has no trade list to remove from.
    """
    trades = result.get("trades_list")
    if trades is None:
        return None
    wins = sorted((t["netPnl"] for t in trades if t["netPnl"] > 0), reverse=True)
    return result["net"] - sum(wins[:n])
