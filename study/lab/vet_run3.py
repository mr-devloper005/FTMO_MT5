"""Gate the buffered regime filter and the new dual-timeframe version on /CL."""
import json
from pathlib import Path
import study.lab.strats
from study.lab import vet, harness, board, data

BUF = dict(strategy="TREND_REGIME", allow_short=False,
           strategy_parameters={"regime_period": 200, "buffer_pct": 0.5})
BUF_V = [("buffer 0.25%", {"buffer_pct": 0.25}), ("buffer 0.75%", {"buffer_pct": 0.75}),
         ("buffer 1.0%", {"buffer_pct": 1.0}), ("buffer 1.5%", {"buffer_pct": 1.5}),
         ("SMA 150", {"regime_period": 150}), ("SMA 250", {"regime_period": 250}),
         ("SMA 300", {"regime_period": 300})]

DUAL = dict(strategy="DUAL_REGIME", allow_short=False,
            strategy_parameters={"period": 200, "buffer_pct": 0.5,
                                 "higher_minutes": 240, "higher_period": 100,
                                 "trade_shorts": False})
DUAL_V = [("buffer 0.25%", {"buffer_pct": 0.25}), ("buffer 1.0%", {"buffer_pct": 1.0}),
          ("SMA 150", {"period": 150}), ("SMA 250", {"period": 250}),
          ("higher 8h", {"higher_minutes": 480}), ("higher 1d", {"higher_minutes": 1440}),
          ("higher SMA 50", {"higher_period": 50}), ("higher SMA 200", {"higher_period": 200})]

if __name__ == "__main__":
    # First, where the new idea stands across every market and timeframe.
    print("  DUAL_REGIME across the matrix")
    for sym in ("/NQ", "/GC", "/CL"):
        for tf in ("30m", "1h", "4h"):
            hm = {"30m": 240, "1h": 240, "4h": 1440}[tf]
            s = json.loads(json.dumps(DUAL)); s["strategy_parameters"]["higher_minutes"] = hm
            c = data.load(sym, tf)
            for lbl, sh in (("long/flat", False), ("long+short", True)):
                s2 = json.loads(json.dumps(s))
                s2["allow_short"] = sh; s2["strategy_parameters"]["trade_shorts"] = sh
                r = harness.run(sym, tf, s2, c)
                n = board.add(batch="dual-regime", symbol=sym, tf=tf,
                              name=f"Dual regime {lbl}", spec=s2, result=r)
                print(board.line(n, sym, tf, f"Dual regime {lbl}", r))

    store = {}
    for key, sym, tf, name, spec, var in (
        ("buf_cl_1h",  "/CL", "1h", "Trend regime SMA200 + 0.5% band", BUF, BUF_V),
        ("dual_cl_1h", "/CL", "1h", "Dual-timeframe regime (1h + 4h)", DUAL, DUAL_V)):
        v = vet.vet(sym, tf, name, spec, var)
        store[key] = v
        print(vet.report(v))
    Path("study/lab/vet_batch4.json").write_text(json.dumps(store, indent=1))
