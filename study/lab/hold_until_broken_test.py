"""Does HOLD_UNTIL_BROKEN actually beat buy & hold, or is it another loser?

Tests the new family against the two datasets this session already measured
to death: the 14 semiconductor names and TARIL. Scored against BOTH benchmarks
now -- buy & hold to the last bar (the old, hard one) and buy & hold to its own
peak (the new one, which is what a well-timed exit is really competing with).

Ad-hoc research script (repo convention: study/lab/*.py).
"""
import json

from ftmo_mt5.lab import metrics, service, verdict, windows

SEMIS = ["AMAT", "AMD", "ARM", "CRDO", "DELL", "LITE", "MRVL", "MU",
         "NBIS", "NVDA", "SMH", "SNDK", "TSM", "WDC"]
SYMBOLS = SEMIS + ["TARIL.NS"]
TFS = ["1d", "1w"]
BARS = 600
NEW = ["HOLD_UNTIL_BROKEN", "BUY_AND_HOLD_BASELINE"]

defaults = service.strategy_defaults()
rows = []

hdr = f"{'SYM':<9}{'TF':<4}{'STRATEGY':<24}{'TRD':<5}{'RET%':<10}{'BH%':<10}{'ALPHA':<10}{'BHPEAK%':<10}{'vsPEAK':<10}{'BEAT?'}"
print(hdr)
print("-" * len(hdr))

for symbol in SYMBOLS:
    for tf in TFS:
        try:
            candles = windows.window(symbol, tf, BARS)["candles"]
            if not candles:
                continue
            size = service.size_for(symbol, candles, 100_000.0)
        except Exception as e:
            print(f"{symbol:<9}{tf:<4} SKIP {type(e).__name__}: {e}")
            continue

        for name in NEW:
            spec = {"strategy": name, "strategy_parameters": dict(defaults[name]),
                    "position_size": float(size),
                    "allow_long": True, "allow_short": False}
            try:
                r = metrics.measure(symbol, tf, spec, candles)
            except Exception as e:
                print(f"{symbol:<9}{tf:<4}{name:<24} ERROR {type(e).__name__}: {e}")
                continue
            rows.append(r)
            print(f"{symbol:<9}{tf:<4}{name:<24}{r['trades']:<5}{r['returnPct']:<10.1f}"
                  f"{r['bhReturnPct']:<10.1f}{r['vsBHPct']:<10.1f}"
                  f"{(r.get('bhPeakReturnPct') or 0):<10.1f}"
                  f"{(r.get('vsBHPeakPct') or 0):<10.1f}{r['beatsBH']}")

json.dump(rows, open("study/lab/hold_until_broken_results.json", "w"), indent=2, default=str)

hub = [r for r in rows if r["strategy"] == "HOLD_UNTIL_BROKEN"]
beats = [r for r in hub if r["beatsBH"]]
beats_peak = [r for r in hub if (r.get("vsBHPeakPct") or -1e9) > 0]
print(f"\nHOLD_UNTIL_BROKEN: {len(beats)}/{len(hub)} beat buy & hold (to last bar)")
print(f"HOLD_UNTIL_BROKEN: {len(beats_peak)}/{len(hub)} beat buy & hold (to its peak)")

print(f"\n=== validate() on all {len(beats)} that beat B&H ===\n")
for r in sorted(beats, key=lambda r: -(r["vsBHPct"] or 0)):
    candles = windows.window(r["symbol"], r["timeframe"], BARS)["candles"]
    size = service.size_for(r["symbol"], candles, 100_000.0)
    spec = {"strategy": "HOLD_UNTIL_BROKEN", "position_size": float(size),
            "allow_long": True, "allow_short": False}
    out = verdict.validate(r["symbol"], r["timeframe"], spec, candles, "sma_period")
    g, full, oos = out["gates"], out["full"], out["oos"]
    print(f"{r['symbol']:<9}{r['timeframe']:<4} verdict={out['verdict']:<9} "
          f"trades={full['trades']:<4} vsBH={full['vsBHPct']:+.1f}pp "
          f"oosBeats={g['beatsOutOfSample']} folds={g['foldsWon']}/{g['foldsRun']} "
          f"ticks={g['survivesTicks']} varHeld={g['variationsHeldPct']}")
    print(f"   why: {out['why']}")
