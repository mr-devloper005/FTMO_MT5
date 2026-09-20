"""Walk-forward. A single 70/30 split can be lucky; five sequential folds cannot
all be lucky in the same direction.

The parameters are locked before the first fold and never touched again. Each
fold tests on a slice of history that comes strictly after the slice before it,
and buy & hold is recomputed inside each slice.
"""
import json
from pathlib import Path
import study.lab.strats
from study.lab import harness, data

SPEC = dict(strategy="TREND_REGIME", allow_short=True,
            strategy_parameters={"regime_period": 200, "buffer_pct": 0.5})


def folds(sym, tf, spec, n=5):
    c = data.load(sym, tf)
    step = len(c) // n
    out = []
    for k in range(n):
        # Each fold carries the warm-up bars it needs for a 200-bar average,
        # but is only measured over its own slice.
        lo, hi = k * step, (k + 1) * step if k < n - 1 else len(c)
        out.append(harness.run(sym, tf, spec, c[max(0, lo - 250):hi]))
    return out


if __name__ == "__main__":
    print("  Walk-forward, five sequential folds, parameters locked throughout\n")
    print(f"  {'fold':<8}{'bars':>7}{'tr':>5}{'net':>11}{'B&H':>11}{'vs B&H':>11}"
          f"{'PF':>6}{'DD%':>7}{'TiM%':>7}")
    store = {}
    for sym, tf in (("/CL", "1h"),):
        fs = folds(sym, tf, SPEC)
        store[f"{sym}{tf}"] = fs
        won = 0
        for i, r in enumerate(fs, 1):
            won += bool(r["beatsBH"])
            print(f"  {i:<8}{r['bars']:>7}{r['trades']:>5}{r['net']:>11,.0f}"
                  f"{r['bh']:>11,.0f}{r['vsBH']:>11,.0f}{(r['pf'] or 0):>6.2f}"
                  f"{abs(r['maxDDPct']):>7.1f}{r['timeInMarket']:>7.1f}"
                  + ("  BEAT" if r["beatsBH"] else ""))
        print(f"\n  beat buy & hold in {won} of {len(fs)} folds")

    print("\n  Regime length: a plateau, or a single lucky value?")
    print(f"  {'SMA':<8}{'tr':>5}{'net':>11}{'vs B&H':>11}{'PF':>6}")
    grid = {}
    for p in (120, 140, 160, 180, 200, 220, 240, 260, 280, 300):
        s = json.loads(json.dumps(SPEC)); s["strategy_parameters"]["regime_period"] = p
        r = harness.run("/CL", "1h", s)
        grid[p] = r
        print(f"  {p:<8}{r['trades']:>5}{r['net']:>11,.0f}{r['vsBH']:>11,.0f}"
              f"{(r['pf'] or 0):>6.2f}" + ("  BEAT" if r["beatsBH"] else ""))
    store["smaGrid"] = {str(k): v for k, v in grid.items()}
    Path("study/lab/walkforward.json").write_text(json.dumps(store, indent=1))
