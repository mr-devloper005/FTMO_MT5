"""Walk-forward optimisation — variations chosen without seeing the answer.

For every market and timeframe, a small grid of parameter variations is run on
the first 70% of the history. The single best variation there is locked and run
once on the last 30%, which was never touched while choosing.

Only the out-of-sample column is a result. The development column is shown so
the gap between "what looked best" and "what actually happened" is visible,
because that gap is the whole reason this procedure exists.

Grids are deliberately small — six to nine values per family. A grid large
enough to guarantee a winner on the development half guarantees nothing on the
other half.
"""
import json
from pathlib import Path
import study.lab.strats
from study.lab import harness, data

TFS = ("1h", "2h", "4h", "6h", "8h", "12h", "1d")
MIN = {"1h":60,"2h":120,"4h":240,"6h":360,"8h":480,"12h":720,"1d":1440}


def families(tf):
    h = MIN[tf] * 4
    return {
     "EMA crossover + RSI": [
       (f"EMA {a}/{b}, {d}", dict(strategy="EMA_RSI_PATTERN", allow_short=(d=="L/S"),
        stop_loss_atr=2.0, take_profit_atr=3.0,
        strategy_parameters={"fast_period":a,"slow_period":b,"rsi_buy_above":50.0,
                             "rsi_sell_below":50.0,"require_pattern":True}))
       for a,b in ((10,30),(20,50),(30,80)) for d in ("L/S","long")],

     "Trend regime": [
       (f"SMA {p}, band {q}%, {d}", dict(strategy="TREND_REGIME",
        allow_short=(d=="L/S"), strategy_parameters={"regime_period":p,"buffer_pct":q}))
       for p in (100,200,300) for q in (0.0,0.5,1.0) for d in ("L/S","long")],

     "MACD + ADX": [
       (f"ADX in {m} out {x}", dict(strategy="MACD_ADX", allow_short=True,
        strategy_parameters={"adx_min":m,"adx_exit":x,"trade_shorts":True}))
       for m in (20.0,25.0,30.0) for x in (10.0,15.0,20.0)],

     "Donchian breakout": [
       (f"{n}/{x}, {d}", dict(strategy="DONCHIAN_BREAKOUT", allow_short=(d=="L/S"),
        strategy_parameters={"entry_lookback":n,"exit_lookback":x}))
       for n,x in ((20,10),(55,20),(100,50)) for d in ("L/S","long")],

     "MTF trend stack": [
       (f"legs x{k}", dict(strategy="MTF_GREEN_STACK", allow_short=True,
        trailing_stop_atr=t, strategy_parameters={"tf1_minutes":MIN[tf],
        "tf2_minutes":MIN[tf]*2,"tf3_minutes":MIN[tf]*k,"require_completed":True,
        "trade_shorts":True,"exit_on_condition":True}))
       for k in (4,6,8) for t in (1.5,2.0,3.0)],
    }


def main():
    rows = []
    print("  Walk-forward optimisation: tuned on the first 70%, judged on the last 30%\n")
    print(f"  {'cell':<9}{'family':<22}{'chosen on dev':<24}{'dev vs B&H':>12}"
          f"{'OOS vs B&H':>12}{'OOS tr':>8}{'OOS PF':>8}")
    for sym in ("/NQ", "/GC", "/CL"):
        for tf in TFS:
            c = data.load(sym, tf)
            cut = int(len(c) * 0.70)
            dev, oos = c[:cut], c[cut:]
            for fam, grid in families(tf).items():
                best, best_v = None, None
                for label, spec in grid:
                    try:
                        r = harness.run(sym, tf, spec, dev)
                    except Exception:
                        continue
                    if best_v is None or r["vsBH"] > best_v["vsBH"]:
                        best, best_v = (label, spec), r
                if best is None:
                    continue
                o = harness.run(sym, tf, best[1], oos)     # locked, unseen
                rows.append(dict(symbol=sym, tf=tf, family=fam, chosen=best[0],
                                 devVsBH=best_v["vsBH"], devBeat=best_v["beatsBH"],
                                 oosVsBH=o["vsBH"], oosBeat=o["beatsBH"],
                                 oosTrades=o["trades"], oosPF=o["pf"], oosNet=o["net"],
                                 oosBH=o["bh"], oosDD=o["maxDDPct"],
                                 oosTop1=o["topTradeShare"], gridSize=len(grid)))
                print(f"  {sym+' '+tf:<9}{fam:<22}{best[0][:23]:<24}"
                      f"{best_v['vsBH']:>12,.0f}{o['vsBH']:>12,.0f}{o['trades']:>8}"
                      f"{(o['pf'] or 0):>8.2f}" + ("  BEAT" if o["oosBeat"] if False
                                                   else "  BEAT" if o["beatsBH"] else ""),
                      flush=True)
    Path("study/lab/walkopt.json").write_text(json.dumps(rows, indent=1))
    dv = sum(1 for r in rows if r["devBeat"]); oo = sum(1 for r in rows if r["oosBeat"])
    print(f"\n  {len(rows)} optimisations · {dv} looked like winners on the "
          f"development half · {oo} still beat buy & hold on unseen data")


if __name__ == "__main__":
    main()
