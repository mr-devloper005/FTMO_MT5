"""TARIL, custom N-trading-day bars (1D/2D/3D/5D/8D/10D), every strategy,
April 2023 to now -- then validate() on anything that actually beats B&H.

Ad-hoc research script (repo convention: study/lab/*.py). The Lab has no
native support for arbitrary N-day bars (only 1d/1w), so this builds them
directly from the cached daily series and registers them with the Lab's own
BARS_PER_YEAR / ENGINE_TF tables at runtime, in-process -- no file in
ftmo_mt5/ is touched. Treat the results with the same skepticism as
everything else this session: a raw in-sample "beat" here is a candidate to
stress-test, not a finding to report as fact.
"""
import json
from datetime import date, datetime, timezone

from ftmo_mt5.lab import metrics, service, verdict, windows
from ftmo_mt5.marketdata.base import Candle

SYMBOL = "TARIL.NS"
START = date(2023, 4, 1)
NDAYS = [1, 2, 3, 5, 8, 10]

all_candles = windows.load(SYMBOL, "1d")
start_ts = int(datetime(START.year, START.month, START.day, tzinfo=timezone.utc).timestamp())
daily = [c for c in all_candles if c.timestamp >= start_ts]
print(f"{SYMBOL}: {len(daily)} daily bars from {START} onward\n")


def n_day_bars(candles, n):
    """Chunk N consecutive trading days into one OHLCV bar. Not calendar-aligned --
    just every N bars, which is what '2D/3D/5D/8D/10D bars' means in retail usage."""
    if n == 1:
        return list(candles)
    out = []
    for i in range(0, len(candles), n):
        chunk = candles[i:i + n]
        if len(chunk) < n:
            break  # drop a short trailing partial bar -- it isn't a real N-day bar
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
all_rows = []

for n in NDAYS:
    label = TF_LABELS[n]
    bars = n_day_bars(daily, n)
    bh_first, bh_last = bars[0].close, bars[-1].close
    size = service.size_for(SYMBOL, bars, 100_000.0)
    print(f"=== {label}: {len(bars)} bars ({datetime.fromtimestamp(bars[0].timestamp, tz=timezone.utc).date()} "
          f"to {datetime.fromtimestamp(bars[-1].timestamp, tz=timezone.utc).date()}), size={size} ===")

    for name, params in defaults.items():
        spec = {"strategy": name, "strategy_parameters": dict(params),
                "position_size": float(size), "allow_long": True, "allow_short": True}
        try:
            r = metrics.measure(SYMBOL, label, spec, bars)
        except Exception as e:
            print(f"  {name:<24} ERROR {type(e).__name__}: {e}")
            continue
        r["timeframe_label"] = label
        all_rows.append(r)

    best = max((r for r in all_rows if r["timeframe_label"] == label),
               key=lambda r: r["vsBHPct"] or -1e18)
    beat_count = sum(1 for r in all_rows if r["timeframe_label"] == label and r["beatsBH"])
    print(f"  best: {best['strategy']} vsBH={best['vsBHPct']:+.1f}pp trades={best['trades']} "
          f"beatsBH={best['beatsBH']}  |  {beat_count}/{len(defaults)} strategies beat B&H on this timeframe\n")

json.dump(all_rows, open("study/lab/taril_multitf_matrix_results.json", "w"), indent=2, default=str)

# --- full leaderboard across every timeframe x strategy tried ---
print("\n=== FULL LEADERBOARD, ALL TIMEFRAMES x STRATEGIES, sorted by alpha ===")
ranked = sorted(all_rows, key=lambda r: -(r["vsBHPct"] or -1e18))
print(f"{'TF':<5}{'STRATEGY':<24}{'TRADES':<8}{'RET%':<10}{'BH%':<10}{'ALPHA(pp)':<11}{'BEAT?'}")
for r in ranked[:20]:
    print(f"{r['timeframe_label']:<5}{r['strategy']:<24}{r['trades']:<8}{r['returnPct']:<10.1f}"
          f"{r['bhReturnPct']:<10.1f}{r['vsBHPct']:<11.1f}{r['beatsBH']}")

beats = [r for r in all_rows if r["beatsBH"]]
print(f"\n{len(beats)} of {len(all_rows)} timeframe x strategy combinations beat Buy & Hold in-sample.")

# --- validate() on every real candidate: beats BH, at least 5 trades ---
candidates = [r for r in beats if r["trades"] >= 5]
candidates.sort(key=lambda r: -(r["vsBHPct"] or 0))
print(f"\n=== validate() on all {len(candidates)} candidates that beat B&H with >=5 trades ===\n")

label_to_bars = {TF_LABELS[n]: n_day_bars(daily, n) for n in NDAYS}
val_out = []
for r in candidates:
    label = r["timeframe_label"]
    bars = label_to_bars[label]
    spec = {"strategy": r["strategy"], "allow_long": True, "allow_short": True}
    out = verdict.validate(SYMBOL, label, spec, bars, None)
    full, oos, gates = out["full"], out["oos"], out["gates"]
    print(f"{label:<5}{r['strategy']:<24} verdict={out['verdict']:<9} trades={full['trades']:<4} "
          f"vsBH={full['vsBHPct']:+.1f}pp  oosBeats={gates['beatsOutOfSample']}  "
          f"oosVsBH={oos['vsBHPct']:+.1f}pp  ticks={gates['survivesTicks']}  "
          f"top3={gates['survivesTop3Removal']}  top5={gates['survivesTop5Removal']}  "
          f"folds={gates['foldsWon']}/{gates['foldsRun']}  varHeld={gates['variationsHeldPct']}")
    print(f"   why: {out['why']}")
    val_out.append({"timeframe": label, "strategy": r["strategy"], "verdict": out["verdict"],
                    "gates": gates, "full": full, "oos": oos, "why": out["why"]})

json.dump(val_out, open("study/lab/taril_multitf_validate_results.json", "w"), indent=2, default=str)
print(f"\nDONE. {len(all_rows)} backtests, {len(candidates)} sent to validate().")
