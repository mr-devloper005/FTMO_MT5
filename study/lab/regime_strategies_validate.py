"""EMA_REGIME_LS and FOUR_REGIME, every timeframe, all 14 semiconductor names,
through validate() -- the two strategies predict.py picked (for ARM and NVDA
respectively) that hadn't been through the Lab's robustness engine yet.

Ad-hoc research script (repo convention: study/lab/*.py). Same methodology as
mean_reversion_validate.py: service.validate() via the Lab's own
ftmo_mt5.lab.verdict engine, bars=600, position sized to ~$100k notional.
"""
import json, time

from ftmo_mt5.lab import service, windows

SYMBOLS = ["AMAT", "AMD", "ARM", "CRDO", "DELL", "LITE", "MRVL", "MU",
           "NBIS", "NVDA", "SMH", "SNDK", "TSM", "WDC"]
TFS = windows.TIMEFRAMES
BARS = 600
STRATEGIES = ["EMA_REGIME_LS", "FOUR_REGIME"]

rows, started = [], time.time()
for strategy in STRATEGIES:
    spec = {"strategy": strategy, "allow_long": True, "allow_short": True}
    for symbol in SYMBOLS:
        for tf in TFS:
            try:
                out = service.validate(symbol, tf, dict(spec), BARS, None, size_to_capital=True)
            except Exception as e:
                rows.append({"strategy": strategy, "symbol": symbol, "timeframe": tf,
                             "error": f"{type(e).__name__}: {e}"})
                print(f"{strategy:<15}{symbol:<6}{tf:<5} ERROR {type(e).__name__}: {e}", flush=True)
                continue
            full, oos, gates = out["full"], out["oos"], out["gates"]
            row = {
                "strategy": strategy, "symbol": symbol, "timeframe": tf,
                "verdict": out["verdict"],
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
            print(f"{strategy:<15}{symbol:<6}{tf:<5} {out['verdict']:<9} trades={full['trades']:<4} "
                  f"vsBH={full['vsBHPct']:+.1f}pp  oosBeats={gates['beatsOutOfSample']}  "
                  f"[{len(rows)}/{len(STRATEGIES)*len(SYMBOLS)*len(TFS)}, {time.time()-started:.1f}s]",
                  flush=True)

json.dump(rows, open("study/lab/regime_strategies_validate_results.json", "w"), indent=2)
print(f"\nDONE {len(rows)} validations in {time.time()-started:.1f}s")
