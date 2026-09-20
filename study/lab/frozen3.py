"""Strategy 3, frozen, under two exit structures.

The entry rule is identical in every run. Only the exit differs between the two
variants, and only the market and timeframe differ within a variant.

  A  stop 1.5 x ATR, target 3 x ATR
  B  stop 1.5 x ATR, breakeven at +1 ATR, 2 x ATR trail armed at +2 ATR
"""
import json
from pathlib import Path
import study.lab.strats
from study.lab import harness, board, data

PARAMS = {"fast_period":20,"mid_period":50,"slow_period":200,"rsi_period":14,
          "rsi_long_above":55.0,"rsi_short_below":45.0,"macd_fast":12,
          "macd_slow":26,"macd_signal":9,"atr_period":14,"atr_average":20,
          "dip_lookback":10,"trade_shorts":True}

VARIANTS = {
  "A": dict(strategy="TREND_PULLBACK_STACK", allow_short=True,
            stop_loss_atr=1.5, take_profit_atr=3.0, strategy_parameters=dict(PARAMS)),
  "B": dict(strategy="TREND_PULLBACK_STACK", allow_short=True,
            stop_loss_atr=1.5, breakeven_at_atr=1.0, trailing_stop_atr=2.0,
            trail_activate_atr=2.0, strategy_parameters=dict(PARAMS)),
}
TFS = ("5m","15m","30m","1h","2h","4h","6h","8h","12h","1d")
SYMS = ("/NQ","/GC","/CL")


def main():
    out = {}
    for v, spec in VARIANTS.items():
        rows = []
        for sym in SYMS:
            for tf in TFS:
                c = data.load(sym, tf)
                cut = int(len(c) * 0.70)
                f = harness.run(sym, tf, spec, c)
                o = harness.run(sym, tf, spec, c[cut:])
                board.add(batch=f"frozen-3{v}", symbol=sym, tf=tf,
                          name=f"Trend pullback stack, exit {v}", spec=spec, result=f)
                rows.append(dict(symbol=sym, tf=tf, bars=len(c),
                    trades=f["trades"], net=f["net"], bh=f["bh"], vsBH=f["vsBH"],
                    beat=f["beatsBH"], pf=f["pf"], win=f["winRate"], dd=f["maxDDPct"],
                    bhdd=f["bhDDPct"], sharpe=f["sharpe"], tim=f["timeInMarket"],
                    bigWin=f["bigWin"], bigLoss=f["bigLoss"], comm=f["commission"],
                    top1=f["topTradeShare"],
                    oosNet=o["net"], oosBH=o["bh"], oosVsBH=o["vsBH"],
                    oosBeat=o["beatsBH"], oosTrades=o["trades"], oosPF=o["pf"]))
            print(f"  variant {v}: {sym} done", flush=True)
        out[v] = rows
    Path("study/lab/frozen3.json").write_text(json.dumps(out, indent=1))

    sh = lambda x: f"{x:.2f}" if x is not None else "n/a"
    for v, rows in out.items():
        print(f"\n\n  ============ EXIT VARIANT {v} ============\n")
        print(f"  {'Inst':<5}{'TF':<5}{'Tr':>5}{'Net P&L':>10}{'B&H':>11}{'Vs B&H':>11}"
              f"{'PF':>6}{'Win%':>6}{'MaxDD':>7}{'Shrp':>7}{'TiM%':>6}"
              f"{'BigWin':>9}{'BigLoss':>9}{'Comm':>7}{'OOS vs B&H':>12}  verdict")
        for r in rows:
            print(f"  {r['symbol']:<5}{r['tf']:<5}{r['trades']:>5}{r['net']:>10,.0f}"
                  f"{r['bh']:>11,.0f}{r['vsBH']:>11,.0f}{(r['pf'] or 0):>6.2f}"
                  f"{r['win']:>5.1f}%{abs(r['dd']):>6.1f}%{sh(r['sharpe']):>7}"
                  f"{r['tim']:>5.1f}%{r['bigWin']:>9,.0f}{r['bigLoss']:>9,.0f}"
                  f"{r['comm']:>7,.0f}{r['oosVsBH']:>12,.0f}"
                  f"  {'BEAT B&H' if r['beat'] else 'DID NOT BEAT'}")
        d = {(r['symbol'], r['tf']): r for r in rows}
        for title, key, fmt in (("VS BUY & HOLD", 'vsBH', lambda x: f"{x:,.0f}"),
                                ("PROFIT FACTOR", 'pf', lambda x: f"{(x or 0):.2f}"),
                                ("OOS VS B&H", 'oosVsBH', lambda x: f"{x:,.0f}")):
            print(f"\n  {title}   (variant {v})\n")
            print(f"  {'':<6}" + "".join(f"{t:>11}" for t in TFS))
            for s in SYMS:
                cells = []
                for t in TFS:
                    r = d[(s, t)]
                    mk = "*" if (key == 'vsBH' and r['beat']) or \
                                (key == 'oosVsBH' and r['oosBeat']) else ""
                    cells.append(mk + fmt(r[key]))
                print(f"  {s:<6}" + "".join(f"{x:>11}" for x in cells))


if __name__ == "__main__":
    main()
