import json, sys
from pathlib import Path
import study.lab.strats
from study.lab import vet

ADX_VARIANTS = [("trend_above 20", {"trend_above": 20.0}),
                ("trend_above 30", {"trend_above": 30.0}),
                ("range_below 15", {"range_below": 15.0}),
                ("range_below 25", {"range_below": 25.0}),
                ("entry_z 1.5", {"entry_z": 1.5}),
                ("entry_z 2.5", {"entry_z": 2.5}),
                ("EMA 10/30", {"fast": 10, "slow": 30}),
                ("EMA 30/80", {"fast": 30, "slow": 80})]

SPEC = dict(strategy="ADX_SWITCH", allow_short=True, stop_loss_atr=3.0,
            strategy_parameters={"trend_above": 25.0, "range_below": 20.0,
                                 "entry_z": 2.0, "exit_z": 0.3, "trade_shorts": True})

if __name__ == "__main__":
    store = {}
    for tf in ("30m", "1h", "4h"):
        v = vet.vet("/CL", tf, "ADX regime switch", SPEC, ADX_VARIANTS)
        store[tf] = v
        print(vet.report(v))
    Path("study/lab/vet_adx_cl.json").write_text(json.dumps(store, indent=1))
