"""TARIL (Transformers and Rectifiers India Ltd, NSE) -- daily backtest,
April 2023 to now, every strategy the Lab has, then validate() on the best.

Ad-hoc research script (repo convention: study/lab/*.py). TARIL.NS is not one
of the 14 semiconductor names this session tested before -- first real test
of the Lab against an Indian (NSE) equity via Yahoo Finance.

Caveat worth stating up front: the Lab's cost model (COMMISSION=$2.50/side,
tick-based slippage) was calibrated for USD-denominated US/futures instruments.
TARIL trades in INR. The backtest engine has no currency awareness -- it will
apply that same numeric $2.50/side commission against INR prices, which is a
mismatched, not a correct, cost assumption. Flagged in the results, not hidden.
"""
import json
from datetime import date, datetime, timezone

from ftmo_mt5.lab import metrics, service, verdict, windows

SYMBOL = "TARIL.NS"
TIMEFRAME = "1d"
START = date(2023, 4, 1)

all_candles = windows.load(SYMBOL, TIMEFRAME)
start_ts = int(datetime(START.year, START.month, START.day, tzinfo=timezone.utc).timestamp())
candles = [c for c in all_candles if c.timestamp >= start_ts]

print(f"{SYMBOL} {TIMEFRAME}: {len(all_candles)} bars cached total, "
      f"{len(candles)} bars from {START} onward "
      f"({datetime.fromtimestamp(candles[0].timestamp, tz=timezone.utc).date()} to "
      f"{datetime.fromtimestamp(candles[-1].timestamp, tz=timezone.utc).date()})")

size = service.size_for(SYMBOL, candles, 100_000.0)
print(f"position size (normalized to ~100,000 notional in the price's own currency): {size} shares\n")

defaults = service.strategy_defaults()
rows = []
for name, params in defaults.items():
    spec = {"strategy": name, "strategy_parameters": dict(params),
            "position_size": float(size), "allow_long": True, "allow_short": True}
    try:
        r = metrics.measure(SYMBOL, TIMEFRAME, spec, candles)
    except Exception as e:
        rows.append({"strategy": name, "error": f"{type(e).__name__}: {e}"})
        continue
    rows.append(r)

rows_ok = [r for r in rows if "error" not in r]
rows_ok.sort(key=lambda r: -(r["vsBHPct"] or 0))

print(f"{'STRATEGY':<24}{'TRADES':<8}{'WIN%':<7}{'RET%':<9}{'BH%':<9}{'ALPHA(pp)':<11}{'SHARPE':<9}{'MAXDD%':<8}{'BEAT?'}")
for r in rows_ok:
    sh = f"{r['sharpe']:.2f}" if r["sharpe"] is not None else "-"
    print(f"{r['strategy']:<24}{r['trades']:<8}{r['winRate']:<7.0f}{r['returnPct']:<9.1f}"
          f"{r['bhReturnPct']:<9.1f}{r['vsBHPct']:<11.1f}{sh:<9}{r['maxDDPct']:<8.1f}{r['beatsBH']}")

for r in rows:
    if "error" in r:
        print(f"{r['strategy']:<24} ERROR: {r['error']}")

json.dump(rows, open("study/lab/taril_matrix_results.json", "w"), indent=2, default=str)

# --- validate() on the candidates worth a real look ---
candidates = [r for r in rows_ok if r["beatsBH"] and r["trades"] >= 5][:5]
print(f"\n=== validate() on top {len(candidates)} candidates (beatsBH, >=5 trades) ===\n")
val_out = []
for r in candidates:
    spec = {"strategy": r["strategy"], "allow_long": True, "allow_short": True}
    out = verdict.validate(SYMBOL, TIMEFRAME, spec, candles, None)
    full, oos, gates = out["full"], out["oos"], out["gates"]
    print(f"{r['strategy']:<24} verdict={out['verdict']:<9} trades={full['trades']:<4} "
          f"vsBH={full['vsBHPct']:+.1f}pp  oosBeats={gates['beatsOutOfSample']}  "
          f"oosVsBH={oos['vsBHPct']:+.1f}pp  ticks={gates['survivesTicks']}  "
          f"top3={gates['survivesTop3Removal']}  top5={gates['survivesTop5Removal']}  "
          f"folds={gates['foldsWon']}/{gates['foldsRun']}  varHeld={gates['variationsHeldPct']}")
    print(f"   why: {out['why']}")
    val_out.append({"strategy": r["strategy"], "verdict": out["verdict"], "gates": gates,
                    "full": full, "oos": oos, "why": out["why"]})

json.dump(val_out, open("study/lab/taril_validate_results.json", "w"), indent=2, default=str)
print("\nDONE")
