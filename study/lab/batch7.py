"""Batch 7 — the whole strategy library on daily bars.

Every earlier batch ran on at most two years of intraday history, which held
one market regime and no bear market. Daily bars go back 26 years and contain
the dot-com bust, 2008, the 2014-16 oil collapse, COVID and 2022. If any of
these rules has a real edge rather than a lucky window, this is where it shows.
"""
import study.lab.strats
from study.lab import harness, board, data

W = 10080     # one week in minutes - the natural higher timeframe for daily bars

def specs():
    return [
     ("EMA trend following", dict(strategy="EMA_REGIME_LS", allow_short=True,
        trailing_stop_atr=2.0, strategy_parameters={"period":100,"trade_shorts":True})),
     ("EMA trend, long only", dict(strategy="EMA_REGIME_LS", allow_short=False,
        trailing_stop_atr=2.0, strategy_parameters={"period":100,"trade_shorts":False})),
     ("EMA + RSI pullback", dict(strategy="EMA_RSI_PULLBACK", allow_short=False,
        stop_loss_atr=2.0, take_profit_atr=3.0, strategy_parameters={"trend_minutes":W})),
     ("EMA+RSI+ATR risk mgmt", dict(strategy="TREND_REGIME_PULLBACK", allow_short=False,
        strategy_parameters={"trend_minutes":W})),
     ("Multi-timeframe EMA trend", dict(strategy="MTF_GREEN_STACK", allow_short=True,
        trailing_stop_atr=2.0, strategy_parameters={"tf1_minutes":1440,"tf2_minutes":2880,
        "tf3_minutes":5760,"require_completed":True,"trade_shorts":True,
        "exit_on_condition":True})),
     ("EMA crossover + RSI", dict(strategy="EMA_RSI_PATTERN", allow_short=True,
        stop_loss_atr=2.0, take_profit_atr=3.0, strategy_parameters={"fast_period":20,
        "slow_period":50,"rsi_buy_above":50.0,"rsi_sell_below":50.0,"require_pattern":True})),
     ("Trend regime SMA200", dict(strategy="TREND_REGIME", allow_short=False,
        strategy_parameters={"regime_period":200,"buffer_pct":0.0})),
     ("Trend regime + 0.5% band", dict(strategy="TREND_REGIME", allow_short=False,
        strategy_parameters={"regime_period":200,"buffer_pct":0.5})),
     ("Trend regime band L/S", dict(strategy="TREND_REGIME", allow_short=True,
        strategy_parameters={"regime_period":200,"buffer_pct":0.5})),
     ("Four-regime breakout", dict(strategy="FOUR_REGIME", allow_short=False,
        trailing_stop_atr=2.0, strategy_parameters={"trend_minutes":W})),
     ("Donchian breakout 55/20", dict(strategy="DONCHIAN_BREAKOUT", allow_short=True,
        strategy_parameters={"entry_lookback":55,"exit_lookback":20})),
     ("Donchian long only", dict(strategy="DONCHIAN_BREAKOUT", allow_short=False,
        strategy_parameters={"entry_lookback":55,"exit_lookback":20})),
     ("Mean reversion + RSI", dict(strategy="MEAN_REVERSION", allow_short=True,
        stop_loss_atr=3.0, strategy_parameters={"period":100,"entry_z":2.5,
        "exit_z":0.5,"trade_shorts":True})),
     ("ATR-managed trend (master)", dict(strategy="GOLD_MASTER_TREND", allow_short=False,
        trailing_stop_atr=2.0, breakeven_at_atr=1.0, trail_activate_atr=2.0,
        strategy_parameters={"trend_minutes":W})),
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
     ("Dual regime (daily+weekly)", dict(strategy="DUAL_REGIME", allow_short=False,
        strategy_parameters={"period":200,"buffer_pct":0.5,"higher_minutes":W,
        "higher_period":100,"trade_shorts":False})),
     ("Regime + buy-below-short", dict(strategy="REGIME_RATCHET", allow_short=True,
        strategy_parameters={"regime_period":200,"buffer_pct":0.5,"rearm_bars":0})),
     ("Daily 200-SMA swing", dict(strategy="DAILY_SWING_SMA200", allow_short=False,
        strategy_parameters={"sma_period":200,"buffer_pct":1.0,"max_extension_pct":5.0,
        "require_bullish_candle":True,"use_last_sell_filter":False,"exit_confirm_days":0})),
    ]


def main():
    n_beat = 0
    for sym in ("/CL","/GC","/NQ"):
        c = data.load(sym,"1d")
        print(f"\n  === {sym} 1d  ({len(c):,} bars, 26 years)   "
              f"buy & hold ${harness.run(sym,'1d',specs()[0][1],c)['bh']:,.0f} ===")
        for name, spec in specs():
            try:
                r = harness.run(sym,"1d",spec,c)
            except Exception as e:
                print(f"      {name:<30} FAILED {str(e)[:60]}"); continue
            i = board.add(batch="daily-library", symbol=sym, tf="1d", name=name,
                          spec=spec, result=r)
            n_beat += bool(r["beatsBH"])
            print(board.line(i, sym, "1d", name, r))
    print(f"\n  {n_beat} runs beat buy & hold")


if __name__ == "__main__":
    main()
