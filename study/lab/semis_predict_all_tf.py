"""ftmo_mt5.lab.predict, forced to one timeframe at a time -- all 14 symbols x
all 11 timeframes (154 cells), instead of letting it pick its own best
timeframe per symbol like the last run did.

Ad-hoc research script (repo convention: study/lab/*.py). Cached data only,
no network calls, allow_short=True throughout to match every other test in
this session.
"""
import json

from ftmo_mt5.lab import predict, windows

SYMBOLS = ["AMAT", "AMD", "ARM", "CRDO", "DELL", "LITE", "MRVL", "MU",
           "NBIS", "NVDA", "SMH", "SNDK", "TSM", "WDC"]
TFS = windows.TIMEFRAMES
BARS = 600

rows = []
for symbol in SYMBOLS:
    for tf in TFS:
        r = predict.for_symbol(symbol, [tf], BARS, base={"allow_short": True}, cached_only=True)
        rows.append(r)
        print(f"{symbol:<6}{tf:<5} confidence={r.get('confidence'):<10} "
              f"stance={str(r.get('stance')):<6} strategy={str(r.get('strategy')):<20} "
              f"trades={r.get('trades')!s:<4} vsBHPct={r.get('vsBHPct')}", flush=True)

json.dump(rows, open("study/lab/semis_predict_all_tf_results.json", "w"), indent=2, default=str)
print(f"\nDONE {len(rows)} cells")
