"""Strategy 2, frozen: volatility breakout with a trend filter.

BUY   close above the previous 20-bar high, ATR above its 20-bar average,
      close above EMA 200
SELL  the mirror
EXIT  stop 2 x ATR, target 3 x ATR

One specification object, thirty runs. Nothing varies per market or timeframe.
"""
import json
from pathlib import Path
import study.lab.strats
from study.lab import harness, board, data

SPEC = dict(strategy="VOL_BREAKOUT_TREND", allow_short=True,
            stop_loss_atr=2.0, take_profit_atr=3.0,
            strategy_parameters={"lookback": 20, "atr_period": 14,
                                 "atr_average": 20, "trend_period": 200,
                                 "trade_shorts": True})
NAME = "Volatility breakout + trend filter"
TFS = ("5m","15m","30m","1h","2h","4h","6h","8h","12h","1d")
SYMS = ("/NQ","/GC","/CL")


def main():
    rows = []
    for sym in SYMS:
        for tf in TFS:
            c = data.load(sym, tf)
            cut = int(len(c) * 0.70)
            f = harness.run(sym, tf, SPEC, c)
            o = harness.run(sym, tf, SPEC, c[cut:])
            board.add(batch="frozen-2", symbol=sym, tf=tf, name=NAME, spec=SPEC, result=f)
            rows.append(dict(symbol=sym, tf=tf, bars=len(c),
                trades=f["trades"], net=f["net"], bh=f["bh"], vsBH=f["vsBH"],
                beat=f["beatsBH"], pf=f["pf"], win=f["winRate"], dd=f["maxDDPct"],
                bhdd=f["bhDDPct"], sharpe=f["sharpe"], tim=f["timeInMarket"],
                top1=f["topTradeShare"],
                oosNet=o["net"], oosBH=o["bh"], oosVsBH=o["vsBH"], oosBeat=o["beatsBH"],
                oosTrades=o["trades"], oosPF=o["pf"]))
        print(f"  {sym} done", flush=True)
    Path("study/lab/frozen2.json").write_text(json.dumps(rows, indent=1))

    sh = lambda v: f"{v:.2f}" if v is not None else "n/a"
    print(f"\n\n  STRATEGY: {NAME}\n")
    print(f"  {'Inst':<5}{'TF':<5}{'Trades':>7}{'Net P&L':>11}{'Buy & Hold':>12}"
          f"{'Vs B&H':>11}{'PF':>7}{'Win%':>7}{'MaxDD':>8}{'Sharpe':>8}"
          f"{'OOS vs B&H':>12}{'OOS PF':>8}")
    for r in rows:
        print(f"  {r['symbol']:<5}{r['tf']:<5}{r['trades']:>7}{r['net']:>11,.0f}"
              f"{r['bh']:>12,.0f}{r['vsBH']:>11,.0f}{(r['pf'] or 0):>7.2f}"
              f"{r['win']:>6.1f}%{abs(r['dd']):>7.1f}%{sh(r['sharpe']):>8}"
              f"{r['oosVsBH']:>12,.0f}{(r['oosPF'] or 0):>8.2f}"
              + ("  BEAT" if r["beat"] else ""))

    d = {(r['symbol'], r['tf']): r for r in rows}
    for title, key, fmt in (("VS BUY & HOLD", 'vsBH', lambda v: f"{v:,.0f}"),
                            ("NET P&L", 'net', lambda v: f"{v:,.0f}"),
                            ("PROFIT FACTOR", 'pf', lambda v: f"{(v or 0):.2f}"),
                            ("MAX DRAWDOWN %", 'dd', lambda v: f"{abs(v):.1f}%"),
                            ("TRADES", 'trades', lambda v: f"{v}"),
                            ("OOS VS BUY & HOLD", 'oosVsBH', lambda v: f"{v:,.0f}")):
        print(f"\n\n  {title}\n")
        print(f"  {'':<6}" + "".join(f"{t:>11}" for t in TFS))
        for s in SYMS:
            cells = []
            for t in TFS:
                r = d[(s, t)]
                mark = "*" if (key == 'vsBH' and r['beat']) or \
                              (key == 'oosVsBH' and r['oosBeat']) else ""
                cells.append(mark + fmt(r[key]))
            print(f"  {s:<6}" + "".join(f"{x:>11}" for x in cells))


if __name__ == "__main__":
    main()
