"""Strategy 1 with a 100-bar regime average instead of 200. Nothing else moves.

The 200-bar run is reloaded from its own results file rather than re-derived,
so the comparison column is the same numbers reported before.
"""
import json
from pathlib import Path
import study.lab.strats
from study.lab import harness, board, data

SPEC = dict(strategy="TREND_REGIME", allow_short=False,
            strategy_parameters={"regime_period": 100, "buffer_pct": 0.0})
TFS = ("5m","15m","30m","1h","2h","4h","6h","8h","12h","1d")
SYMS = ("/NQ","/GC","/CL")


def main():
    old = {(r["tf"], r["symbol"]): r for r in json.load(open("study/lab/frozen1.json"))}
    rows = []
    for sym in SYMS:
        for tf in TFS:
            c = data.load(sym, tf); cut = int(len(c) * 0.70)
            f = harness.run(sym, tf, SPEC, c)
            o = harness.run(sym, tf, SPEC, c[cut:])
            board.add(batch="frozen-1b", symbol=sym, tf=tf,
                      name="Trend regime SMA100, long or flat", spec=SPEC, result=f)
            rows.append(dict(symbol=sym, tf=tf, trades=f["trades"], net=f["net"],
                bh=f["bh"], vsBH=f["vsBH"], beat=f["beatsBH"], pf=f["pf"],
                win=f["winRate"], dd=f["maxDDPct"], sharpe=f["sharpe"],
                tim=f["timeInMarket"], top1=f["topTradeShare"],
                oosVsBH=o["vsBH"], oosBeat=o["beatsBH"], oosPF=o["pf"],
                oosNet=o["net"], oosTrades=o["trades"]))
        print(f"  {sym} done", flush=True)
    Path("study/lab/frozen1b.json").write_text(json.dumps(rows, indent=1))

    sh = lambda x: f"{x:.2f}" if x is not None else "n/a"
    print("\n\n  TREND REGIME SMA 100, long or flat\n")
    print(f"  {'Inst':<5}{'TF':<5}{'Trades':>7}{'Net P&L':>11}{'Buy & Hold':>12}"
          f"{'Vs B&H':>11}{'PF':>7}{'Win%':>7}{'MaxDD':>8}{'Sharpe':>8}"
          f"{'TiM%':>7}{'OOS vs B&H':>12}  verdict")
    for r in rows:
        print(f"  {r['symbol']:<5}{r['tf']:<5}{r['trades']:>7}{r['net']:>11,.0f}"
              f"{r['bh']:>12,.0f}{r['vsBH']:>11,.0f}{(r['pf'] or 0):>7.2f}"
              f"{r['win']:>6.1f}%{abs(r['dd']):>7.1f}%{sh(r['sharpe']):>8}"
              f"{r['tim']:>6.1f}%{r['oosVsBH']:>12,.0f}  "
              f"{'BEAT B&H' if r['beat'] else 'DID NOT BEAT'}")

    d = {(r['symbol'], r['tf']): r for r in rows}
    for title, key, fmt in (("VS BUY & HOLD", 'vsBH', lambda x: f"{x:,.0f}"),
                            ("NET P&L", 'net', lambda x: f"{x:,.0f}"),
                            ("PROFIT FACTOR", 'pf', lambda x: f"{(x or 0):.2f}"),
                            ("MAX DRAWDOWN", 'dd', lambda x: f"{abs(x):.1f}%"),
                            ("TRADES", 'trades', lambda x: f"{x}"),
                            ("OOS VS B&H", 'oosVsBH', lambda x: f"{x:,.0f}")):
        print(f"\n  {title}   (SMA 100)\n")
        print(f"  {'':<6}" + "".join(f"{t:>11}" for t in TFS))
        for s in SYMS:
            cells = []
            for t in TFS:
                r = d[(s, t)]
                mk = "*" if (key == 'vsBH' and r['beat']) or \
                            (key == 'oosVsBH' and r['oosBeat']) else ""
                cells.append(mk + fmt(r[key]))
            print(f"  {s:<6}" + "".join(f"{x:>11}" for x in cells))

    print("\n\n  SMA 100 vs SMA 200 — net P&L, side by side\n")
    print(f"  {'cell':<9}{'SMA200 net':>12}{'SMA100 net':>12}{'change':>12}"
          f"{'SMA200 vsB&H':>14}{'SMA100 vsB&H':>14}")
    better = worse = 0
    for r in rows:
        o = old[(r['tf'], r['symbol'])]
        ch = r['net'] - o['net']
        better += ch > 0; worse += ch < 0
        print(f"  {r['symbol']+' '+r['tf']:<9}{o['net']:>12,.0f}{r['net']:>12,.0f}"
              f"{ch:>+12,.0f}{o['vsBH']:>14,.0f}{r['vsBH']:>14,.0f}")
    real = [r for r in rows if r['bh'] > 1000]
    oreal = [old[(r['tf'], r['symbol'])] for r in real]
    print(f"\n  SMA100 better in {better} of 30 cells, worse in {worse}")
    print(f"  {'':<26}{'SMA 200':>12}{'SMA 100':>12}")
    for lbl, fa, fb in (
        ("profitable cells", lambda: f"{sum(1 for r in old.values() if r['net']>0)}/30",
                             lambda: f"{sum(1 for r in rows if r['net']>0)}/30"),
        ("beat B&H (real cells)", lambda: f"{sum(1 for r in oreal if r['beat'])}/{len(oreal)}",
                                  lambda: f"{sum(1 for r in real if r['beat'])}/{len(real)}"),
        ("OOS beat", lambda: str(sum(1 for r in oreal if r['oosBeat'])),
                     lambda: str(sum(1 for r in real if r['oosBeat']))),
        ("beat BOTH", lambda: str(sum(1 for r in oreal if r['beat'] and r['oosBeat'])),
                      lambda: str(sum(1 for r in real if r['beat'] and r['oosBeat']))),
        ("median profit factor", lambda: f"{sorted((r['pf'] or 0) for r in old.values())[15]:.2f}",
                                 lambda: f"{sorted((r['pf'] or 0) for r in rows)[15]:.2f}"),
        ("median max drawdown", lambda: f"{sorted(abs(r['dd']) for r in old.values())[15]:.1f}%",
                                lambda: f"{sorted(abs(r['dd']) for r in rows)[15]:.1f}%"),
        ("median trades", lambda: f"{sorted(r['trades'] for r in old.values())[15]}",
                          lambda: f"{sorted(r['trades'] for r in rows)[15]}"),
        ("total net P&L", lambda: f"{sum(r['net'] for r in old.values()):,.0f}",
                          lambda: f"{sum(r['net'] for r in rows):,.0f}"),
    ):
        print(f"  {lbl:<26}{fa():>12}{fb():>12}")


if __name__ == "__main__":
    main()
