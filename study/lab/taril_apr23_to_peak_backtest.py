"""TARIL, April 1 2023 -> January 3 2025 (the peak-close date found last
message), instead of April 2023 -> now. Same full grid as before: 1D-10D
bars, all 14 strategies, no trailing stop AND ATR trailing stop (2.0/3.0),
then validate() on anything that beats B&H, no trade-count minimum.

Ad-hoc research script (repo convention: study/lab/*.py). No file in
ftmo_mt5/ is modified.
"""
import json
from datetime import date, datetime, timezone

from ftmo_mt5.lab import metrics, service, verdict, windows
from ftmo_mt5.marketdata.base import Candle

SYMBOL = "TARIL.NS"
START = date(2023, 4, 1)
END = date(2025, 1, 3)          # the peak-close date
NDAYS = [1, 2, 3, 5, 8, 10]
TRAILS = [None, 2.0, 3.0]       # None = no trailing stop, for comparison

all_candles = windows.load(SYMBOL, "1d")
start_ts = int(datetime(START.year, START.month, START.day, tzinfo=timezone.utc).timestamp())
end_ts = int(datetime(END.year, END.month, END.day, 23, 59, 59, tzinfo=timezone.utc).timestamp())
daily = [c for c in all_candles if start_ts <= c.timestamp <= end_ts]
print(f"{SYMBOL}: {len(daily)} daily bars, {START} to {END} "
      f"({datetime.fromtimestamp(daily[0].timestamp, tz=timezone.utc).date()} to "
      f"{datetime.fromtimestamp(daily[-1].timestamp, tz=timezone.utc).date()})")
print(f"Start close={daily[0].close:.2f}  End close={daily[-1].close:.2f}  "
      f"Raw B&H multiple={daily[-1].close/daily[0].close:.2f}x\n")


def n_day_bars(candles, n):
    if n == 1:
        return list(candles)
    out = []
    for i in range(0, len(candles), n):
        chunk = candles[i:i + n]
        if len(chunk) < n:
            break
        out.append(Candle(
            timestamp=chunk[0].timestamp, open=chunk[0].open,
            high=max(c.high for c in chunk), low=min(c.low for c in chunk),
            close=chunk[-1].close, volume=sum(c.volume or 0 for c in chunk),
        ))
    return out


TF_LABELS = {n: (f"{n}D" if n > 1 else "1D") for n in NDAYS}
for n, label in TF_LABELS.items():
    windows.BARS_PER_YEAR[label] = 252.0 / n
    windows.ENGINE_TF[label] = "1d"

defaults = service.strategy_defaults()
label_to_bars = {TF_LABELS[n]: n_day_bars(daily, n) for n in NDAYS}
all_rows = []

for label, bars in label_to_bars.items():
    if len(bars) < 10:
        print(f"{label}: only {len(bars)} bars, too few to backtest meaningfully -- skipped")
        continue
    size = service.size_for(SYMBOL, bars, 100_000.0)
    for trail in TRAILS:
        for name, params in defaults.items():
            spec = {"strategy": name, "strategy_parameters": dict(params),
                    "position_size": float(size), "allow_long": True, "allow_short": True}
            if trail is not None:
                spec["trailing_stop_atr"] = trail
            try:
                r = metrics.measure(SYMBOL, label, spec, bars)
            except Exception as e:
                print(f"{label:<5}trail={trail}{name:<24} ERROR {type(e).__name__}: {e}")
                continue
            r["timeframe_label"] = label
            r["trailing_stop_atr"] = trail
            all_rows.append(r)
    beat_here = sum(1 for r in all_rows if r["timeframe_label"] == label and r["beatsBH"])
    total_here = len(defaults) * len(TRAILS)
    print(f"{label}: {len(bars)} bars, {beat_here} of {total_here} (strategy x trail-setting) combos beat B&H")

json.dump(all_rows, open("study/lab/taril_apr23_to_peak_matrix_results.json", "w"), indent=2, default=str)

ranked = sorted(all_rows, key=lambda r: -(r["vsBHPct"] if r["vsBHPct"] is not None else -1e18))
print(f"\n=== TOP 15 BY ALPHA, Apr 2023 -> Jan 3 2025, no trade-count filter ===")
print(f"{'TF':<5}{'TRAIL':<6}{'STRATEGY':<24}{'TRADES':<8}{'RET%':<10}{'BH%':<10}{'ALPHA(pp)':<11}{'BEAT?'}")
for r in ranked[:15]:
    trail_s = str(r["trailing_stop_atr"]) if r["trailing_stop_atr"] is not None else "-"
    print(f"{r['timeframe_label']:<5}{trail_s:<6}{r['strategy']:<24}{r['trades']:<8}"
          f"{r['returnPct']:<10.1f}{r['bhReturnPct']:<10.1f}{r['vsBHPct']:<11.1f}{r['beatsBH']}")

beats = [r for r in all_rows if r["beatsBH"]]
print(f"\n{len(beats)} of {len(all_rows)} combinations beat Buy & Hold over this window.")

candidates = sorted(beats, key=lambda r: -(r["vsBHPct"] or 0))
print(f"\n=== validate() on all {len(candidates)} candidates that beat B&H ===\n")
val_out = []
for r in candidates:
    label, trail = r["timeframe_label"], r["trailing_stop_atr"]
    bars = label_to_bars[label]
    spec = {"strategy": r["strategy"], "allow_long": True, "allow_short": True}
    if trail is not None:
        spec["trailing_stop_atr"] = trail
    out = verdict.validate(SYMBOL, label, spec, bars, None)
    full, oos, gates = out["full"], out["oos"], out["gates"]
    trail_s = str(trail) if trail is not None else "none"
    print(f"{label:<5}trail={trail_s:<6}{r['strategy']:<24} verdict={out['verdict']:<9} "
          f"trades={full['trades']:<4} vsBH={full['vsBHPct']:+.1f}pp  "
          f"oosBeats={gates['beatsOutOfSample']}  oosVsBH={oos['vsBHPct']:+.1f}pp  "
          f"ticks={gates['survivesTicks']}  top3={gates['survivesTop3Removal']}  "
          f"top5={gates['survivesTop5Removal']}  folds={gates['foldsWon']}/{gates['foldsRun']}  "
          f"varHeld={gates['variationsHeldPct']}")
    print(f"   why: {out['why']}")
    val_out.append({"timeframe": label, "trailing_stop_atr": trail, "strategy": r["strategy"],
                    "verdict": out["verdict"], "gates": gates, "full": full, "oos": oos, "why": out["why"]})

json.dump(val_out, open("study/lab/taril_apr23_to_peak_validate_results.json", "w"), indent=2, default=str)
print(f"\nDONE. {len(all_rows)} backtests, {len(candidates)} sent to validate().")
