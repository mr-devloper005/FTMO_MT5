"""Does a strategy beat buy & hold week by week and month by month?

A single 26-year total can hide a rule that wins once and drifts the rest of
the time. This walks rolling windows over the equity curve of ONE continuous
run — the strategy is never restarted — and asks, for each window, whether the
account gained more than simply holding the contract over those same bars.

That is the honest form of the question: a live system is not re-launched every
Monday, so the week's result is the change in its equity across that week.
"""
import json, statistics
from datetime import date, timedelta
from pathlib import Path

import study.lab.strats
from study.lab import data
from ftmo_mt5.backtest import BacktestConfig, run_backtest
from ftmo_mt5.instruments import resolve_instrument

CAPITAL, COMMISSION, SLIP = 100_000.0, 2.50, 1.0
TODAY = date.today()


def curve(symbol, tf, spec):
    """One continuous run; returns the equity curve aligned to the candles."""
    c = data.load(symbol, tf)
    cfg = dict(symbol=symbol, timeframe=data.ENGINE_TF.get(tf, tf),
               start=TODAY - timedelta(days=1), end=TODAY, data_source="yahoo",
               commission=COMMISSION, slippage_ticks=SLIP, initial_capital=CAPITAL,
               position_size=1.0, allow_long=True, allow_short=False)
    cfg.update(spec)
    r = run_backtest(BacktestConfig(**cfg), data.series(symbol, tf, c))
    eq = [v for _, v in r.equity_curve]
    n = min(len(eq), len(c))
    return c[:n], eq[:n], resolve_instrument(symbol).contract_multiplier


def windows(candles, seconds):
    """Index pairs (i, j) for consecutive periods of `seconds`, by real time."""
    out, start, i = [], 0, 0
    while i < len(candles):
        if candles[i].timestamp - candles[start].timestamp >= seconds:
            out.append((start, i)); start = i
        i += 1
    return out


def measure(symbol, tf, spec, label):
    c, eq, mult = curve(symbol, tf, spec)
    rows = {}
    for period, secs in (("1 week", 7 * 86400), ("1 month", 30 * 86400)):
        w = windows(c, secs)
        if not w:
            continue
        beat = flat = 0
        diffs, s_pnls, b_pnls = [], [], []
        for i, j in w:
            s = eq[j] - eq[i]
            b = (c[j].close - c[i].close) * mult
            diffs.append(s - b); s_pnls.append(s); b_pnls.append(b)
            if s > b: beat += 1
            elif s == b: flat += 1
        rows[period] = {
            "windows": len(w), "beat": beat, "tied": flat,
            "beatPct": round(beat / len(w) * 100, 1),
            "strategyProfitablePct": round(sum(1 for x in s_pnls if x > 0) / len(w) * 100, 1),
            "bhProfitablePct": round(sum(1 for x in b_pnls if x > 0) / len(w) * 100, 1),
            "medianDiff": round(statistics.median(diffs)),
            "meanDiff": round(statistics.fmean(diffs)),
            "worstDiff": round(min(diffs)), "bestDiff": round(max(diffs)),
        }
    return {"label": label, "symbol": symbol, "tf": tf, "periods": rows}


CAND = [
 ("EMA crossover + RSI (L/S)", "/CL", "1d",
  dict(strategy="EMA_RSI_PATTERN", allow_short=True, stop_loss_atr=2.0, take_profit_atr=3.0,
       strategy_parameters={"fast_period":20,"slow_period":50,"rsi_buy_above":50.0,
       "rsi_sell_below":50.0,"require_pattern":True})),
 ("MACD + ADX trend", "/CL", "6h",
  dict(strategy="MACD_ADX", allow_short=True,
       strategy_parameters={"adx_min":25.0,"adx_exit":15.0,"trade_shorts":True})),
 ("Multi-timeframe EMA trend", "/CL", "8h",
  dict(strategy="MTF_GREEN_STACK", allow_short=True, trailing_stop_atr=2.0,
       strategy_parameters={"tf1_minutes":480,"tf2_minutes":960,"tf3_minutes":1920,
       "require_completed":True,"trade_shorts":True,"exit_on_condition":True})),
 ("Turtle 20/10 long+short", "/CL", "1d",
  dict(strategy="TURTLE_LS", allow_short=True, stop_loss_atr=2.0,
       strategy_parameters={"entry_lookback":20,"exit_lookback":10,"trade_shorts":True})),
 ("Trend regime band L/S", "/CL", "1h",
  dict(strategy="TREND_REGIME", allow_short=True,
       strategy_parameters={"regime_period":200,"buffer_pct":0.5})),
 ("Daily 200-SMA swing", "/NQ", "1d",
  dict(strategy="DAILY_SWING_SMA200", allow_short=False,
       strategy_parameters={"sma_period":200,"buffer_pct":1.0,"max_extension_pct":5.0,
       "require_bullish_candle":True,"use_last_sell_filter":False,"exit_confirm_days":0})),
 ("Trend regime + band (long)", "/GC", "1d",
  dict(strategy="TREND_REGIME", allow_short=False,
       strategy_parameters={"regime_period":200,"buffer_pct":0.5})),
]

if __name__ == "__main__":
    out = []
    print("  Rolling windows — how often does the same running strategy beat buy & hold?\n")
    print(f"  {'strategy':<28}{'cell':<9}{'period':<9}{'windows':>9}{'beat':>7}"
          f"{'beat %':>9}{'median diff':>13}{'worst':>11}{'best':>11}")
    for label, sym, tf, spec in CAND:
        m = measure(sym, tf, spec, label)
        out.append(m)
        for period, r in m["periods"].items():
            print(f"  {label[:27]:<28}{sym+' '+tf:<9}{period:<9}{r['windows']:>9}"
                  f"{r['beat']:>7}{r['beatPct']:>8.1f}%{r['medianDiff']:>13,}"
                  f"{r['worstDiff']:>11,}{r['bestDiff']:>11,}")
        print()
    Path("study/lab/rolling.json").write_text(json.dumps(out, indent=1))
