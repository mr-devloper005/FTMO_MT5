"""What ftmo_mt5.lab.predict currently says for all 14 semiconductor names.

Ad-hoc research script (repo convention: study/lab/*.py). Reads only cached
data (cached_only=True) - no network calls, no new backtests beyond what
predict.for_symbol() runs internally to re-score each cached timeframe.
"""
import json

from ftmo_mt5.lab import predict

SYMBOLS = ["AMAT", "AMD", "ARM", "CRDO", "DELL", "LITE", "MRVL", "MU",
           "NBIS", "NVDA", "SMH", "SNDK", "TSM", "WDC"]
TFS = ["5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d", "1w"]

rows = predict.run(SYMBOLS, TFS, 600, cached_only=True)
json.dump(rows, open("study/lab/semis_predict_results.json", "w"), indent=2, default=str)

for r in rows:
    print(f"{r['symbol']:<6} tested={r['tested']:<3} confidence={r.get('confidence'):<10} "
          f"stance={r.get('stance')!s:<6} strategy={r.get('strategy')!s:<20} "
          f"tf={r.get('timeframe')!s:<4} vsBH={r.get('vsBH')}")
