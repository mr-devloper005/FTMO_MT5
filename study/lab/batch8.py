"""The full matrix — every strategy on ten timeframes across three markets.

Timeframe-dependent parameters scale with the bar size rather than being fixed,
so "the 4-hour trend" means the same thing to a 1-hour run as "the daily trend"
does to a 6-hour one. Nothing else changes between cells.
"""
import json, sys
from pathlib import Path
import study.lab.strats
from study.lab import harness, board, data

TFS = ("5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d")
MIN = {"5m":5,"15m":15,"30m":30,"1h":60,"2h":120,"4h":240,"6h":360,"8h":480,
       "12h":720,"1d":1440}


def specs(tf):
    m = MIN[tf]
    h = m * 4                      # the conventional higher timeframe
    return [
     ("EMA trend following", dict(strategy="EMA_REGIME_LS", allow_short=True,
        trailing_stop_atr=2.0, strategy_parameters={"period":100,"trade_shorts":True})),
     ("EMA trend, long only", dict(strategy="EMA_REGIME_LS", allow_short=False,
        trailing_stop_atr=2.0, strategy_parameters={"period":100,"trade_shorts":False})),
     ("EMA + RSI pullback", dict(strategy="EMA_RSI_PULLBACK", allow_short=False,
        stop_loss_atr=2.0, take_profit_atr=3.0, strategy_parameters={"trend_minutes":h})),
     ("EMA+RSI+ATR risk mgmt", dict(strategy="TREND_REGIME_PULLBACK", allow_short=False,
        strategy_parameters={"trend_minutes":h})),
     ("Multi-timeframe EMA trend", dict(strategy="MTF_GREEN_STACK", allow_short=True,
        trailing_stop_atr=2.0, strategy_parameters={"tf1_minutes":m,"tf2_minutes":m*2,
        "tf3_minutes":m*4,"require_completed":True,"trade_shorts":True,
        "exit_on_condition":True})),
     ("EMA crossover + RSI", dict(strategy="EMA_RSI_PATTERN", allow_short=True,
        stop_loss_atr=2.0, take_profit_atr=3.0, strategy_parameters={"fast_period":20,
        "slow_period":50,"rsi_buy_above":50.0,"rsi_sell_below":50.0,"require_pattern":True})),
     ("EMA crossover + RSI, long", dict(strategy="EMA_RSI_PATTERN", allow_short=False,
        stop_loss_atr=2.0, take_profit_atr=3.0, strategy_parameters={"fast_period":20,
        "slow_period":50,"rsi_buy_above":50.0,"rsi_sell_below":50.0,"require_pattern":True})),
     ("Trend regime SMA200", dict(strategy="TREND_REGIME", allow_short=False,
        strategy_parameters={"regime_period":200,"buffer_pct":0.0})),
     ("Trend regime + 0.5% band", dict(strategy="TREND_REGIME", allow_short=False,
        strategy_parameters={"regime_period":200,"buffer_pct":0.5})),
     ("Trend regime band L/S", dict(strategy="TREND_REGIME", allow_short=True,
        strategy_parameters={"regime_period":200,"buffer_pct":0.5})),
     ("Four-regime breakout", dict(strategy="FOUR_REGIME", allow_short=False,
        trailing_stop_atr=2.0, strategy_parameters={"trend_minutes":h})),
     ("Donchian breakout 55/20", dict(strategy="DONCHIAN_BREAKOUT", allow_short=True,
        strategy_parameters={"entry_lookback":55,"exit_lookback":20})),
     ("Donchian long only", dict(strategy="DONCHIAN_BREAKOUT", allow_short=False,
        strategy_parameters={"entry_lookback":55,"exit_lookback":20})),
     ("Mean reversion + RSI", dict(strategy="MEAN_REVERSION", allow_short=True,
        stop_loss_atr=3.0, strategy_parameters={"period":100,"entry_z":2.5,
        "exit_z":0.5,"trade_shorts":True})),
     ("ATR-managed trend (master)", dict(strategy="GOLD_MASTER_TREND", allow_short=False,
        trailing_stop_atr=2.0, breakeven_at_atr=1.0, trail_activate_atr=2.0,
        strategy_parameters={"trend_minutes":h})),
     ("Volatility squeeze breakout", dict(strategy="SQUEEZE_BREAKOUT", allow_short=True,
        trailing_stop_atr=2.5, strategy_parameters={"bb_period":20,"squeeze_pct":0.25,
        "rank_window":250,"range_lookback":20,"trend_period":100,"trade_shorts":True})),
     ("MACD + ADX trend", dict(strategy="MACD_ADX", allow_short=True,
        strategy_parameters={"adx_min":25.0,"adx_exit":15.0,"trade_shorts":True})),
     ("ADX regime switch", dict(strategy="ADX_SWITCH", allow_short=True, stop_loss_atr=3.0,
        strategy_parameters={"trend_above":25.0,"range_below":20.0,"entry_z":2.0,
        "exit_z":0.3,"trade_shorts":True})),
     ("Keltner channel ride", dict(strategy="KELTNER_RIDE", allow_short=True,
        strategy_parameters={"ema_period":20,"entry_atr":1.0,"trend_period":200,
        "trade_shorts":True})),
     ("Turtle 20/10 long+short", dict(strategy="TURTLE_LS", allow_short=True,
        stop_loss_atr=2.0, strategy_parameters={"entry_lookback":20,"exit_lookback":10,
        "trade_shorts":True})),
     ("Dual regime (2 timeframes)", dict(strategy="DUAL_REGIME", allow_short=False,
        strategy_parameters={"period":200,"buffer_pct":0.5,"higher_minutes":h,
        "higher_period":100,"trade_shorts":False})),
     ("Regime + buy-below-short", dict(strategy="REGIME_RATCHET", allow_short=True,
        strategy_parameters={"regime_period":200,"buffer_pct":0.5,"rearm_bars":0})),
     ("Daily 200-SMA swing", dict(strategy="DAILY_SWING_SMA200", allow_short=False,
        strategy_parameters={"sma_period":200,"buffer_pct":1.0,"max_extension_pct":5.0,
        "require_bullish_candle":True,"use_last_sell_filter":False,"exit_confirm_days":0})),
    ]


def main():
    rows, beats = [], 0
    for sym in ("/NQ", "/GC", "/CL"):
        for tf in TFS:
            c = data.load(sym, tf)
            bh = None
            for name, spec in specs(tf):
                try:
                    r = harness.run(sym, tf, spec, c)
                except Exception as e:
                    continue
                bh = r["bh"]
                board.add(batch="matrix", symbol=sym, tf=tf, name=name, spec=spec, result=r)
                rows.append(dict(symbol=sym, tf=tf, name=name, **{
                    k: r[k] for k in ("trades","net","bh","vsBH","beatsBH","pf","winRate",
                                      "maxDDPct","bhDDPct","sharpe","timeInMarket",
                                      "topTradeShare","bars","avgTrade","bigWin","bigLoss",
                                      "commission","returnPct","bhReturnPct")}))
                beats += bool(r["beatsBH"])
            print(f"  {sym} {tf:<4} {len(c):>7,} bars   B&H {bh:>12,.0f}   "
                  f"running beats: {beats}", flush=True)
    Path("study/lab/matrix.json").write_text(json.dumps(rows, indent=1))
    print(f"\n  {len(rows)} runs, {beats} beat buy & hold")


if __name__ == "__main__":
    main()
