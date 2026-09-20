"""Rank the ten by balanced criteria, then stress the leaders' parameters.

Ranking by profit alone rewards whichever strategy happened to catch the
largest move. The score below weighs evidence (trade count), quality (profit
factor), risk (drawdown) and concentration (how much of the profit came from a
single trade) alongside the result, and every component is reported so the
ranking can be argued with rather than taken on trust.
"""
import json, logging, structlog
structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.CRITICAL))
from datetime import date, timedelta
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from study.run_study import load, measure, build, SYMBOL

RES = json.loads(Path("study/results.json").read_text())

def score(r):
    """A single number for ranking, from components that are each defensible."""
    if r.get("error") or r["net"] <= 0 or not r["pf"] or r["pf"] <= 1:
        return None
    pf = min(r["pf"], 3.0) / 3.0                       # quality, capped
    dd = max(0.0, 1 - abs(r["maxDDPct"]) / 50.0)       # risk
    ev = min(r["trades"], 200) / 200.0                 # evidence
    conc = max(0.0, 1 - r["topTradeShare"] / 100.0)    # not one lucky trade
    return round(100 * (0.30*pf + 0.25*dd + 0.25*ev + 0.20*conc), 1)

rows = []
for tf, info in RES["runs"].items():
    for s in info["strategies"]:
        r = s["result"]
        if r.get("error"): continue
        rows.append({"tf": tf, "no": s["no"], "name": s["name"], "r": r,
                     "score": score(r)})

print(f"\n  ALL TWENTY RUNS, RANKED BY BALANCED SCORE\n")
print(f"  {'score':>6}  {'tf':>4} {'strategy':<34}{'trades':>7}{'net':>11}"
      f"{'PF':>6}{'DD%':>7}{'top1%':>7}{'vs B&H':>11}")
ranked = sorted([x for x in rows if x["score"] is not None],
                key=lambda x: -x["score"])
for x in ranked:
    r = x["r"]
    print(f"  {x['score']:>6}  {x['tf']:>4} {x['name']:<34}{r['trades']:>7}"
          f"{r['net']:>11,.0f}{r['pf']:>6.2f}{abs(r['maxDDPct']):>7.1f}"
          f"{r['topTradeShare']:>7.1f}{r['vsBH']:>11,.0f}")
excluded = [x for x in rows if x["score"] is None]
print(f"\n  excluded (loss-making or profit factor at or below 1): "
      f"{len(excluded)} of {len(rows)}")
for x in excluded:
    print(f"    {x['tf']:>4} {x['name']:<34} net {x['r']['net']:>11,.0f} "
          f"PF {x['r']['pf'] or 0:.2f}")

TOP = ranked[:5]
Path("study/top5.json").write_text(json.dumps(
    [{"tf": x["tf"], "no": x["no"], "name": x["name"], "score": x["score"]}
     for x in TOP], indent=1), encoding="utf-8")

# --- robustness -----------------------------------------------------------
VARIANTS = {
 "EMA_REGIME_LS":        [("EMA 80", {"period":80}), ("EMA 120",{"period":120}),
                          ("EMA 150",{"period":150})],
 "TREND_REGIME":         [("SMA 150",{"regime_period":150}), ("SMA 250",{"regime_period":250}),
                          ("SMA 200 + 0.5% buffer",{"buffer_pct":0.5})],
 "TREND_REGIME_PULLBACK":[("RSI dip 40",{"rsi_dip_below":40.0}),
                          ("RSI dip 50",{"rsi_dip_below":50.0}),
                          ("EMA 25/60",{"entry_fast":25,"entry_slow":60})],
 "DONCHIAN_BREAKOUT":    [("40/15",{"entry_lookback":40,"exit_lookback":15}),
                          ("70/25",{"entry_lookback":70,"exit_lookback":25}),
                          ("55/10",{"entry_lookback":55,"exit_lookback":10})],
 "FOUR_REGIME":          [("ADX 15",{"adx_above":15.0}), ("ADX 25",{"adx_above":25.0}),
                          ("breakout 30",{"breakout_lookback":30})],
 "GOLD_MASTER_TREND":    [("stop 1.25 ATR",{"stop_atr":1.25}), ("stop 2.0 ATR",{"stop_atr":2.0}),
                          ("RSI dip 40",{"rsi_dip_below":40.0})],
 "MTF_GREEN_STACK":      [("1/3/9",None), ("1/2/6",None), ("1/2/3",None)],
 "EMA_RSI_PATTERN":      [("EMA 25/60",{"fast_period":25,"slow_period":60}),
                          ("RSI 48/52",{"rsi_buy_above":48.0,"rsi_sell_below":52.0}),
                          ("no pattern",{"require_pattern":False})],
 "MEAN_REVERSION":       [("z 2.0",{"entry_z":2.0}), ("z 3.0",{"entry_z":3.0}),
                          ("period 150",{"period":150})],
}
RATIOS = {"1/3/9":(1,3,9), "1/2/6":(1,2,6), "1/2/3":(1,2,3)}

print(f"\n\n  ROBUSTNESS — top five, one parameter changed at a time\n")
robust = {}
for x in TOP:
    tf = x["tf"]
    spec = next(s["spec"] for s in build(tf, 60 if tf=="15m" else 240,
                                         "1 hour" if tf=="15m" else "4 hour")
                if s["no"] == x["no"])
    candles = load(tf)
    info = RES["runs"][tf]
    bpy = info["barsPerYear"]
    base = x["r"]
    key = spec["strategy"]
    print(f"  {x['no']}. {x['name']}  ({tf})   base net ${base['net']:,.0f}  "
          f"PF {base['pf']:.2f}")
    outcomes = []
    for label, override in VARIANTS.get(key, []):
        v = {k: dict(val) if isinstance(val, dict) else val for k, val in spec.items()}
        if override is None and label in RATIOS:
            m = 15 if tf == "15m" else 60
            a, b, c = RATIOS[label]
            v["strategy_parameters"] = {**spec["strategy_parameters"],
                "tf1_minutes": m*a, "tf2_minutes": m*b, "tf3_minutes": m*c}
        else:
            v["strategy_parameters"] = {**spec["strategy_parameters"], **(override or {})}
        try:
            r = measure(v, candles, tf, bpy)
        except Exception as exc:
            print(f"      {label:<24} failed {str(exc)[:40]}"); continue
        ok = r["net"] > 0 and (r["pf"] or 0) > 1
        outcomes.append(ok)
        print(f"      {label:<24} net {r['net']:>11,.0f}  PF {r['pf'] or 0:>5.2f}  "
              f"{'holds' if ok else 'BREAKS'}")
    kept = sum(outcomes)
    verdict = ("ROBUST" if kept == len(outcomes) and outcomes else
               "MODERATELY ROBUST" if kept >= max(1, len(outcomes) - 1) else
               "FRAGILE")
    robust[f"{tf}:{x['no']}"] = {"kept": kept, "of": len(outcomes), "verdict": verdict}
    print(f"      -> {verdict}  ({kept} of {len(outcomes)} variations still profitable)\n")

Path("study/robustness.json").write_text(json.dumps(robust, indent=1), encoding="utf-8")
print("  written study/top5.json and study/robustness.json")
