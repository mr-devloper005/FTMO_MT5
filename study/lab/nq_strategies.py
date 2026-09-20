"""Many strategies, each held fixed, across every /NQ timeframe.

Every strategy below uses one parameter set for all ten timeframes. Nothing is
retuned per row. The six rules that need a higher timeframe to read a trend from
are given four times their own bar size everywhere, which is a fixed rule rather
than a per-timeframe choice, and they are marked so that is visible.
"""
import json
from pathlib import Path
import study.lab.strats
from study.lab import harness, data

TFS = ("5m","15m","30m","1h","2h","4h","6h","8h","12h","1d")
MIN = {"5m":5,"15m":15,"30m":30,"1h":60,"2h":120,"4h":240,"6h":360,"8h":480,
       "12h":720,"1d":1440}


def strategies(tf):
    m, h = MIN[tf], MIN[tf] * 4
    return [
     ("Trend regime SMA200", False, dict(strategy="TREND_REGIME", allow_short=False,
        strategy_parameters={"regime_period":200,"buffer_pct":0.0})),
     ("Trend regime + 0.5% band", False, dict(strategy="TREND_REGIME", allow_short=False,
        strategy_parameters={"regime_period":200,"buffer_pct":0.5})),
     ("EMA100 regime, long", False, dict(strategy="EMA_REGIME_LS", allow_short=False,
        strategy_parameters={"period":100,"trade_shorts":False})),
     ("EMA100 regime, long+short", False, dict(strategy="EMA_REGIME_LS", allow_short=True,
        strategy_parameters={"period":100,"trade_shorts":True})),
     ("Donchian 55/20 long", False, dict(strategy="DONCHIAN_BREAKOUT", allow_short=False,
        strategy_parameters={"entry_lookback":55,"exit_lookback":20})),
     ("Turtle 20/10 long+short", False, dict(strategy="TURTLE_LS", allow_short=True,
        stop_loss_atr=2.0, strategy_parameters={"entry_lookback":20,"exit_lookback":10,
        "trade_shorts":True})),
     ("EMA 20/50 + RSI, long", False, dict(strategy="EMA_RSI_PATTERN", allow_short=False,
        stop_loss_atr=2.0, take_profit_atr=3.0, strategy_parameters={"fast_period":20,
        "slow_period":50,"rsi_buy_above":50.0,"rsi_sell_below":50.0,"require_pattern":True})),
     ("EMA 20/50 + RSI, L/S", False, dict(strategy="EMA_RSI_PATTERN", allow_short=True,
        stop_loss_atr=2.0, take_profit_atr=3.0, strategy_parameters={"fast_period":20,
        "slow_period":50,"rsi_buy_above":50.0,"rsi_sell_below":50.0,"require_pattern":True})),
     ("Keltner ride, long", False, dict(strategy="KELTNER_RIDE", allow_short=False,
        strategy_parameters={"ema_period":20,"entry_atr":1.0,"trend_period":200,
        "trade_shorts":False})),
     ("Volatility squeeze breakout", False, dict(strategy="SQUEEZE_BREAKOUT",
        allow_short=True, trailing_stop_atr=2.5, strategy_parameters={"bb_period":20,
        "squeeze_pct":0.25,"rank_window":250,"range_lookback":20,"trend_period":100,
        "trade_shorts":True})),
     ("MACD + ADX", False, dict(strategy="MACD_ADX", allow_short=True,
        strategy_parameters={"adx_min":25.0,"adx_exit":15.0,"trade_shorts":True})),
     ("ADX regime switch", False, dict(strategy="ADX_SWITCH", allow_short=True,
        stop_loss_atr=3.0, strategy_parameters={"trend_above":25.0,"range_below":20.0,
        "entry_z":2.0,"exit_z":0.3,"trade_shorts":True})),
     ("Mean reversion 2.5 sigma", False, dict(strategy="MEAN_REVERSION", allow_short=True,
        stop_loss_atr=3.0, strategy_parameters={"period":100,"entry_z":2.5,"exit_z":0.5,
        "trade_shorts":True})),
     ("200-SMA swing (buffered)", False, dict(strategy="DAILY_SWING_SMA200",
        allow_short=False, strategy_parameters={"sma_period":200,"buffer_pct":1.0,
        "max_extension_pct":5.0,"require_bullish_candle":True,
        "use_last_sell_filter":False,"exit_confirm_days":0,"trade_shorts":False})),
     ("Four-regime switcher", True, dict(strategy="FOUR_REGIME", allow_short=False,
        trailing_stop_atr=2.0, strategy_parameters={"trend_minutes":h})),
     ("MTF trend stack", True, dict(strategy="MTF_GREEN_STACK", allow_short=True,
        trailing_stop_atr=2.0, strategy_parameters={"tf1_minutes":m,"tf2_minutes":m*2,
        "tf3_minutes":m*4,"require_completed":True,"trade_shorts":True,
        "exit_on_condition":True})),
     ("Dual regime, 2 timeframes", True, dict(strategy="DUAL_REGIME", allow_short=False,
        strategy_parameters={"period":200,"buffer_pct":0.5,"higher_minutes":h,
        "higher_period":100,"trade_shorts":False})),
    ]


def main():
    rows = []
    for tf in TFS:
        c = data.load("/NQ", tf); cut = int(len(c) * 0.70)
        for name, tfdep, spec in strategies(tf):
            try:
                f = harness.run("/NQ", tf, spec, c)
                o = harness.run("/NQ", tf, spec, c[cut:])
            except Exception as e:
                continue
            rows.append(dict(tf=tf, name=name, tfDependent=tfdep,
                fullNet=f["net"], bh=f["bh"], fullVsBH=f["vsBH"], fullBeat=f["beatsBH"],
                trades=f["trades"], pf=f["pf"], dd=f["maxDDPct"], bhdd=f["bhDDPct"],
                win=f["winRate"], tim=f["timeInMarket"], top1=f["topTradeShare"],
                oosNet=o["net"], oosBH=o["bh"], oosVsBH=o["vsBH"], oosBeat=o["beatsBH"],
                oosTrades=o["trades"], oosPF=o["pf"]))
        print(f"  {tf} done ({len(c):,} bars, B&H ${rows[-1]['bh']:,.0f})", flush=True)
    Path("study/lab/nq_strategies.json").write_text(json.dumps(rows, indent=1))
    real = [r for r in rows if r["bh"] > 1000]
    print(f"\n  {len(rows)} runs · {len(real)} against a real benchmark")
    print(f"  full history beats : {sum(1 for r in real if r['fullBeat'])}")
    print(f"  out-of-sample beats: {sum(1 for r in real if r['oosBeat'])}")


if __name__ == "__main__":
    main()
