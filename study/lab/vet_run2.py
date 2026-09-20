"""Gate the two strongest /CL candidates from the baseline sweep."""
import json
from pathlib import Path
import study.lab.strats
from study.lab import vet

REGIME = dict(strategy="TREND_REGIME", allow_short=False,
              strategy_parameters={"regime_period": 200, "buffer_pct": 0.0})
REGIME_V = [("SMA 100", {"regime_period": 100}), ("SMA 150", {"regime_period": 150}),
            ("SMA 250", {"regime_period": 250}), ("SMA 300", {"regime_period": 300}),
            ("buffer 0.25%", {"buffer_pct": 0.25}), ("buffer 0.5%", {"buffer_pct": 0.5}),
            ("buffer 1.0%", {"buffer_pct": 1.0})]

MTF = dict(strategy="MTF_GREEN_STACK", allow_short=True, trailing_stop_atr=2.0,
           strategy_parameters={"tf1_minutes": 240, "tf2_minutes": 480,
                                "tf3_minutes": 960, "require_completed": True,
                                "trade_shorts": True, "exit_on_condition": True})
MTF_V = [("legs 240/720/1440", {"tf2_minutes": 720, "tf3_minutes": 1440}),
         ("legs 240/480/1440", {"tf3_minutes": 1440}),
         ("legs 240/960/1920", {"tf2_minutes": 960, "tf3_minutes": 1920}),
         ("two legs only", {"tf3_minutes": 480})]

if __name__ == "__main__":
    store = {}
    for key, sym, tf, name, spec, var in (
        ("regime_cl_1h", "/CL", "1h", "Trend regime SMA200", REGIME, REGIME_V),
        ("regime_cl_4h", "/CL", "4h", "Trend regime SMA200", REGIME, REGIME_V),
        ("regime_gc_4h", "/GC", "4h", "Trend regime SMA200", REGIME, REGIME_V),
        ("mtf_cl_4h",    "/CL", "4h", "Multi-timeframe EMA trend", MTF, MTF_V)):
        v = vet.vet(sym, tf, name, spec, var)
        store[key] = v
        print(vet.report(v))
    Path("study/lab/vet_batch3.json").write_text(json.dumps(store, indent=1))
