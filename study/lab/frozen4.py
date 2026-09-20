"""Strategy 4, frozen: simple trend pullback. Then OOS. Then sensitivity."""
import json
from pathlib import Path
import study.lab.strats
from study.lab import harness, board, data

PARAMS = {"fast_period":20,"mid_period":50,"slow_period":200,"rsi_period":14,
          "rsi_dip":45.0,"rsi_cross":50.0,"rsi_spike":55.0,"dip_lookback":10,
          "trade_shorts":True}
SPEC = dict(strategy="SIMPLE_PULLBACK", allow_short=True,
            stop_loss_atr=1.5, take_profit_atr=3.0, strategy_parameters=dict(PARAMS))
TFS = ("5m","15m","30m","1h","2h","4h","6h","8h","12h","1d")
SYMS = ("/NQ","/GC","/CL")

# One change at a time, and only values a person would pick on purpose.
VARIANTS = [
  ("EMA 10/30/100",   {"fast_period":10,"mid_period":30,"slow_period":100}),
  ("EMA 30/80/300",   {"fast_period":30,"mid_period":80,"slow_period":300}),
  ("RSI dip 40",      {"rsi_dip":40.0,"rsi_spike":60.0}),
  ("RSI dip 50",      {"rsi_dip":50.0,"rsi_spike":50.0}),
  ("pullback 5 bars", {"dip_lookback":5}),
  ("pullback 20 bars",{"dip_lookback":20}),
]
EXITS = [("stop 1/target 2", dict(stop_loss_atr=1.0, take_profit_atr=2.0)),
         ("stop 2/target 4", dict(stop_loss_atr=2.0, take_profit_atr=4.0)),
         ("stop 1.5/target 4.5", dict(stop_loss_atr=1.5, take_profit_atr=4.5))]


def main():
    rows = []
    for sym in SYMS:
        for tf in TFS:
            c = data.load(sym, tf); cut = int(len(c)*0.70)
            f = harness.run(sym, tf, SPEC, c)
            o = harness.run(sym, tf, SPEC, c[cut:])
            board.add(batch="frozen-4", symbol=sym, tf=tf,
                      name="Simple trend pullback", spec=SPEC, result=f)
            rows.append(dict(symbol=sym, tf=tf, trades=f["trades"], net=f["net"],
                bh=f["bh"], vsBH=f["vsBH"], beat=f["beatsBH"], pf=f["pf"],
                win=f["winRate"], dd=f["maxDDPct"], sharpe=f["sharpe"],
                tim=f["timeInMarket"], top1=f["topTradeShare"],
                oosVsBH=o["vsBH"], oosBeat=o["beatsBH"], oosNet=o["net"],
                oosTrades=o["trades"], oosPF=o["pf"]))
        print(f"  {sym} done", flush=True)

    sh = lambda x: f"{x:.2f}" if x is not None else "n/a"
    print(f"\n\n  STRATEGY 4 — simple trend pullback (frozen)\n")
    print(f"  {'Inst':<5}{'TF':<5}{'Tr':>5}{'Net P&L':>11}{'B&H':>11}{'Vs B&H':>11}"
          f"{'PF':>6}{'Win%':>7}{'MaxDD':>8}{'Sharpe':>8}{'OOS vs B&H':>12}  verdict")
    for r in rows:
        print(f"  {r['symbol']:<5}{r['tf']:<5}{r['trades']:>5}{r['net']:>11,.0f}"
              f"{r['bh']:>11,.0f}{r['vsBH']:>11,.0f}{(r['pf'] or 0):>6.2f}"
              f"{r['win']:>6.1f}%{abs(r['dd']):>7.1f}%{sh(r['sharpe']):>8}"
              f"{r['oosVsBH']:>12,.0f}  "
              f"{'BEAT B&H' if r['beat'] else 'DID NOT BEAT'}")

    d = {(r['symbol'],r['tf']): r for r in rows}
    for title, key, fmt in (("VS BUY & HOLD", 'vsBH', lambda x: f"{x:,.0f}"),
                            ("PROFIT FACTOR", 'pf', lambda x: f"{(x or 0):.2f}"),
                            ("OOS VS B&H", 'oosVsBH', lambda x: f"{x:,.0f}")):
        print(f"\n  {title}\n")
        print(f"  {'':<6}" + "".join(f"{t:>11}" for t in TFS))
        for s in SYMS:
            cells=[]
            for t in TFS:
                r=d[(s,t)]
                mk = "*" if (key=='vsBH' and r['beat']) or (key=='oosVsBH' and r['oosBeat']) else ""
                cells.append(mk+fmt(r[key]))
            print(f"  {s:<6}" + "".join(f"{x:>11}" for x in cells))

    # --- sensitivity, on the cells that beat -----------------------------
    winners = [r for r in rows if r['beat'] and r['bh'] > 1000]
    sens = {}
    print(f"\n\n  PARAMETER SENSITIVITY — on each cell that beat buy & hold\n")
    for w in winners:
        sym, tf = w['symbol'], w['tf']
        c = data.load(sym, tf)
        print(f"  {sym} {tf}   baseline {w['vsBH']:>+9,.0f}  PF {w['pf']:.2f}  "
              f"{w['trades']} trades")
        out = []
        for lbl, delta in VARIANTS:
            s = json.loads(json.dumps(SPEC)); s["strategy_parameters"].update(delta)
            r = harness.run(sym, tf, s, c)
            out.append((lbl, r))
            print(f"      {lbl:<22}{r['trades']:>5}tr{r['net']:>11,.0f}"
                  f"{r['vsBH']:>11,.0f}  PF {(r['pf'] or 0):>5.2f}"
                  + ("  BEAT" if r['beatsBH'] else ""))
        for lbl, delta in EXITS:
            s = json.loads(json.dumps(SPEC)); s.update(delta)
            r = harness.run(sym, tf, s, c)
            out.append((lbl, r))
            print(f"      {lbl:<22}{r['trades']:>5}tr{r['net']:>11,.0f}"
                  f"{r['vsBH']:>11,.0f}  PF {(r['pf'] or 0):>5.2f}"
                  + ("  BEAT" if r['beatsBH'] else ""))
        n = sum(1 for _, r in out if r['beatsBH'])
        sens[f"{sym} {tf}"] = {"beat": n, "of": len(out)}
        print(f"      -> {n} of {len(out)} variations still beat\n")

    Path("study/lab/frozen4.json").write_text(json.dumps(
        {"rows": rows, "sensitivity": sens}, indent=1))
    real = [r for r in rows if r['bh'] > 1000]
    print(f"  {sum(1 for r in rows if r['net']>0)}/30 profitable · "
          f"beat B&H {sum(1 for r in real if r['beat'])}/{len(real)} · "
          f"OOS beat {sum(1 for r in real if r['oosBeat'])} · "
          f"BOTH {sum(1 for r in real if r['beat'] and r['oosBeat'])}")


if __name__ == "__main__":
    main()
