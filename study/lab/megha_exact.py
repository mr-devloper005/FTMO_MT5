"""The specification exactly as written, on every timeframe. Nothing altered.

A  daily close above the 200 SMA
B  at least 1% above it
C  a bullish candle closing above that buffer
D  not more than 5% above the SMA
E  not chasing above the previous sell price
   hold while the close stays above the SMA
   sell on a close below it (section 4 also offers a one-day confirmation,
   so both of the specification's own exits are run)

Every condition is on. Long only. One contract.
"""
import json
from pathlib import Path
import study.lab.strats
from study.lab import harness, board, data

TFS = ("5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d")
SPAN = {"5m":"16 hours","15m":"2 days","30m":"4 days","1h":"8 days","2h":"17 days",
        "4h":"33 days","6h":"50 days","8h":"67 days","12h":"100 days","1d":"200 days"}


def spec(confirm):
    return dict(strategy="DAILY_SWING_SMA200", allow_short=False,
                strategy_parameters={
                    "sma_period": 200,          # 1. the 200 SMA
                    "buffer_pct": 1.0,          # 2B. at least 1% above
                    "max_extension_pct": 5.0,   # 2D. not more than 5% above
                    "require_bullish_candle": True,   # 2C. bullish candle
                    "use_last_sell_filter": True,     # 2E. previous sell filter
                    "chase_pct": 2.0,
                    "exit_confirm_days": confirm,     # 4. exit, both versions
                    "trade_shorts": False})           # long only


def main():
    rows = []
    print("  The specification exactly as written — all of A, B, C, D and E\n")
    print(f"  {'cell':<9}{'bars':>7}{'200 bars':>10}{'exit':<8}{'tr':>4}{'net':>12}"
          f"{'B&H':>12}{'vs B&H':>12}{'PF':>6}{'DD%':>7}{'B&H DD':>8}{'TiM%':>7}")
    for sym in ("/NQ", "/GC", "/CL"):
        for tf in TFS:
            c = data.load(sym, tf)
            for label, confirm in (("same day", 0), ("+1 day", 1)):
                r = harness.run(sym, tf, spec(confirm), c)
                board.add(batch="megha-exact", symbol=sym, tf=tf,
                          name=f"200-SMA swing exact ({label} exit)",
                          spec=spec(confirm), result=r)
                rows.append(dict(symbol=sym, tf=tf, exit=label, span=SPAN[tf], **{
                    k: r[k] for k in ("trades","net","bh","vsBH","beatsBH","pf","winRate",
                                      "maxDDPct","bhDDPct","timeInMarket","topTradeShare",
                                      "bars","returnPct","bhReturnPct","sharpe")}))
                print(f"  {sym+' '+tf:<9}{len(c):>7,}{SPAN[tf]:>10}{label:<8}"
                      f"{r['trades']:>4}{r['net']:>12,.0f}{r['bh']:>12,.0f}"
                      f"{r['vsBH']:>12,.0f}{(r['pf'] or 0):>6.2f}{abs(r['maxDDPct']):>7.1f}"
                      f"{abs(r['bhDDPct']):>8.1f}{r['timeInMarket']:>7.1f}"
                      + ("  BEAT" if r["beatsBH"] else ""), flush=True)
        print()
    Path("study/lab/megha_exact.json").write_text(json.dumps(rows, indent=1))
    b = [r for r in rows if r["beatsBH"]]
    real = [r for r in b if r["bh"] > 1000]
    print(f"  {len(rows)} runs · {len(b)} beat buy & hold · {len(real)} of those "
          f"against a benchmark above $1,000")


if __name__ == "__main__":
    main()
