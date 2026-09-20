"""Gate the only two 200-SMA swing runs with enough trades to test."""
import json
from pathlib import Path
import study.lab.strats
from study.lab import vet

SPEC = dict(strategy="DAILY_SWING_SMA200", allow_short=False,
            strategy_parameters={"sma_period":200,"buffer_pct":1.0,
                                 "max_extension_pct":5.0,"require_bullish_candle":True,
                                 "use_last_sell_filter":False,"chase_pct":2.0,
                                 "exit_confirm_days":0})
V = [("buffer 0.5%",{"buffer_pct":0.5}),("buffer 1.5%",{"buffer_pct":1.5}),
     ("buffer 2%",{"buffer_pct":2.0}),("no-chase 3%",{"max_extension_pct":3.0}),
     ("no-chase 8%",{"max_extension_pct":8.0}),
     ("no-chase off",{"max_extension_pct":100000.0}),
     ("SMA 150",{"sma_period":150}),("SMA 250",{"sma_period":250}),
     ("no up-candle",{"require_bullish_candle":False}),
     ("E switched on",{"use_last_sell_filter":True})]

if __name__ == "__main__":
    store, summ = {}, []
    for sym, tf in (("/CL","1h"), ("/CL","1d")):
        v = vet.vet(sym, tf, "200-SMA swing (E off)", SPEC, V)
        store[f"{sym}{tf}"] = v
        print(vet.report(v))
        s=v["split"]; th=sum(1 for x in v["thirds"] if x["beatsBH"])
        se=sum(1 for x in v["sensitivity"] if x["result"]["beatsBH"])
        co=[int(k) for k,x in v["costs"].items() if x["beatsBH"]]
        summ.append((f"{sym} {tf}", s, th, se, len(v["sensitivity"]), max(co) if co else 0))
    Path("study/lab/vet_megha.json").write_text(json.dumps(store, indent=1))
    print("\n\n  ==================== GATE SUMMARY ====================\n")
    print(f"  {'cell':<9}{'full':>10}{'dev':>10}{'oos':>11}{'OOS PF':>8}"
          f"{'thirds':>8}{'params':>9}{'cost':>7}  verdict")
    for cell, s, th, se, sen, co in summ:
        ok = s["oos"]["beatsBH"] and th>=2
        print(f"  {cell:<9}{s['full']['vsBH']:>10,.0f}{s['dev']['vsBH']:>10,.0f}"
              f"{s['oos']['vsBH']:>11,.0f}{(s['oos']['pf'] or 0):>8.2f}{f'{th}/3':>8}"
              f"{f'{se}/{sen}':>9}{f'{co}t' if co else 'fails':>7}  "
              f"{'SURVIVED' if ok else 'FAILED OUT-OF-SAMPLE'}")
