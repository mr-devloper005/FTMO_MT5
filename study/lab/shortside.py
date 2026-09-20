"""The short side as its own family. Stage 1: /CL 1d only.

Each variant changes one dimension of the same core idea and is frozen before
running. Long-only and buy-and-hold are printed alongside every row, because a
short-only rule compared with a long benchmark flatters itself whenever the
market falls, and that has to stay visible.
"""
import json
from pathlib import Path
import study.lab.strats
from study.lab import harness, data

SYM, TF = "/CL", "1d"
CORE = {"lookback":20,"atr_period":14,"atr_average":20,"trend_period":200,
        "trade_shorts":True,"exit_on_trend_cross":False,
        "atr_multiple":1.0,"adx_min":0.0,"atr_rising_bars":0}


def S(**over):
    s = dict(strategy="VOL_BREAKOUT_TREND", allow_long=False, allow_short=True,
             stop_loss_atr=2.0, take_profit_atr=3.0,
             strategy_parameters=dict(CORE))
    p = over.pop("params", None)
    if p: s["strategy_parameters"].update(p)
    s.update(over)
    return s


VARIANTS = [
 ("S0  baseline (20 bars, EMA200, 2/3)", S()),
 ("S1  lookback 10",                    S(params={"lookback":10})),
 ("S2  lookback 15",                    S(params={"lookback":15})),
 ("S3  lookback 30",                    S(params={"lookback":30})),
 ("S4  EMA regime 50",                  S(params={"trend_period":50})),
 ("S5  EMA regime 100",                 S(params={"trend_period":100})),
 ("S6  EMA regime 300",                 S(params={"trend_period":300})),
 ("S7  ATR must clear avg x1.2",        S(params={"atr_multiple":1.2})),
 ("S8  ATR must clear avg x1.5",        S(params={"atr_multiple":1.5})),
 ("S9  ADX above 20",                   S(params={"adx_min":20.0})),
 ("S10 ADX above 25",                   S(params={"adx_min":25.0})),
 ("S11 ATR rising over 5 bars",         S(params={"atr_rising_bars":5})),
 ("S12 ATR rising over 10 bars",        S(params={"atr_rising_bars":10})),
 ("S13 time exit after 10 bars",        S(max_bars_in_trade=10)),
 ("S14 time exit after 20 bars",        S(max_bars_in_trade=20)),
 ("S15 time exit after 40 bars",        S(max_bars_in_trade=40)),
 ("S16 trailing 2 ATR, no target",      S(take_profit_atr=None, trailing_stop_atr=2.0)),
 ("S17 trailing 3 ATR, no target",      S(take_profit_atr=None, trailing_stop_atr=3.0)),
 ("S18 stop 1.5 / target 3",            S(stop_loss_atr=1.5)),
]

if __name__ == "__main__":
    c = data.load(SYM, TF)
    cut = int(len(c) * 0.70)
    rows = []
    print(f"  /CL 1d · 6,531 bars · 26 years · buy & hold $51,645\n")
    print(f"  {'variant':<38}{'tr':>5}{'net':>10}{'vs B&H':>10}{'PF':>6}{'win%':>7}"
          f"{'DD%':>7}{'top1%':>7}{'OOS vsB&H':>11}{'OOS PF':>8}")
    for name, spec in VARIANTS:
        f = harness.run(SYM, TF, spec, c)
        o = harness.run(SYM, TF, spec, c[cut:])
        rows.append(dict(name=name, spec=spec, trades=f["trades"], net=f["net"],
            bh=f["bh"], vsBH=f["vsBH"], beat=f["beatsBH"], pf=f["pf"],
            win=f["winRate"], dd=f["maxDDPct"], top1=f["topTradeShare"],
            sharpe=f["sharpe"], tim=f["timeInMarket"],
            oosVsBH=o["vsBH"], oosBeat=o["beatsBH"], oosPF=o["pf"],
            oosTrades=o["trades"], oosNet=o["net"]))
        print(f"  {name:<38}{f['trades']:>5}{f['net']:>10,.0f}{f['vsBH']:>10,.0f}"
              f"{(f['pf'] or 0):>6.2f}{f['winRate']:>6.1f}%{abs(f['maxDDPct']):>6.1f}%"
              f"{f['top1']  if False else f['topTradeShare']:>6.1f}%"
              f"{o['vsBH']:>11,.0f}{(o['pf'] or 0):>8.2f}"
              + ("  BEAT" if f["beatsBH"] else ""))

    print("\n  For reference, the same core rule on the other two sides:\n")
    for lbl, cfg in (("long only", dict(allow_long=True, allow_short=False,
                                        params={"trade_shorts": False})),
                     ("long + short", dict(allow_long=True, allow_short=True))):
        r = harness.run(SYM, TF, S(**cfg), c)
        print(f"  {lbl:<38}{r['trades']:>5}{r['net']:>10,.0f}{r['vsBH']:>10,.0f}"
              f"{(r['pf'] or 0):>6.2f}{r['winRate']:>6.1f}%{abs(r['maxDDPct']):>6.1f}%"
              + ("  BEAT" if r["beatsBH"] else ""))

    Path("study/lab/shortside.json").write_text(json.dumps(rows, indent=1))
    beat = [r for r in rows if r["beat"]]
    both = [r for r in rows if r["beat"] and r["oosBeat"]]
    print(f"\n  {len(beat)} of {len(rows)} variants beat buy & hold · "
          f"{len(both)} beat it out-of-sample as well")
