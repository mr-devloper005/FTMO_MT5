"""The closing question: is a rising market beatable at all with one contract?

The best long/flat rules capture most of a bull market but not all of it, and
the gap is the cost of being out during the pullbacks they sidestep. This batch
measures that gap directly, then asks what it takes to close it.

Risk-based sizing is included not as a strategy but as the control: if the only
thing that closes the gap is holding more than one contract, then what looks
like an edge is leverage, and the report has to say so.
"""
import json
import study.lab.strats
from study.lab import harness, board, data

BASE = dict(strategy="TREND_REGIME", allow_short=False,
            strategy_parameters={"regime_period": 200, "buffer_pct": 0.5})

if __name__ == "__main__":
    print("  How much of a rising market can a long/flat filter actually keep?\n")
    print(f"  {'market':<10}{'B&H':>11}{'strategy':>11}{'captured':>10}{'DD%':>7}"
          f"{'B&H DD%':>9}{'TiM%':>7}")
    for sym, tf in (("/GC", "4h"), ("/GC", "1h"), ("/NQ", "4h"), ("/NQ", "1h"), ("/CL", "1h")):
        r = harness.run(sym, tf, BASE)
        cap = r["net"] / r["bh"] * 100 if r["bh"] else float("nan")
        print(f"  {sym+' '+tf:<10}{r['bh']:>11,.0f}{r['net']:>11,.0f}{cap:>9.0f}%"
              f"{abs(r['maxDDPct']):>7.1f}{abs(r['bhDDPct']):>9.1f}{r['timeInMarket']:>7.1f}")

    print("\n  Same rule, sized by risk instead of one fixed contract"
          "\n  (this is leverage, not a better rule - shown so the difference is visible)\n")
    print(f"  {'market':<10}{'sizing':<12}{'net':>11}{'B&H':>11}{'vs B&H':>11}"
          f"{'PF':>6}{'DD%':>7}")
    for sym, tf in (("/GC", "4h"), ("/NQ", "4h"), ("/CL", "1h")):
        c = data.load(sym, tf)
        for label, extra in (("1 contract", {}),
                             ("risk 1%", {"risk_per_trade_pct": 1.0, "stop_loss_atr": 3.0}),
                             ("risk 2%", {"risk_per_trade_pct": 2.0, "stop_loss_atr": 3.0})):
            s = json.loads(json.dumps(BASE)); s.update(extra)
            r = harness.run(sym, tf, s, c)
            board.add(batch="sizing", symbol=sym, tf=tf,
                      name=f"Regime +0.5% band, {label}", spec=s, result=r)
            print(f"  {sym+' '+tf:<10}{label:<12}{r['net']:>11,.0f}{r['bh']:>11,.0f}"
                  f"{r['vsBH']:>11,.0f}{(r['pf'] or 0):>6.2f}{abs(r['maxDDPct']):>7.1f}"
                  + ("  BEAT" if r["beatsBH"] else ""))
