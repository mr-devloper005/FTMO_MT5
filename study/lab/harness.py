"""One backtest, fully measured — plus the chronological train/test split.

Nothing here re-implements execution. `run_backtest` from the project engine
does the filling, the stops and the buy-and-hold benchmark; this module only
chooses the data window, passes the configuration through, and collects every
metric the research loop reports.
"""
import json, math, statistics
from datetime import date, timedelta
from pathlib import Path

from ftmo_mt5.backtest import BacktestConfig, run_backtest
from study.lab import data

CAPITAL = 100_000.0
COMMISSION = 2.50          # per contract per side
SLIPPAGE_TICKS = 1.0       # one tick in and one tick out
TODAY = date.today()

BARS_PER_YEAR = {"5m": 23400, "15m": 7800, "30m": 3900, "1h": 5716, "2h": 2858,
                 "4h": 1550, "6h": 1033, "8h": 775, "12h": 516, "1d": 252, "1w": 52}



def sharpe(curve, bars_per_year):
    vals = [v for _, v in curve]
    rets = [(b - a) / a for a, b in zip(vals, vals[1:]) if a]
    if len(rets) < 30:
        return None
    sd = statistics.pstdev(rets)
    return None if sd == 0 else statistics.fmean(rets) / sd * math.sqrt(bars_per_year)


def run(symbol, tf, spec, candles=None):
    """Run one strategy specification and return every metric."""
    c = candles if candles is not None else data.load(symbol, tf)
    cfg = dict(symbol=symbol, timeframe=data.ENGINE_TF.get(tf, tf),
               start=TODAY - timedelta(days=1), end=TODAY, data_source="yahoo",
               commission=COMMISSION, slippage_ticks=SLIPPAGE_TICKS,
               initial_capital=CAPITAL, position_size=1.0,
               allow_long=True, allow_short=False)
    cfg.update(spec)
    r = run_backtest(BacktestConfig(**cfg), data.series(symbol, tf, c))
    m, b = r.metrics(), r.benchmark() or {}
    held = sum(t.bars_held for t in r.trades)
    wins = [t.net_pnl for t in r.trades if t.net_pnl > 0]
    gp = m["grossProfit"] or 0.0
    return {
        "trades": m["totalTrades"], "winRate": m["winRate"],
        "net": m["netPnl"], "returnPct": m["returnPct"],
        "grossProfit": gp, "grossLoss": m["grossLoss"], "pf": m["profitFactor"],
        "avgTrade": m["averageTrade"], "avgWin": m["averageWinner"],
        "avgLoss": m["averageLoser"], "bigWin": m["largestWinner"],
        "bigLoss": m["largestLoser"], "maxDD": m["maxDrawdown"],
        "maxDDPct": m["maxDrawdownPct"], "commission": m["totalCommission"],
        "timeInMarket": round(held / len(c) * 100, 1) if c else 0.0,
        "sharpe": sharpe(r.equity_curve, BARS_PER_YEAR[tf]),
        # Share of gross profit contributed by the single best trade. High means
        # the result is one outcome wearing the costume of a system.
        "topTradeShare": round(max(wins) / gp * 100, 1) if (wins and gp > 0) else 0.0,
        "bh": b.get("netPnl", 0.0), "bhReturnPct": b.get("returnPct", 0.0),
        "bhDDPct": b.get("maxDrawdownPct", 0.0),
        "vsBH": b.get("outperformance", 0.0),
        "beatsBH": bool(b.get("beatsBuyAndHold", False)),
        "bars": len(c),
    }


def split(symbol, tf, spec, frac=0.70):
    """Chronological development / out-of-sample run of the same locked spec.

    The series is cut once, in time order, and never shuffled. Buy & hold is
    recomputed inside each half so the comparison uses the same window as the
    strategy it is being compared against.
    """
    c = data.load(symbol, tf)
    cut = int(len(c) * frac)
    return {"dev": run(symbol, tf, spec, c[:cut]),
            "oos": run(symbol, tf, spec, c[cut:]),
            "full": run(symbol, tf, spec, c)}
