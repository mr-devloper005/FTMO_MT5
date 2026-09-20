"""Re-test of the daily 200-SMA swing specification, from scratch.

Runs the rule exactly as written, then with condition E switched off, over the
full daily history of all three markets. Everything is measured by the same
engine the tool uses, through the strategy now registered in the tool.
"""
import json
from pathlib import Path
import study.lab.strats
from study.lab import harness, data, vet

def spec(**over):
    p = {"sma_period": 200, "buffer_pct": 1.0, "max_extension_pct": 5.0,
         "require_bullish_candle": True, "use_last_sell_filter": False,
         "chase_pct": 2.0, "exit_confirm_days": 0}
    p.update(over)
    return dict(strategy="DAILY_SWING_SMA200", allow_short=False,
                strategy_parameters=p)

AS_WRITTEN = spec(use_last_sell_filter=True)
NO_E       = spec()

VARIANTS = [("buffer 0.5%", {"buffer_pct": 0.5}),
            ("buffer 1.5%", {"buffer_pct": 1.5}),
            ("buffer 2%",   {"buffer_pct": 2.0}),
            ("no-chase 3%", {"max_extension_pct": 3.0}),
            ("no-chase 8%", {"max_extension_pct": 8.0}),
            ("no-chase off",{"max_extension_pct": 100000.0}),
            ("SMA 150",     {"sma_period": 150}),
            ("SMA 250",     {"sma_period": 250}),
            ("no up-candle",{"require_bullish_candle": False}),
            ("exit confirm 1d", {"exit_confirm_days": 1})]


def line(sym, label, r):
    return (f"  {sym:<6}{label:<22}{r['bars']:>6}{r['trades']:>5}{r['net']:>12,.0f}"
            f"{r['bh']:>12,.0f}{r['vsBH']:>12,.0f}{(r['pf'] or 0):>6.2f}"
            f"{abs(r['maxDDPct']):>7.1f}{abs(r['bhDDPct']):>8.1f}"
            f"{r['winRate']:>7.1f}{r['timeInMarket']:>7.1f}"
            + ("  BEAT" if r["beatsBH"] else ""))


def main():
    out = {}
    print("  Full daily history, both readings of the specification\n")
    print(f"  {'mkt':<6}{'version':<22}{'bars':>6}{'tr':>5}{'net':>12}{'B&H':>12}"
          f"{'vs B&H':>12}{'PF':>6}{'DD%':>7}{'B&H DD':>8}{'win%':>7}{'TiM%':>7}")
    for sym in ("/CL", "/GC", "/NQ"):
        c = data.load(sym, "1d")
        for label, sp in (("as written (E on)", AS_WRITTEN), ("E switched off", NO_E)):
            r = harness.run(sym, "1d", sp, c)
            out[f"{sym} {label}"] = r
            print(line(sym, label, r))
        print()

    print("\n  Chronological 70/30 split - 18 years development, 8 years never seen\n")
    print(f"  {'mkt':<6}{'version':<18}{'half':<5}{'tr':>5}{'net':>12}{'B&H':>12}"
          f"{'vs B&H':>12}{'PF':>6}{'DD%':>7}")
    for sym in ("/CL", "/GC", "/NQ"):
        for label, sp in (("as written", AS_WRITTEN), ("E off", NO_E)):
            s = harness.split(sym, "1d", sp)
            out[f"{sym} {label} split"] = s
            for k in ("dev", "oos"):
                r = s[k]
                print(f"  {sym:<6}{label:<18}{k:<5}{r['trades']:>5}{r['net']:>12,.0f}"
                      f"{r['bh']:>12,.0f}{r['vsBH']:>12,.0f}{(r['pf'] or 0):>6.2f}"
                      f"{abs(r['maxDDPct']):>7.1f}" + ("  BEAT" if r["beatsBH"] else ""))
        print()

    print("\n  Thirds of history, E off - does it work in more than one era?\n")
    print(f"  {'mkt':<6}{'third':<8}{'tr':>5}{'net':>12}{'B&H':>12}{'vs B&H':>12}"
          f"{'PF':>6}{'DD%':>7}")
    for sym in ("/CL", "/GC", "/NQ"):
        c = data.load(sym, "1d"); n = len(c) // 3
        thirds = []
        for i in range(3):
            r = harness.run(sym, "1d", NO_E, c[i * n:(i + 1) * n])
            thirds.append(r)
            print(f"  {sym:<6}{i+1:<8}{r['trades']:>5}{r['net']:>12,.0f}{r['bh']:>12,.0f}"
                  f"{r['vsBH']:>12,.0f}{(r['pf'] or 0):>6.2f}{abs(r['maxDDPct']):>7.1f}"
                  + ("  BEAT" if r["beatsBH"] else ""))
        out[f"{sym} thirds"] = thirds
        print()

    Path("study/lab/retest_daily.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
