"""Gold futures strategy backtest study.

Ten strategies, two timeframe pairings, one engine. Every number the report
quotes is written to `study/results.json` by this script and read back by the
document generator, so the report cannot drift from the backtest.

Nothing here re-implements the engine. Data comes from the project's Yahoo
provider, indicators from `ftmo_mt5.signals.indicators`, execution and the
buy-and-hold benchmark from `ftmo_mt5.backtest.engine`.
"""
import json, logging, math, statistics, structlog
structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.CRITICAL))
from datetime import date, timedelta
import datetime as dt
from pathlib import Path

from ftmo_mt5.marketdata.base import Candle, CandleSeries
from ftmo_mt5.marketdata.yahoo import YahooHistoricalDataProvider
from ftmo_mt5.backtest import BacktestConfig, run_backtest

PROV, TODAY, SYMBOL = YahooHistoricalDataProvider(), date.today(), "/GC"
CAPITAL, COMMISSION, SLIPPAGE = 100_000.0, 2.50, 1.0

# ---------------------------------------------------------------------------
# Data. 15m is capped by Yahoo at 60 days and 1h at two years; 4h is built by
# grouping real 1h bars, never synthesised.
# ---------------------------------------------------------------------------

def group(candles, seconds, factor):
    if factor <= 1:
        return list(candles)
    out, b = [], []
    def flush():
        if b:
            out.append(Candle(timestamp=b[0].timestamp, open=b[0].open,
                              high=max(x.high for x in b), low=min(x.low for x in b),
                              close=b[-1].close, volume=sum(x.volume or 0 for x in b)))
            b.clear()
    for c in candles:
        if b and (int(c.timestamp) // seconds != int(b[0].timestamp) // seconds
                  or len(b) >= factor):
            flush()
        b.append(c)
    flush()
    return out


def load(tf):
    if tf == "15m":
        return PROV.get_history(SYMBOL, "15m", TODAY - timedelta(days=59), TODAY).candles
    if tf == "1h":
        return PROV.get_history(SYMBOL, "1h", TODAY - timedelta(days=720), TODAY).candles
    raise ValueError(tf)


# ---------------------------------------------------------------------------
# The ten strategies. Each is a trading idea, not a parameter tweak, and each
# has an explicit exit. Parameters are the specifications' own defaults or the
# conventional values for the indicator; none were chosen by searching for the
# best result.
# ---------------------------------------------------------------------------
def S(no, name, idea, entry_tf, trend_tf, indicators, buy, sell, exit_rule,
      stop, target, trail, sizing, spec, notes=""):
    return dict(no=no, name=name, idea=idea, entry_tf=entry_tf, trend_tf=trend_tf,
                indicators=indicators, buy=buy, sell=sell, exit=exit_rule,
                stop=stop, target=target, trail=trail, sizing=sizing,
                spec=spec, notes=notes)


def build(entry_tf, trend_min, trend_label):
    """The same ten ideas, with the context timeframe set for this pairing."""
    m = {"15m": 15, "1h": 60}[entry_tf]
    return [
     S(1, "EMA trend following", "Hold the market in whichever direction it is already moving.",
       entry_tf, "none (single timeframe)", "EMA 100",
       "Close above EMA 100.", "Close below EMA 100 (short).",
       "Reversed by the opposite signal, or the trailing stop.",
       "none", "none", "2 x ATR(14)", "1 contract",
       dict(strategy="EMA_REGIME_LS", allow_short=True, trailing_stop_atr=2.0,
            strategy_parameters={"period": 100, "trade_shorts": True}),
       "EMA 100 is the conventional medium-term trend average."),

     S(2, "EMA + RSI pullback", "Buy a dip inside an established uptrend.",
       entry_tf, trend_label, "EMA 20, EMA 50, EMA 200, RSI 14, ATR 14",
       "Context: close above EMA 200, EMA 50 above EMA 200, RSI above 52. "
       "Entry: price touches the EMA 20/50 band, RSI dipped below 45 within "
       "10 bars, RSI crosses above 50, candle closes up.",
       "None - long only.",
       "Stop or target only; the rule defines no exit of its own.",
       "2 x ATR(14)", "3 x ATR(14)", "none", "1 contract",
       dict(strategy="EMA_RSI_PULLBACK", allow_short=False,
            stop_loss_atr=2.0, take_profit_atr=3.0,
            strategy_parameters={"trend_minutes": trend_min}),
       "Thresholds are the specification's; the 2/3 ATR bracket is the "
       "conventional 1:1.5 risk-reward."),

     S(3, "EMA + RSI + ATR risk management",
       "The same pullback, but the position is closed when the entry timeframe "
       "loses its trend rather than at a fixed target.",
       entry_tf, trend_label, "EMA 20, EMA 50, EMA 200, RSI 14, ATR 14",
       "Context: close above EMA 200, EMA 50 above EMA 200, EMA 200 rising, "
       "RSI above 50. Entry: pullback into the EMA zone, RSI dip below 45, "
       "RSI crosses above 50, bullish close.",
       "None - long only.",
       "Close below EMA 50 with RSI below 45.",
       "the setup's own swing low", "none", "none", "1 contract",
       dict(strategy="TREND_REGIME_PULLBACK", allow_short=False,
            strategy_parameters={"trend_minutes": trend_min}),
       "The stop is placed by the rule at the most recent swing low, so it "
       "adapts to the setup rather than to a fixed distance."),

     S(4, "Multi-timeframe EMA trend",
       "Trade only when three timeframes agree on direction.",
       entry_tf, f"{m}m / {m*2}m / {m*4}m", "bar direction on three timeframes",
       "All three timeframes printing an up bar.",
       "All three printing a down bar (short).",
       "Timeframes disagree, or the trailing stop.",
       "none", "none", "2 x ATR(14)", "1 contract",
       dict(strategy="MTF_GREEN_STACK", allow_short=True, trailing_stop_atr=2.0,
            strategy_parameters={"tf1_minutes": m, "tf2_minutes": m*2,
                                 "tf3_minutes": m*4, "require_completed": True,
                                 "trade_shorts": True, "exit_on_condition": True}),
       "The 1/2/4 spacing keeps all three timeframes within the available history."),

     S(5, "EMA crossover + RSI confirmation",
       "Trade the EMA pair, but only when momentum and a candlestick pattern agree.",
       entry_tf, "none (single timeframe)", "EMA 20, EMA 50, RSI 14, candlestick patterns",
       "Price above EMA 20, EMA 20 above EMA 50, RSI above 50, bullish pattern.",
       "The mirror image (short).",
       "Reversed by the opposite signal, or the stop/target.",
       "2 x ATR(14)", "3 x ATR(14)", "none", "1 contract",
       dict(strategy="EMA_RSI_PATTERN", allow_short=True,
            stop_loss_atr=2.0, take_profit_atr=3.0,
            strategy_parameters={"fast_period": 20, "slow_period": 50,
                                 "rsi_buy_above": 50.0, "rsi_sell_below": 50.0,
                                 "require_pattern": True}),
       "The project's original rule, included as a baseline."),

     S(6, "Trend regime filter", "Own the market while it is above its long average, stand aside below it.",
       entry_tf, "none (single timeframe)", "SMA 200",
       "Close above SMA 200.", "None - long or flat.",
       "Close below SMA 200.",
       "none", "none", "none", "1 contract",
       dict(strategy="TREND_REGIME", allow_short=False,
            strategy_parameters={"regime_period": 200, "buffer_pct": 0.0}),
       "A participation rule rather than a trading rule; included because it is "
       "the simplest thing that can beat holding, by being absent during declines."),

     S(7, "Breakout + trend filter (four regime)",
       "Classify the market into four states and use the playbook each deserves.",
       entry_tf, trend_label, "EMA 8, EMA 14, EMA 200, ADX 14, ATR 14, RSI 14",
       "Regime 1: aligned rising uptrend with ADX above 20, entered on a pullback. "
       "Regime 2: a new 20-bar high with ATR expanding.",
       "None - long only. Regimes 3 (ADX low) and 4 (downtrend) stay in cash.",
       "Trailing stop; regime 4 blocks new entries.",
       "none", "none", "2 x ATR(14)", "1 contract",
       dict(strategy="FOUR_REGIME", allow_short=False, trailing_stop_atr=2.0,
            strategy_parameters={"trend_minutes": trend_min}),
       "ADX 20 is the conventional threshold between trend and range."),

     S(8, "Donchian breakout", "Buy a new N-bar high, leave on a new M-bar low.",
       entry_tf, "none (single timeframe)", "55-bar high, 20-bar low",
       "Close above the 55-bar high.", "Close below the 20-bar low (short).",
       "The opposite channel break.",
       "none", "none", "none", "1 contract",
       dict(strategy="DONCHIAN_BREAKOUT", allow_short=True,
            strategy_parameters={"entry_lookback": 55, "exit_lookback": 20}),
       "55/20 are the original Turtle parameters, not tuned here."),

     S(9, "Mean reversion + RSI", "Fade a stretch away from the average instead of following it.",
       entry_tf, "none (single timeframe)", "SMA 100, rolling standard deviation",
       "Close 2.5 standard deviations below the average.",
       "Close 2.5 standard deviations above the average (short).",
       "Price returns within 0.5 deviations of the average.",
       "3 x ATR(14)", "none", "none", "1 contract",
       dict(strategy="MEAN_REVERSION", allow_short=True, stop_loss_atr=3.0,
            strategy_parameters={"period": 100, "entry_z": 2.5, "exit_z": 0.5,
                                 "trade_shorts": True}),
       "Included as the opposite hypothesis to the eight trend rules above."),

     S(10, "ATR-managed trend (master)",
       "A strict multi-condition trend filter with staged stop management.",
       entry_tf, trend_label, "EMA 8, EMA 14, EMA 200, RSI 14, ATR 14",
       "Context: EMA 8 > EMA 14 > EMA 200, close above EMA 200, RSI above 50. "
       "Entry: pullback into the EMA 8/14 zone, RSI dip below 45, RSI crosses "
       "above 50, bullish candle closing above EMA 8.",
       "None - long only.",
       "Close below EMA 14 with RSI below 45, or the context breaking.",
       "1.5 x ATR(14)", "none",
       "2 x ATR, activated once 2 ATR in profit; stop to breakeven at 1 ATR",
       "1 contract",
       dict(strategy="GOLD_MASTER_TREND", allow_short=False,
            trailing_stop_atr=2.0, breakeven_at_atr=1.0, trail_activate_atr=2.0,
            strategy_parameters={"trend_minutes": trend_min}),
       "Every number here is the specification's own."),
    ]


# ---------------------------------------------------------------------------
# Running and measuring
# ---------------------------------------------------------------------------

def sharpe(curve, bars_per_year):
    """Annualised Sharpe from the equity curve, at a zero risk-free rate."""
    vals = [v for _, v in curve]
    rets = [(b - a) / a for a, b in zip(vals, vals[1:]) if a]
    if len(rets) < 30:
        return None
    sd = statistics.pstdev(rets)
    if sd == 0:
        return None
    return statistics.fmean(rets) / sd * math.sqrt(bars_per_year)


def measure(spec, candles, tf, bars_per_year, slip=SLIPPAGE):
    s = CandleSeries(symbol=SYMBOL, timeframe=tf, candles=candles, provider="yahoo")
    cfg = dict(symbol=SYMBOL, timeframe=tf, start=TODAY - timedelta(days=1),
               end=TODAY, data_source="yahoo", commission=COMMISSION,
               slippage_ticks=slip, allow_long=True,
               initial_capital=CAPITAL, position_size=1.0)
    cfg.update(spec)
    r = run_backtest(BacktestConfig(**cfg), s)
    m, b = r.metrics(), r.benchmark() or {}
    held = sum(t.bars_held for t in r.trades)
    wins = [t.net_pnl for t in r.trades if t.net_pnl > 0]
    top = max(wins) if wins else 0.0
    gp = m["grossProfit"] or 0.0
    return {
        "trades": m["totalTrades"], "winners": m["winningTrades"],
        "losers": m["losingTrades"], "winRate": m["winRate"],
        "net": m["netPnl"], "returnPct": m["returnPct"],
        "grossProfit": gp, "grossLoss": m["grossLoss"],
        "pf": m["profitFactor"], "avgTrade": m["averageTrade"],
        "avgWin": m["averageWinner"], "avgLoss": m["averageLoser"],
        "bigWin": m["largestWinner"], "bigLoss": m["largestLoser"],
        "maxDD": m["maxDrawdown"], "maxDDPct": m["maxDrawdownPct"],
        "commission": m["totalCommission"], "signals": r.signals,
        "timeInMarket": round(held / len(candles) * 100, 1) if candles else 0.0,
        "sharpe": sharpe(r.equity_curve, bars_per_year),
        # How much of the gross profit came from the single best trade. A high
        # number means the result rests on one outcome, not on a process.
        "topTradeShare": round(top / gp * 100, 1) if gp > 0 else 0.0,
        "bh": b.get("netPnl", 0.0), "bhReturnPct": b.get("returnPct", 0.0),
        "bhDDPct": b.get("maxDrawdownPct", 0.0),
        "vsBH": b.get("outperformance", 0.0),
        "beatsBH": bool(b.get("beatsBuyAndHold", False)),
    }


def main():
    out = {"generated": dt.datetime.now(dt.UTC).isoformat(),
           "symbol": SYMBOL, "capital": CAPITAL,
           "commission": COMMISSION, "slippage": SLIPPAGE, "runs": {}}

    for entry_tf, trend_min, trend_label in (("15m", 60, "1 hour"),
                                             ("1h", 240, "4 hour")):
        candles = load(entry_tf)
        span_days = (candles[-1].timestamp - candles[0].timestamp) / 86400
        bars_per_year = len(candles) / span_days * 365 if span_days else 252
        info = {
            "bars": len(candles), "spanDays": round(span_days),
            "from": dt.datetime.fromtimestamp(candles[0].timestamp, dt.UTC)
                      .strftime("%d %b %Y"),
            "to": dt.datetime.fromtimestamp(candles[-1].timestamp, dt.UTC)
                    .strftime("%d %b %Y"),
            "firstPrice": candles[0].open, "lastPrice": candles[-1].close,
            "barsPerYear": round(bars_per_year),
            "strategies": [],
        }
        for st in build(entry_tf, trend_min, trend_label):
            try:
                res = measure(st["spec"], candles, entry_tf, bars_per_year)
            except Exception as exc:
                res = {"error": str(exc)[:120]}
            row = {k: v for k, v in st.items() if k != "spec"}
            row["result"] = res
            info["strategies"].append(row)
            r = res
            if "error" in r:
                print(f"  {entry_tf:>4} {st['no']:>2}. {st['name']:<34} FAILED {r['error']}")
            else:
                print(f"  {entry_tf:>4} {st['no']:>2}. {st['name']:<34}"
                      f"{r['trades']:>6} tr {r['net']:>11,.0f} PF {r['pf'] or 0:>5.2f}"
                      f" DD {abs(r['maxDDPct']):>5.1f}% vsBH {r['vsBH']:>11,.0f}"
                      f" {'BEAT' if r['beatsBH'] else ''}")
        out["runs"][entry_tf] = info
        print()

    Path("study").mkdir(exist_ok=True)
    Path("study/results.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"  written study/results.json")


if __name__ == "__main__":
    main()
