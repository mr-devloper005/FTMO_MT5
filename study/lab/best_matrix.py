"""The best strategy alone, on every timeframe and every market.

EMA crossover + RSI + candlestick pattern, the configuration that survived the
out-of-sample gate on /CL daily, run unchanged everywhere else. Nothing is
re-tuned per cell — that is the point: a rule that only works where it was
found is not a strategy, it is a coincidence.
"""
import json
from pathlib import Path
import study.lab.strats
from study.lab import harness, board, data

TFS = ("5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d")


def spec(shorts):
    return dict(strategy="EMA_RSI_PATTERN", allow_short=shorts,
                stop_loss_atr=2.0, take_profit_atr=3.0,
                strategy_parameters={"fast_period": 20, "slow_period": 50,
                                     "rsi_buy_above": 50.0, "rsi_sell_below": 50.0,
                                     "require_pattern": True})


def main():
    rows = []
    print("  EMA crossover + RSI  ·  EMA 20/50 · RSI 50 · pattern required\n"
          "  stop 2xATR · target 3xATR · 1 contract · $2.50 commission · 1 tick slippage\n")
    print(f"  {'cell':<9}{'bars':>7}{'dir':<7}{'tr':>5}{'net':>12}{'B&H':>12}"
          f"{'vs B&H':>12}{'PF':>6}{'DD%':>7}{'B&H DD':>8}{'win%':>7}{'top1%':>7}")
    for sym in ("/NQ", "/GC", "/CL"):
        for tf in TFS:
            c = data.load(sym, tf)
            for label, sh in (("L/S", True), ("long", False)):
                r = harness.run(sym, tf, spec(sh), c)
                board.add(batch="best-matrix", symbol=sym, tf=tf,
                          name=f"EMA crossover + RSI ({label})", spec=spec(sh), result=r)
                rows.append(dict(symbol=sym, tf=tf, dir=label, **{
                    k: r[k] for k in ("trades","net","bh","vsBH","beatsBH","pf","winRate",
                                      "maxDDPct","bhDDPct","sharpe","timeInMarket",
                                      "topTradeShare","bars","returnPct","bhReturnPct")}))
                print(f"  {sym+' '+tf:<9}{len(c):>7,}{label:<7}{r['trades']:>5}"
                      f"{r['net']:>12,.0f}{r['bh']:>12,.0f}{r['vsBH']:>12,.0f}"
                      f"{(r['pf'] or 0):>6.2f}{abs(r['maxDDPct']):>7.1f}"
                      f"{abs(r['bhDDPct']):>8.1f}{r['winRate']:>7.1f}"
                      f"{r['topTradeShare']:>7.1f}" + ("  BEAT" if r["beatsBH"] else ""),
                      flush=True)
        print()
    Path("study/lab/best_matrix.json").write_text(json.dumps(rows, indent=1))
    b = [r for r in rows if r["beatsBH"]]
    real = [r for r in b if r["bh"] > 1000]
    print(f"  {len(rows)} runs · {len(b)} beat buy & hold · "
          f"{len(real)} of those against a benchmark worth beating")


if __name__ == "__main__":
    main()
