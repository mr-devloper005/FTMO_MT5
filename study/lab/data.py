"""Historical data for the research loop: fetched once, cached on disk.

Yahoo's intraday window is short and rate-limited, so hundreds of backtests
cannot each go to the network. Every series is fetched once, written to
`study/lab/cache/`, and reloaded from there afterwards.

4-hour bars are built by grouping real 1-hour bars into clock-aligned buckets.
Nothing here synthesises a price that was never printed.
"""
import json, logging, structlog
structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.CRITICAL))
from datetime import date, timedelta
from pathlib import Path

from ftmo_mt5.marketdata.base import Candle, CandleSeries
from ftmo_mt5.marketdata.yahoo import YahooHistoricalDataProvider

CACHE = Path(__file__).parent / "cache"
CACHE.mkdir(exist_ok=True)
PROV = YahooHistoricalDataProvider()

# What Yahoo actually serves. 30m is a 60-day trailing window; 1h is 730 days.
BASE = {
    # timeframe -> (provider timeframe to fetch, grouping factor, days requested)
    # Yahoo serves 5m-30m for a 60-day trailing window and 1h for two years.
    # Everything between 2h and 12h is built by grouping real 1-hour bars.
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
    # Weekly bars are grouped from real daily bars, clock-aligned to the week.
    "1w":  ("1d",  5, 9600),
}

SECONDS = {"5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "2h": 7200,
           "4h": 14400, "6h": 21600, "8h": 28800, "12h": 43200, "1d": 86400,
           "1w": 604800}

# What the engine should be told a grouped series is, since it has no bar size
# of its own for 2h/6h/8h/12h. The strategies read the real timestamps either way.
ENGINE_TF = {"2h": "1h", "4h": "1h", "6h": "1h", "8h": "1h",
             "12h": "1h", "1w": "1d"}


def group(candles, seconds, factor):
    """Clock-aligned grouping. A bucket also flushes on a session gap."""
    if factor <= 1:
        return list(candles)
    out, b = [], []
    def flush():
        if b:
            out.append(Candle(timestamp=b[0].timestamp, open=b[0].open,
                              high=max(x.high for x in b), low=min(x.low for x in b),
                              close=b[-1].close, volume=sum(x.volume or 0 for x in b)))
            b.clear()
    for c in candles:
        if b and (int(c.timestamp) // seconds != int(b[0].timestamp) // seconds
                  or len(b) >= factor):
            flush()
        b.append(c)
    flush()
    return out


def _fetch(symbol, base_tf, days):
    key = CACHE / f"{symbol.strip('/')}_{base_tf}_{days}.json"
    if key.exists():
        raw = json.loads(key.read_text())
    else:
        end = date.today()
        c = PROV.get_history(symbol, base_tf, end - timedelta(days=days), end).candles
        raw = [[c_.timestamp, c_.open, c_.high, c_.low, c_.close, c_.volume or 0] for c_ in c]
        key.write_text(json.dumps(raw))
    return [Candle(timestamp=t, open=o, high=h, low=l, close=cl, volume=v)
            for t, o, h, l, cl, v in raw]


def load(symbol, tf):
    """Candles for one instrument and timeframe, cached."""
    base_tf, factor, days = BASE[tf]
    return group(_fetch(symbol, base_tf, days), SECONDS[tf], factor)


def series(symbol, tf, candles):
    return CandleSeries(symbol=symbol, timeframe=ENGINE_TF.get(tf, tf),
                        candles=candles, provider="yahoo")
