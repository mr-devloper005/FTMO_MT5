"""A wide variation sweep on /NQ alone, judged honestly.

Every variation is scored twice: once over the whole history, and once under a
chronological split where the best variation is chosen on the first 70% and
then run, locked, on the last 30%. The second number is the one that counts.

The families and grids below are deliberately broad — this is the search the
goal asks for — but breadth is exactly why the split matters. With enough
variations something always wins the development half.
"""
import json
from pathlib import Path
import study.lab.strats
from study.lab import harness, data

TFS = ("1h", "2h", "4h", "6h", "8h", "12h", "1d")
MIN = {"1h":60,"2h":120,"4h":240,"6h":360,"8h":480,"12h":720,"1d":1440}


def grids(tf):
    h = MIN[tf]
    out = []
    # Participation rules: own the index while it is above a long average.
    for p in (100, 150, 200, 250, 300):
        for q in (0.0, 0.5, 1.0, 2.0):
            out.append((f"regime SMA{p} band {q}%", dict(strategy="TREND_REGIME",
                allow_short=False,
                strategy_parameters={"regime_period": p, "buffer_pct": q})))
    # The swing rule, across its two entry-window numbers.
    for b in (0.5, 1.0, 2.0):
        for e in (3.0, 5.0, 10.0, 100000.0):
            out.append((f"swing buf {b}% ext {e:g}%", dict(strategy="DAILY_SWING_SMA200",
                allow_short=False, strategy_parameters={"sma_period":200,"buffer_pct":b,
                "max_extension_pct":e,"require_bullish_candle":True,
                "use_last_sell_filter":False,"exit_confirm_days":0,"trade_shorts":False})))
    # A single moving average, long or flat, with and without a trail.
    for p in (50, 100, 200):
        for t in (None, 2.0, 4.0):
            s = dict(strategy="EMA_REGIME_LS", allow_short=False,
                     strategy_parameters={"period": p, "trade_shorts": False})
            if t: s["trailing_stop_atr"] = t
            out.append((f"EMA{p} long{' trail '+str(t) if t else ''}", s))
    # Breakouts, long only — shorts have lost on every index run so far.
    for n, x in ((20,10),(55,20),(100,50),(200,100)):
        out.append((f"Donchian {n}/{x} long", dict(strategy="DONCHIAN_BREAKOUT",
            allow_short=False,
            strategy_parameters={"entry_lookback":n,"exit_lookback":x})))
    # Momentum with a trend filter.
    for a, b_ in ((10,30),(20,50),(30,80),(50,150)):
        out.append((f"EMA {a}/{b_} + RSI long", dict(strategy="EMA_RSI_PATTERN",
            allow_short=False, stop_loss_atr=2.0, take_profit_atr=3.0,
            strategy_parameters={"fast_period":a,"slow_period":b_,"rsi_buy_above":50.0,
            "rsi_sell_below":50.0,"require_pattern":True})))
    # Two-timeframe participation.
    for hp in (50, 100, 200):
        out.append((f"dual regime higher SMA{hp}", dict(strategy="DUAL_REGIME",
            allow_short=False, strategy_parameters={"period":200,"buffer_pct":0.5,
            "higher_minutes":h*4,"higher_period":hp,"trade_shorts":False})))
    # Keltner, long only.
    for k in (0.5, 1.0, 2.0):
        out.append((f"Keltner {k} ATR long", dict(strategy="KELTNER_RIDE",
            allow_short=False, strategy_parameters={"ema_period":20,"entry_atr":k,
            "trend_period":200,"trade_shorts":False})))
    return out


def main():
    rows = []
    for tf in TFS:
        c = data.load("/NQ", tf)
        cut = int(len(c) * 0.70)
        dev, oos = c[:cut], c[cut:]
        g = grids(tf)
        full_best = dev_best = None
        for label, spec in g:
            try:
                f = harness.run("/NQ", tf, spec, c)
                d = harness.run("/NQ", tf, spec, dev)
            except Exception:
                continue
            rows.append(dict(tf=tf, label=label, fullVsBH=f["vsBH"], fullNet=f["net"],
                             fullBH=f["bh"], fullBeat=f["beatsBH"], trades=f["trades"],
                             pf=f["pf"], dd=f["maxDDPct"], bhdd=f["bhDDPct"],
                             tim=f["timeInMarket"], top1=f["topTradeShare"],
                             devVsBH=d["vsBH"], devBeat=d["beatsBH"]))
            if full_best is None or f["vsBH"] > full_best[1]["vsBH"]:
                full_best = (label, f, spec)
            if dev_best is None or d["vsBH"] > dev_best[1]["vsBH"]:
                dev_best = (label, d, spec)
        o = harness.run("/NQ", tf, dev_best[2], oos)
        print(f"\n  /NQ {tf}   {len(c):,} bars   {len(g)} variations   B&H "
              f"${full_best[1]['bh']:,.0f}")
        f = full_best[1]
        print(f"    best over the whole history : {full_best[0]:<28}"
              f"net ${f['net']:>10,.0f}  vs B&H ${f['vsBH']:>10,.0f}"
              + ("  BEAT" if f["beatsBH"] else ""))
        print(f"    best on the first 70%       : {dev_best[0]:<28}"
              f"net ${dev_best[1]['net']:>10,.0f}  vs B&H ${dev_best[1]['vsBH']:>10,.0f}"
              + ("  BEAT" if dev_best[1]["beatsBH"] else ""))
        print(f"    ...that one on the last 30% : {'':<28}"
              f"net ${o['net']:>10,.0f}  vs B&H ${o['vsBH']:>10,.0f}"
              + ("  BEAT" if o["beatsBH"] else ""), flush=True)
        rows.append(dict(tf=tf, label="__oos__", chosen=dev_best[0],
                         oosVsBH=o["vsBH"], oosBeat=o["beatsBH"], oosNet=o["net"],
                         oosTrades=o["trades"]))
    Path("study/lab/nq_hunt.json").write_text(json.dumps(rows, indent=1))
    real = [r for r in rows if r.get("fullBeat") and r.get("fullBH",0) > 1000]
    print(f"\n  {len([r for r in rows if 'label' in r and r['label']!='__oos__'])} "
          f"variations run · {len(real)} beat buy & hold over the full history")


if __name__ == "__main__":
    main()
