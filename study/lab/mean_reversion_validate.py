"""MEAN_REVERSION, every timeframe, all 14 semiconductor names, through validate().

Ad-hoc research script (repo convention: study/lab/*.py). Reuses the Lab's own
robustness engine (ftmo_mt5.lab.verdict, via service.validate()) instead of
re-implementing anything. Position sizing matches sweep_equities.py's
convention (~$100k notional per cell) via size_to_capital=True so buy & hold
stays a real opponent on cheap names.
"""
import json, time

from ftmo_mt5.lab import service, windows

SYMBOLS = ["AMAT", "AMD", "ARM", "CRDO", "DELL", "LITE", "MRVL", "MU",
           "NBIS", "NVDA", "SMH", "SNDK", "TSM", "WDC"]
TFS = windows.TIMEFRAMES
BARS = 600

spec = {"strategy": "MEAN_REVERSION", "allow_long": True, "allow_short": True}

rows, started = [], time.time()
for symbol in SYMBOLS:
    for tf in TFS:
        try:
            out = service.validate(symbol, tf, dict(spec), BARS, None, size_to_capital=True)
        except Exception as e:
            rows.append({"symbol": symbol, "timeframe": tf,
                         "error": f"{type(e).__name__}: {e}"})
            print(f"{symbol:<6}{tf:<5} ERROR {type(e).__name__}: {e}", flush=True)
            continue
        full, oos, gates = out["full"], out["oos"], out["gates"]
        row = {
            "symbol": symbol, "timeframe": tf, "verdict": out["verdict"],
            "trades": full["trades"], "winRate": full["winRate"],
            "returnPct": full["returnPct"], "bhReturnPct": full["bhReturnPct"],
            "vsBHPct": full["vsBHPct"], "sharpe": full["sharpe"],
            "maxDDPct": full["maxDDPct"], "beatsBH": full["beatsBH"],
            "beatsOOS": gates["beatsOutOfSample"], "oosVsBHPct": oos["vsBHPct"],
            "oosTrades": oos["trades"], "survivesTicks": gates["survivesTicks"],
            "survivesTop3": gates["survivesTop3Removal"],
            "survivesTop5": gates["survivesTop5Removal"],
            "foldsWon": gates["foldsWon"], "foldsRun": gates["foldsRun"],
            "variationsHeldPct": gates["variationsHeldPct"],
            "topTradeShare": full["topTradeShare"], "bh": full["bh"],
            "why": out["why"],
        }
        rows.append(row)
        print(f"{symbol:<6}{tf:<5} {out['verdict']:<9} trades={full['trades']:<4} "
              f"vsBH={full['vsBHPct']:+.1f}pp  oosBeats={gates['beatsOutOfSample']}  "
              f"[{len(rows)}/{len(SYMBOLS)*len(TFS)}, {time.time()-started:.1f}s]",
              flush=True)

json.dump(rows, open("study/lab/mean_reversion_validate_results.json", "w"), indent=2)
print(f"\nDONE {len(rows)} validations in {time.time()-started:.1f}s")
