"""Strategy 6: regime switch, frozen. Thirty runs, then the robustness battery."""
import json
from pathlib import Path
import study.lab.strats
from study.lab import harness, board, data

SPEC = dict(strategy="REGIME_SWITCH_LS", allow_long=True, allow_short=True,
            stop_loss_atr=2.0, trailing_stop_atr=2.0,
            strategy_parameters={"mid_period":50,"slow_period":200,
                                 "breakout_lookback":20,"atr_period":14,
                                 "trade_shorts":True})
TFS = ("5m","15m","30m","1h","2h","4h","6h","8h","12h","1d")
SYMS = ("/NQ","/GC","/CL")

def main():
    rows = []
    for sym in SYMS:
        for tf in TFS:
            c = data.load(sym, tf); cut = int(len(c)*0.70)
            f = harness.run(sym, tf, SPEC, c)
            o = harness.run(sym, tf, SPEC, c[cut:])
            board.add(batch="frozen-6", symbol=sym, tf=tf,
                      name="Regime switch (long/short/cash)", spec=SPEC, result=f)
            rows.append(dict(symbol=sym, tf=tf, trades=f["trades"], net=f["net"],
                bh=f["bh"], vsBH=f["vsBH"], beat=f["beatsBH"], pf=f["pf"],
                win=f["winRate"], dd=f["maxDDPct"], sharpe=f["sharpe"],
                bigWin=f["bigWin"], bigLoss=f["bigLoss"], tim=f["timeInMarket"],
                top1=f["topTradeShare"], oosVsBH=o["vsBH"], oosBeat=o["beatsBH"],
                oosNet=o["net"], oosTrades=o["trades"], oosPF=o["pf"]))
        print(f"  {sym} done", flush=True)
    Path("study/lab/frozen6.json").write_text(json.dumps(rows, indent=1))

    sh = lambda x: f"{x:.2f}" if x is not None else "n/a"
    print("\n\n  STRATEGY 6 — regime switch (long / short / cash), frozen\n")
    print(f"  {'Inst':<5}{'TF':<5}{'Tr':>5}{'Net P&L':>11}{'B&H':>11}{'Vs B&H':>11}"
          f"{'PF':>6}{'Win%':>6}{'MaxDD':>7}{'Shrp':>7}{'BigWin':>9}{'BigLoss':>9}"
          f"{'TiM%':>6}{'OOS vsB&H':>11}  verdict")
    for r in rows:
        print(f"  {r['symbol']:<5}{r['tf']:<5}{r['trades']:>5}{r['net']:>11,.0f}"
              f"{r['bh']:>11,.0f}{r['vsBH']:>11,.0f}{(r['pf'] or 0):>6.2f}"
              f"{r['win']:>5.1f}%{abs(r['dd']):>6.1f}%{sh(r['sharpe']):>7}"
              f"{r['bigWin']:>9,.0f}{r['bigLoss']:>9,.0f}{r['tim']:>5.1f}%"
              f"{r['oosVsBH']:>11,.0f}  "
              f"{'BEAT B&H' if r['beat'] else 'DID NOT BEAT'}")

    d = {(r['symbol'],r['tf']): r for r in rows}
    for title, key, fmt in (("VS BUY & HOLD", 'vsBH', lambda x: f"{x:,.0f}"),
                            ("PROFIT FACTOR", 'pf', lambda x: f"{(x or 0):.2f}"),
                            ("MAX DRAWDOWN", 'dd', lambda x: f"{abs(x):.1f}%"),
                            ("OOS VS B&H", 'oosVsBH', lambda x: f"{x:,.0f}")):
        print(f"\n  {title}\n")
        print(f"  {'':<6}" + "".join(f"{t:>11}" for t in TFS))
        for s in SYMS:
            cells=[]
            for t in TFS:
                r=d[(s,t)]
                mk="*" if (key=='vsBH' and r['beat']) or (key=='oosVsBH' and r['oosBeat']) else ""
                cells.append(mk+fmt(r[key]))
            print(f"  {s:<6}" + "".join(f"{x:>11}" for x in cells))

    real=[r for r in rows if r['bh']>1000]
    both=[r for r in real if r['beat'] and r['oosBeat']]
    print(f"\n\n  {sum(1 for r in rows if r['net']>0)}/30 profitable · "
          f"beat B&H {sum(1 for r in real if r['beat'])}/{len(real)} real cells · "
          f"OOS beat {sum(1 for r in real if r['oosBeat'])} · BOTH {len(both)}")
    for r in both:
        print(f"      BOTH: {r['symbol']} {r['tf']}  {r['trades']}tr  "
              f"net {r['net']:,.0f}  vs B&H {r['vsBH']:+,.0f}  PF {r['pf']:.2f}  "
              f"DD {abs(r['dd']):.1f}%  top1 {r['top1']:.1f}%  OOS {r['oosVsBH']:+,.0f}")

if __name__ == "__main__":
    main()
