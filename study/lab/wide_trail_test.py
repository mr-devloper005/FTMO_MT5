"""A WIDE ATR trailing stop instead of the SMA exit.

The SMA200 exit failed for a diagnosable reason: it fired during ordinary
drawdowns inside intact uptrends, sold low, and bought back higher. A trailing
stop wide enough to sit outside normal volatility should not do that -- it
should ignore the noise and only trigger on a genuine rollover.

Earlier this session, trailing stops at 2 and 3 ATR were tested on the ACTIVE
strategies and lost. This is a different experiment: the base is a pure hold,
the engine's own trailing stop is the only exit, re-entry requires a new high
(otherwise the next bar buys straight back in and the stop does nothing), and
the multiples tested go far wider -- out to 20 ATR.

Ad-hoc research script (repo convention: study/lab/*.py).
"""
import json

from ftmo_mt5.lab import metrics, service, verdict, windows

SEMIS = ["AMAT", "AMD", "ARM", "CRDO", "DELL", "LITE", "MRVL", "MU",
         "NBIS", "NVDA", "SMH", "SNDK", "TSM", "WDC"]
SYMBOLS = SEMIS + ["TARIL.NS"]
TFS = ["1d", "1w"]
BARS = 600
TRAILS = [3.0, 5.0, 8.0, 12.0, 20.0]

rows = []
for symbol in SYMBOLS:
    for tf in TFS:
        try:
            candles = windows.window(symbol, tf, BARS)["candles"]
            if not candles:
                continue
            size = service.size_for(symbol, candles, 100_000.0)
        except Exception as e:
            print(f"{symbol} {tf} SKIP {type(e).__name__}: {e}")
            continue

        for trail in TRAILS:
            spec = {
                "strategy": "HOLD_UNTIL_BROKEN",
                "strategy_parameters": {"use_trailing_exit": True,
                                        "reentry_high_lookback": 20},
                "position_size": float(size),
                "allow_long": True, "allow_short": False,
                "trailing_stop_atr": trail,
            }
            try:
                r = metrics.measure(symbol, tf, spec, candles)
            except Exception as e:
                print(f"{symbol} {tf} atr={trail} ERROR {type(e).__name__}: {e}")
                continue
            r["trail"] = trail
            rows.append(r)

json.dump(rows, open("study/lab/wide_trail_results.json", "w"), indent=2, default=str)

beats = [r for r in rows if r["beatsBH"]]
beats_peak = [r for r in rows if (r.get("vsBHPeakPct") or -1e9) > 0]
print(f"{len(rows)} runs  |  beat B&H(last bar): {len(beats)}  "
      f"|  beat B&H(peak): {len(beats_peak)}\n")

print("=== best 18 by alpha vs buy & hold ===")
hdr = f"{'SYM':<9}{'TF':<4}{'ATR':<6}{'TRD':<5}{'RET%':<11}{'BH%':<11}{'ALPHA':<11}{'BHPEAK%':<11}{'vsPEAK':<10}{'BEAT?'}"
print(hdr); print("-" * len(hdr))
for r in sorted(rows, key=lambda r: -(r["vsBHPct"] or -1e18))[:18]:
    print(f"{r['symbol']:<9}{r['timeframe']:<4}{r['trail']:<6}{r['trades']:<5}"
          f"{r['returnPct']:<11.1f}{r['bhReturnPct']:<11.1f}{r['vsBHPct']:<11.1f}"
          f"{(r.get('bhPeakReturnPct') or 0):<11.1f}{(r.get('vsBHPeakPct') or 0):<10.1f}"
          f"{r['beatsBH']}")

# Does a wider stop help monotonically? Average alpha per multiple.
print("\n=== average alpha by ATR multiple (is wider better?) ===")
for t in TRAILS:
    sub = [r for r in rows if r["trail"] == t]
    if sub:
        avg = sum(r["vsBHPct"] for r in sub) / len(sub)
        avg_trd = sum(r["trades"] for r in sub) / len(sub)
        won = sum(1 for r in sub if r["beatsBH"])
        print(f"  {t:>5} ATR:  avg alpha {avg:+9.1f}pp   avg trades {avg_trd:4.1f}   "
              f"beat B&H {won}/{len(sub)}")

print(f"\n=== validate() on all {len(beats)} that beat B&H ===\n")
for r in sorted(beats, key=lambda r: -(r["vsBHPct"] or 0)):
    candles = windows.window(r["symbol"], r["timeframe"], BARS)["candles"]
    size = service.size_for(r["symbol"], candles, 100_000.0)
    spec = {"strategy": "HOLD_UNTIL_BROKEN",
            "strategy_parameters": {"use_trailing_exit": True, "reentry_high_lookback": 20},
            "position_size": float(size), "allow_long": True, "allow_short": False,
            "trailing_stop_atr": r["trail"]}
    out = verdict.validate(r["symbol"], r["timeframe"], spec, candles,
                           "reentry_high_lookback")
    g, full, oos = out["gates"], out["full"], out["oos"]
    print(f"{r['symbol']:<9}{r['timeframe']:<4}atr={r['trail']:<5} "
          f"verdict={out['verdict']:<9} trades={full['trades']:<4} "
          f"vsBH={full['vsBHPct']:+.1f}pp oosBeats={g['beatsOutOfSample']} "
          f"folds={g['foldsWon']}/{g['foldsRun']} ticks={g['survivesTicks']} "
          f"varHeld={g['variationsHeldPct']}")
    print(f"   why: {out['why']}")
