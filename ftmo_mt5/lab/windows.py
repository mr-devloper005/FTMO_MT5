"""Data windows for the lab: fetched once per symbol-and-timeframe, then sliced.

Two rules live here, and they are the reason this module exists rather than
each caller going to the provider itself.

**One download per symbol-and-timeframe.** A matrix asks the same series for
thirty different configurations. Fetching it thirty times would put a data
difference inside a comparison meant to isolate a difference in method, and
Yahoo rate-limits long before thirty downloads anyway.

**Bar counts, not dates.** The research question is almost always "the last
600 bars", not "March through August". A window is therefore a count taken
from the end of the series, and the module reports what it could actually
serve when the provider's history is shorter than the count asked for.

Bars above 1h and the weekly bar are grouped from real 1h and 1d bars.
Nothing here synthesises a price that was never printed.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import structlog

structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.CRITICAL))

from ftmo_mt5.marketdata.base import Candle, CandleSeries
from ftmo_mt5.marketdata.yahoo import YahooHistoricalDataProvider

REPO = Path(__file__).resolve().parents[2]
# The research session's cache, reused rather than re-downloaded. Override to
# keep the server's data separate from a researcher's.
CACHE = Path(os.getenv("LAB_CACHE_DIR", REPO / "study" / "lab" / "cache"))
CACHE.mkdir(parents=True, exist_ok=True)

_PROVIDER = YahooHistoricalDataProvider()

# timeframe -> (provider timeframe fetched, grouping factor, days requested)
BASE: Dict[str, tuple] = {
    "5m":  ("5m",  1, 59),
    "15m": ("15m", 1, 59),
    "30m": ("30m", 1, 59),
    "1h":  ("1h",  1, 725),
    "2h":  ("1h",  2, 725),
    "4h":  ("1h",  4, 725),
    "6h":  ("1h",  6, 725),
    "8h":  ("1h",  8, 725),
    "12h": ("1h", 12, 725),
    "1d":  ("1d",  1, 9600),
    "1w":  ("1d",  5, 9600),
}

SECONDS = {"5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "2h": 7200,
           "4h": 14400, "6h": 21600, "8h": 28800, "12h": 43200,
           "1d": 86400, "1w": 604800}

# What a grouped series is called for the engine, which has no bar size of its
# own for 2h/6h/8h/12h. Strategies read the real timestamps either way.
ENGINE_TF = {"2h": "1h", "4h": "1h", "6h": "1h", "8h": "1h",
             "12h": "1h", "1w": "1d"}

BARS_PER_YEAR = {"5m": 23400, "15m": 7800, "30m": 3900, "1h": 5716, "2h": 2858,
                 "4h": 1550, "6h": 1033, "8h": 775, "12h": 516,
                 "1d": 252, "1w": 52}

# What the provider can serve, stated on every result rather than discovered
# when a window comes back short.
CEILING = {
    "5m":  "Yahoo serves 5m bars for a 60-day trailing window only.",
    "15m": "Yahoo serves 15m bars for a 60-day trailing window only.",
    "30m": "Yahoo serves 30m bars for a 60-day trailing window only.",
    "1h":  "Yahoo serves 1h bars for roughly 730 days.",
    "2h":  "Grouped from 1h bars, so the 730-day 1h ceiling applies.",
    "4h":  "Grouped from 1h bars, so the 730-day 1h ceiling applies.",
    "6h":  "Grouped from 1h bars, so the 730-day 1h ceiling applies.",
    "8h":  "Grouped from 1h bars, so the 730-day 1h ceiling applies.",
    "12h": "Grouped from 1h bars, so the 730-day 1h ceiling applies.",
    "1d":  "Daily history runs back decades; the window is rarely the limit.",
    "1w":  "Grouped from real daily bars.",
}

TIMEFRAMES = list(BASE)

_lock = threading.Lock()
_memory: Dict[tuple, List[Candle]] = {}


def group(candles: List[Candle], seconds: int, factor: int) -> List[Candle]:
    """Clock-aligned grouping. A bucket also flushes on a session gap."""
    if factor <= 1:
        return list(candles)
    out: List[Candle] = []
    bucket: List[Candle] = []

    def flush() -> None:
        if bucket:
            out.append(Candle(
                timestamp=bucket[0].timestamp, open=bucket[0].open,
                high=max(c.high for c in bucket), low=min(c.low for c in bucket),
                close=bucket[-1].close,
                volume=sum(c.volume or 0 for c in bucket)))
            bucket.clear()

    for c in candles:
        if bucket and (int(c.timestamp) // seconds != int(bucket[0].timestamp) // seconds
                       or len(bucket) >= factor):
            flush()
        bucket.append(c)
    flush()
    return out


def _fetch(symbol: str, base_tf: str, days: int) -> List[Candle]:
    key = CACHE / f"{symbol.strip('/')}_{base_tf}_{days}.json"
    if key.exists():
        raw = json.loads(key.read_text())
    else:
        end = date.today()
        got = _PROVIDER.get_history(symbol, base_tf, end - timedelta(days=days), end)
        raw = [[c.timestamp, c.open, c.high, c.low, c.close, c.volume or 0]
               for c in got.candles]
        key.write_text(json.dumps(raw))
    return [Candle(timestamp=t, open=o, high=h, low=lo, close=cl, volume=v)
            for t, o, h, lo, cl, v in raw]


def is_cached(symbol: str, timeframe: str) -> bool:
    """Is this series already on disk or in memory, so reading it costs nothing?

    Callers that promise not to hit the network check this first rather than
    discovering the download by waiting for it.
    """
    if timeframe not in BASE:
        return False
    with _lock:
        if (symbol, timeframe) in _memory:
            return True
    base_tf, _, days = BASE[timeframe]
    return (CACHE / f"{symbol.strip('/')}_{base_tf}_{days}.json").exists()


def cached_symbols() -> List[str]:
    """Every symbol with at least one series already on disk.

    This is what the backtests have pulled, and therefore what a prediction can
    read without going back to the provider.
    """
    found = set()
    for path in CACHE.glob("*_*.json"):
        stem = path.stem
        for base_tf, _, days in BASE.values():
            suffix = f"_{base_tf}_{days}"
            if stem.endswith(suffix):
                found.add(stem[: -len(suffix)])
                break
    return sorted(found)


def load(symbol: str, timeframe: str) -> List[Candle]:
    """Every bar available for one instrument and timeframe.

    Cached on disk across restarts and in memory within one, so a matrix pays
    the download once no matter how many cells read the same series.
    """
    if timeframe not in BASE:
        raise ValueError(f"Unknown timeframe '{timeframe}'. "
                         f"Use one of: {', '.join(BASE)}.")
    memo = (symbol, timeframe)
    with _lock:
        hit = _memory.get(memo)
    if hit is not None:
        return hit
    base_tf, factor, days = BASE[timeframe]
    candles = group(_fetch(symbol, base_tf, days), SECONDS[timeframe], factor)
    with _lock:
        _memory[memo] = candles
    return candles


def window(symbol: str, timeframe: str, bars: Optional[int] = None) -> Dict:
    """The last `bars` candles, plus an honest account of what was served.

    `requested` and `served` differ whenever the provider's history is shorter
    than the window asked for. The caller shows both rather than presenting a
    short window as if it were the one requested.
    """
    everything = load(symbol, timeframe)
    served = everything if not bars else everything[-int(bars):]
    return {
        "candles": served,
        "requested": int(bars) if bars else len(everything),
        "served": len(served),
        "available": len(everything),
        "short": bool(bars) and len(served) < int(bars),
        "ceiling": CEILING.get(timeframe, ""),
        "from": served[0].timestamp if served else None,
        "to": served[-1].timestamp if served else None,
    }


def series(symbol: str, timeframe: str, candles: List[Candle]) -> CandleSeries:
    return CandleSeries(symbol=symbol, timeframe=ENGINE_TF.get(timeframe, timeframe),
                        candles=candles, provider="yahoo")
