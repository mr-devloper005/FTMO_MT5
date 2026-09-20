"""Does the four-regime rule survive on all five timeframes it won on?

Winning on five consecutive timeframes of one market is the closest thing this
study has found to a rule that travels. This tests whether that survives unseen
data, or whether the five wins are five small samples agreeing by accident.
"""
import json
from pathlib import Path
import study.lab.strats
from study.lab import vet, harness, data

MIN = {"2h":120,"4h":240,"6h":360,"8h":480,"12h":720}

def spec(tf):
    return dict(strategy="FOUR_REGIME", allow_short=False, trailing_stop_atr=2.0,
                strategy_parameters={"trend_minutes": MIN[tf]*4})

VAR = [("trail 1.5 ATR",{}), ("trail 3 ATR",{}), ("trend x2",{}), ("trend x8",{})]

if __name__ == "__main__":
    store, summ = {}, []
    for tf in ("2h","4h","6h","8h","12h"):
        s = spec(tf)
        v = {"split": harness.split("/CL", tf, s)}
        c = data.load("/CL", tf); n = len(c)//3
        v["thirds"] = [harness.run("/CL", tf, s, c[i*n:(i+1)*n]) for i in range(3)]
        # Sensitivity that actually varies something: the trailing stop and the
        # context timeframe are the only two numbers this rule exposes.
        v["sens"] = []
        for lbl, t, k in (("trail 1.5 ATR",1.5,4), ("trail 3 ATR",3.0,4),
                          ("context x2",2.0,2), ("context x8",2.0,8)):
            sp = dict(strategy="FOUR_REGIME", allow_short=False, trailing_stop_atr=t,
                      strategy_parameters={"trend_minutes": MIN[tf]*k})
            v["sens"].append((lbl, harness.run("/CL", tf, sp, c)))
        store[tf] = {"split": v["split"], "thirds": v["thirds"],
                     "sens": [(l, r) for l, r in v["sens"]]}
        summ.append((tf, v))

    print("  Four-regime breakout on /CL — the five timeframes it won on\n")
    print(f"  {'tf':<5}{'window':<7}{'tr':>5}{'net':>11}{'B&H':>10}{'vs B&H':>11}"
          f"{'PF':>6}{'DD%':>7}{'TiM%':>7}")
    for tf, v in summ:
        for w in ("full","dev","oos"):
            r = v["split"][w]
            print(f"  {tf:<5}{w:<7}{r['trades']:>5}{r['net']:>11,.0f}{r['bh']:>10,.0f}"
                  f"{r['vsBH']:>11,.0f}{(r['pf'] or 0):>6.2f}{abs(r['maxDDPct']):>7.1f}"
                  f"{r['timeInMarket']:>7.1f}" + ("  BEAT" if r["beatsBH"] else ""))
        print()

    print("\n  SUMMARY\n")
    print(f"  {'tf':<5}{'full':>10}{'dev':>10}{'oos':>10}{'OOS tr':>8}{'thirds':>8}"
          f"{'params':>8}{'TiM%':>7}  verdict")
    for tf, v in summ:
        s = v["split"]; th = sum(1 for x in v["thirds"] if x["beatsBH"])
        se = sum(1 for _, r in store[tf]["sens"] if r["beatsBH"])
        o = s["oos"]
        verdict = ("SURVIVED" if o["beatsBH"] and th >= 2 else
                   "too few OOS trades to judge" if o["trades"] < 5 else
                   "FAILED OUT-OF-SAMPLE" if not o["beatsBH"] else "PARTIAL")
        print(f"  {tf:<5}{s['full']['vsBH']:>10,.0f}{s['dev']['vsBH']:>10,.0f}"
              f"{o['vsBH']:>10,.0f}{o['trades']:>8}{f'{th}/3':>8}{f'{se}/4':>8}"
              f"{s['full']['timeInMarket']:>7.1f}  {verdict}")
    Path("study/lab/vet_fourregime.json").write_text(json.dumps(
        {k: {"split": v["split"], "thirds": v["thirds"]} for k, v in store.items()}, indent=1))
