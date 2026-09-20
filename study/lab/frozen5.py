"""Strategy 5: volatility breakout + trend regime, under two frozen exits.

  A  stop 2 x ATR, target 3 x ATR
  B  stop 2 x ATR, exit when the close crosses back over the EMA 200

Variant A is byte-for-byte the rule already measured as Strategy 2; it is re-run
here so both exits sit in one table rather than being compared across messages.

A cell is only called a candidate if it clears all six tests at once: beats buy
and hold, at least 30 trades, profit factor above 1, drawdown under 40%,
survives the out-of-sample half, and does not lean on one giant trade.
"""
import json
from pathlib import Path
import study.lab.strats
from study.lab import harness, board, data

BASE = {"lookback":20,"atr_period":14,"atr_average":20,"trend_period":200,
        "trade_shorts":True}
VARIANTS = {
 "A": dict(strategy="VOL_BREAKOUT_TREND", allow_short=True,
           stop_loss_atr=2.0, take_profit_atr=3.0,
           strategy_parameters={**BASE, "exit_on_trend_cross": False}),
 "B": dict(strategy="VOL_BREAKOUT_TREND", allow_short=True, stop_loss_atr=2.0,
           strategy_parameters={**BASE, "exit_on_trend_cross": True}),
}
TFS = ("5m","15m","30m","1h","2h","4h","6h","8h","12h","1d")
SYMS = ("/NQ","/GC","/CL")


def main():
    out = {}
    for v, spec in VARIANTS.items():
        rows = []
        for sym in SYMS:
            for tf in TFS:
                c = data.load(sym, tf); cut = int(len(c)*0.70)
                f = harness.run(sym, tf, spec, c)
                o = harness.run(sym, tf, spec, c[cut:])
                board.add(batch=f"frozen-5{v}", symbol=sym, tf=tf,
                          name=f"Vol breakout + trend regime, exit {v}",
                          spec=spec, result=f)
                rows.append(dict(symbol=sym, tf=tf, trades=f["trades"], net=f["net"],
                    bh=f["bh"], vsBH=f["vsBH"], beat=f["beatsBH"], pf=f["pf"],
                    win=f["winRate"], dd=f["maxDDPct"], sharpe=f["sharpe"],
                    bigWin=f["bigWin"], bigLoss=f["bigLoss"], tim=f["timeInMarket"],
                    top1=f["topTradeShare"], oosVsBH=o["vsBH"], oosBeat=o["beatsBH"],
                    oosNet=o["net"], oosTrades=o["trades"], oosPF=o["pf"]))
            print(f"  variant {v}: {sym} done", flush=True)
        out[v] = rows
    Path("study/lab/frozen5.json").write_text(json.dumps(out, indent=1))

    sh = lambda x: f"{x:.2f}" if x is not None else "n/a"
    for v, rows in out.items():
        print(f"\n\n  ======== VARIANT {v} ========\n")
        print(f"  {'Inst':<5}{'TF':<5}{'Tr':>5}{'Net P&L':>10}{'B&H':>11}{'Vs B&H':>11}"
              f"{'PF':>6}{'Win%':>6}{'MaxDD':>7}{'Shrp':>7}{'BigWin':>9}{'BigLoss':>9}"
              f"{'TiM%':>6}{'top1%':>7}{'OOS vsB&H':>11}  verdict")
        for r in rows:
            print(f"  {r['symbol']:<5}{r['tf']:<5}{r['trades']:>5}{r['net']:>10,.0f}"
                  f"{r['bh']:>11,.0f}{r['vsBH']:>11,.0f}{(r['pf'] or 0):>6.2f}"
                  f"{r['win']:>5.1f}%{abs(r['dd']):>6.1f}%{sh(r['sharpe']):>7}"
                  f"{r['bigWin']:>9,.0f}{r['bigLoss']:>9,.0f}{r['tim']:>5.1f}%"
                  f"{r['top1']:>6.1f}%{r['oosVsBH']:>11,.0f}  "
                  f"{'BEAT B&H' if r['beat'] else 'DID NOT BEAT'}")
        d = {(r['symbol'],r['tf']): r for r in rows}
        for title, key, fmt in (("VS BUY & HOLD", 'vsBH', lambda x: f"{x:,.0f}"),
                                ("PROFIT FACTOR", 'pf', lambda x: f"{(x or 0):.2f}"),
                                ("MAX DRAWDOWN", 'dd', lambda x: f"{abs(x):.1f}%"),
                                ("OOS VS B&H", 'oosVsBH', lambda x: f"{x:,.0f}")):
            print(f"\n  {title}   (variant {v})\n")
            print(f"  {'':<6}" + "".join(f"{t:>11}" for t in TFS))
            for s in SYMS:
                cells=[]
                for t in TFS:
                    r=d[(s,t)]
                    mk = "*" if (key=='vsBH' and r['beat']) or (key=='oosVsBH' and r['oosBeat']) else ""
                    cells.append(mk+fmt(r[key]))
                print(f"  {s:<6}" + "".join(f"{x:>11}" for x in cells))

    print("\n\n  ======== SIX-TEST CANDIDATE SCREEN ========\n")
    print("  beats B&H · 30+ trades · PF > 1 · DD < 40% · survives OOS · top trade < 40%\n")
    for v, rows in out.items():
        real = [r for r in rows if r['bh'] > 1000]
        print(f"  Variant {v}: {sum(1 for r in rows if r['net']>0)}/30 profitable · "
              f"beat {sum(1 for r in real if r['beat'])}/{len(real)} · "
              f"OOS beat {sum(1 for r in real if r['oosBeat'])} · "
              f"BOTH {sum(1 for r in real if r['beat'] and r['oosBeat'])}")
        for r in real:
            checks = [("beats B&H", r['beat']), ("30+ trades", r['trades'] >= 30),
                      ("PF > 1", (r['pf'] or 0) > 1), ("DD < 40%", abs(r['dd']) < 40),
                      ("survives OOS", r['oosBeat']), ("top1 < 40%", r['top1'] < 40)]
            passed = sum(1 for _, ok in checks if ok)
            if passed >= 5:
                fails = [n for n, ok in checks if not ok]
                print(f"      {r['symbol']} {r['tf']:<4}{passed}/6 "
                      f"{'CANDIDATE' if passed == 6 else 'fails: ' + ', '.join(fails)}"
                      f"   ({r['trades']}tr, PF {r['pf']:.2f}, DD {abs(r['dd']):.1f}%, "
                      f"vsB&H {r['vsBH']:+,.0f}, OOS {r['oosVsBH']:+,.0f})")
        print()


if __name__ == "__main__":
    main()
