"""Push the buffered regime filter: the long+short version, and whether the
same rule survives on the timeframes either side of the one it was found on."""
import json
from pathlib import Path
import study.lab.strats
from study.lab import vet, harness, board, data

LS = dict(strategy="TREND_REGIME", allow_short=True,
          strategy_parameters={"regime_period": 200, "buffer_pct": 0.5})
V = [("buffer 0.25%", {"buffer_pct": 0.25}), ("buffer 0.75%", {"buffer_pct": 0.75}),
     ("buffer 1.0%", {"buffer_pct": 1.0}), ("buffer 1.5%", {"buffer_pct": 1.5}),
     ("SMA 150", {"regime_period": 150}), ("SMA 250", {"regime_period": 250}),
     ("SMA 300", {"regime_period": 300})]

if __name__ == "__main__":
    print("  Same rule, every market and timeframe — does it travel?")
    for sym in ("/NQ", "/GC", "/CL"):
        for tf in ("30m", "1h", "4h"):
            c = data.load(sym, tf)
            for lbl, sh in (("long/flat", False), ("long+short", True)):
                s = json.loads(json.dumps(LS)); s["allow_short"] = sh
                r = harness.run(sym, tf, s, c)
                n = board.add(batch="buffered-regime", symbol=sym, tf=tf,
                              name=f"Regime SMA200 +0.5% band {lbl}", spec=s, result=r)
                print(board.line(n, sym, tf, f"Regime +0.5% band {lbl}", r))

    v = vet.vet("/CL", "1h", "Regime SMA200 + 0.5% band, long+short", LS, V)
    print(vet.report(v))
    Path("study/lab/vet_batch5.json").write_text(json.dumps({"ls_cl_1h": v}, indent=1))
