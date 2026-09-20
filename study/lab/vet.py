"""The gate a promising strategy has to pass before it can be called a winner.

Nothing is optimised in here. The specification arrives locked; these checks
only try to break it — on unseen data, at neighbouring parameter values, in
each third of history, with the shorts removed, and at higher costs.
"""
import copy, json
from pathlib import Path

from study.lab import harness, data


def _cost(spec, sym, tf, candles, slip):
    import study.lab.harness as h
    old = h.SLIPPAGE_TICKS
    h.SLIPPAGE_TICKS = slip
    try:
        return h.run(sym, tf, spec, candles)
    finally:
        h.SLIPPAGE_TICKS = old


def vet(sym, tf, name, spec, variants):
    """Run the full robustness battery for one locked specification."""
    c = data.load(sym, tf)
    out = {"symbol": sym, "tf": tf, "name": name, "spec": spec}

    # 1. Chronological out-of-sample. Development is the first 70% of the bars
    #    in time order; the test half is never seen while designing.
    out["split"] = harness.split(sym, tf, spec)

    # 2. Thirds. A strategy that only works in one third of the history is a
    #    description of that third, not a system.
    n = len(c) // 3
    out["thirds"] = [harness.run(sym, tf, spec, c[i * n:(i + 1) * n]) for i in range(3)]

    # 3. Parameter sensitivity. Neighbouring values, one change at a time.
    out["sensitivity"] = []
    for label, delta in variants:
        s = copy.deepcopy(spec)
        s.setdefault("strategy_parameters", {}).update(delta)
        out["sensitivity"].append({"label": label, "result": harness.run(sym, tf, s, c)})

    # 4. Direction. Does shorting actually earn its place?
    out["direction"] = {}
    for label, cfg in (("long+short", {"allow_long": True, "allow_short": True}),
                       ("long only", {"allow_long": True, "allow_short": False}),
                       ("short only", {"allow_long": False, "allow_short": True})):
        s = copy.deepcopy(spec); s.update(cfg)
        sp = s.setdefault("strategy_parameters", {})
        if "trade_shorts" in sp:
            sp["trade_shorts"] = cfg["allow_short"]
        out["direction"][label] = harness.run(sym, tf, s, c)

    # 5. Costs. One tick is the assumption; 2, 4 and 8 ask how much of the
    #    result is really a bet on getting filled well.
    out["costs"] = {str(t): _cost(spec, sym, tf, c, t) for t in (1, 2, 4, 8)}
    return out


def report(v):
    f = v["full"] if "full" in v else None
    L = []
    s = v["split"]
    L.append(f"\n  === {v['name']}  {v['symbol']} {v['tf']} ===")
    L.append(f"  {'window':<14}{'bars':>7}{'tr':>6}{'net':>11}{'B&H':>11}{'vs B&H':>11}"
             f"{'PF':>6}{'DD%':>7}{'top1%':>7}")
    for k in ("full", "dev", "oos"):
        r = s[k]
        L.append(f"  {k:<14}{r['bars']:>7}{r['trades']:>6}{r['net']:>11,.0f}{r['bh']:>11,.0f}"
                 f"{r['vsBH']:>11,.0f}{(r['pf'] or 0):>6.2f}{abs(r['maxDDPct']):>7.1f}"
                 f"{r['topTradeShare']:>7.1f}"
                 + ("  BEAT" if r["beatsBH"] else ""))
    L.append("  thirds:")
    for i, r in enumerate(v["thirds"], 1):
        L.append(f"    third {i}      {r['bars']:>7}{r['trades']:>6}{r['net']:>11,.0f}"
                 f"{r['bh']:>11,.0f}{r['vsBH']:>11,.0f}{(r['pf'] or 0):>6.2f}"
                 f"{abs(r['maxDDPct']):>7.1f}" + ("  BEAT" if r["beatsBH"] else ""))
    L.append("  parameter sensitivity:")
    for x in v["sensitivity"]:
        r = x["result"]
        L.append(f"    {x['label']:<24}{r['trades']:>5}tr{r['net']:>11,.0f}"
                 f"{r['vsBH']:>11,.0f}{(r['pf'] or 0):>6.2f}" + ("  BEAT" if r["beatsBH"] else ""))
    L.append("  direction:")
    for k, r in v["direction"].items():
        L.append(f"    {k:<24}{r['trades']:>5}tr{r['net']:>11,.0f}{r['vsBH']:>11,.0f}"
                 f"{(r['pf'] or 0):>6.2f}" + ("  BEAT" if r["beatsBH"] else ""))
    L.append("  slippage ticks:")
    for k, r in v["costs"].items():
        L.append(f"    {k+' tick':<24}{r['trades']:>5}tr{r['net']:>11,.0f}{r['vsBH']:>11,.0f}"
                 f"{(r['pf'] or 0):>6.2f}" + ("  BEAT" if r["beatsBH"] else ""))
    return "\n".join(L)
