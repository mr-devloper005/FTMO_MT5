"""The 200-SMA swing specification alone, across every timeframe and market.

Only this rule is run. Two readings of it are measured side by side: the
specification exactly as written, and the same rule with condition E (the
previous-sell-price filter) switched off.

A note the numbers cannot carry on their own: the specification says "200-DAY
SMA" and "daily closing prices". On any timeframe other than 1d that becomes a
200-BAR average, which is a different rule — 200 bars is 16 hours on 5m and
100 days on 12h. The daily row is the specification; the others are the shape
of the rule applied elsewhere.
"""
import json
from pathlib import Path
import study.lab.strats
from study.lab import harness, board, data

TFS = ("5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d")
SPAN = {"5m": "16 hours", "15m": "2 days", "30m": "4 days", "1h": "8 days",
        "2h": "17 days", "4h": "33 days", "6h": "50 days", "8h": "67 days",
        "12h": "100 days", "1d": "200 days"}


def spec(e_on):
    return dict(strategy="DAILY_SWING_SMA200", allow_short=False,
                strategy_parameters={"sma_period": 200, "buffer_pct": 1.0,
                                     "max_extension_pct": 5.0,
                                     "require_bullish_candle": True,
                                     "use_last_sell_filter": e_on,
                                     "chase_pct": 2.0, "exit_confirm_days": 0})


def main():
    rows = []
    print("  The 200-SMA swing rule, alone, on every timeframe\n")
    print(f"  {'cell':<9}{'bars':>7}{'200 bars =':>12}{'E':<5}{'tr':>5}{'net':>12}"
          f"{'B&H':>12}{'vs B&H':>12}{'PF':>6}{'DD%':>7}{'B&H DD':>8}{'TiM%':>7}")
    for sym in ("/NQ", "/GC", "/CL"):
        for tf in TFS:
            c = data.load(sym, tf)
            for e_on in (True, False):
                s = spec(e_on)
                r = harness.run(sym, tf, s, c)
                board.add(batch="megha-only", symbol=sym, tf=tf,
                          name=f"200-SMA swing (E {'on' if e_on else 'off'})",
                          spec=s, result=r)
                rows.append(dict(symbol=sym, tf=tf, eOn=e_on, span=SPAN[tf], **{
                    k: r[k] for k in ("trades","net","bh","vsBH","beatsBH","pf","winRate",
                                      "maxDDPct","bhDDPct","sharpe","timeInMarket",
                                      "topTradeShare","bars","returnPct","bhReturnPct",
                                      "avgTrade","bigWin","bigLoss","commission")}))
                print(f"  {sym+' '+tf:<9}{len(c):>7,}{SPAN[tf]:>12}"
                      f"{'on' if e_on else 'off':<5}{r['trades']:>5}{r['net']:>12,.0f}"
                      f"{r['bh']:>12,.0f}{r['vsBH']:>12,.0f}{(r['pf'] or 0):>6.2f}"
                      f"{abs(r['maxDDPct']):>7.1f}{abs(r['bhDDPct']):>8.1f}"
                      f"{r['timeInMarket']:>7.1f}" + ("  BEAT" if r["beatsBH"] else ""),
                      flush=True)
        print()
    Path("study/lab/megha_matrix.json").write_text(json.dumps(rows, indent=1))
    b = [r for r in rows if r["beatsBH"]]
    print(f"  {len(rows)} runs · {len(b)} beat buy & hold")


if __name__ == "__main__":
    main()
