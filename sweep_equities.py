"""14 semiconductor names x every strategy x every timeframe, on defaults.

Position size is set per symbol and timeframe so each run trades roughly
$100,000 of stock. One share of a $120 name moves the account by tens of
dollars, which would put every buy-and-hold benchmark under the lab's
flat-benchmark floor and get the whole sweep dismissed as "no opponent" —
a unit problem masquerading as a finding.
"""
import json, sys, time

from ftmo_mt5.lab import metrics, service, windows

SYMBOLS = ["AMAT", "AMD", "ARM", "CRDO", "DELL", "LITE", "MRVL", "MU",
           "NBIS", "NVDA", "SMH", "SNDK", "TSM", "WDC"]
TFS = windows.TIMEFRAMES
BARS = 600
NOTIONAL = 100_000.0

defaults = service.strategy_defaults()
rows, started = [], time.time()

for symbol in SYMBOLS:
    for tf in TFS:
        try:
            w = windows.window(symbol, tf, BARS)
            candles = w["candles"]
            if not candles:
                raise ValueError("provider returned no bars")
            size = max(1, round(NOTIONAL / candles[0].close))
        except Exception as e:
            print(f"{symbol:<6}{tf:<5} SKIP  {type(e).__name__}: {e}", flush=True)
            continue

        beat = 0
        for name, params in defaults.items():
            spec = {"strategy": name, "strategy_parameters": dict(params),
                    "position_size": float(size),
                    "allow_long": True, "allow_short": True}
            try:
                r = metrics.measure(symbol, tf, spec, candles)
            except Exception as e:
                rows.append({"symbol": symbol, "timeframe": tf, "strategy": name,
                             "error": f"{type(e).__name__}: {e}"})
                continue
            r["shares"] = size
            rows.append(r)
            beat += bool(r["beatsBH"] and r["trades"])
        print(f"{symbol:<6}{tf:<5} {len(candles):>4} bars  {size:>5} sh  "
              f"{beat:>2}/14 beat   [{len(rows)} runs, {time.time()-started:.0f}s]",
              flush=True)

json.dump(rows, open("/tmp/equities.json", "w"))
print(f"\nDONE {len(rows)} runs in {time.time()-started:.0f}s")
