"""Continuous historical series for the replay simulator.

The game replays real market history one bar at a time, so it needs the whole
series up front rather than a request per bar. This module fetches that series,
caches it on disk, and hands back plain lists ready to serialize.

Three things here are not just a thin wrapper over the provider:

* **Chunked 1m fetches.** Yahoo refuses more than 8 days of 1-minute history
  per request, so the full 30-day window is stitched together from weekly
  requests and de-duplicated by timestamp.
* **Derived timeframes.** Yahoo has no 10-minute interval. Rather than refuse
  it or invent bars, 10m is aggregated from real 5m data — open of the first
  bar, high/low across the group, close of the last, volume summed. Nothing is
  synthesized; a derived series says so in `source`.
* **Stale-cache fallback.** If the fetch fails (offline, rate limited), a cached
  series is served with `stale=True` instead of an error, so the game keeps
  working. It never silently substitutes a *different* timeframe.
"""

__all__ = ["ReplaySeries", "load_replay_series", "aggregate", "REPLAY_TIMEFRAMES"]

import json
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Sequence

import structlog

from ftmo_mt5.marketdata.base import Candle, DataUnavailable, TIMEFRAME_SECONDS
from ftmo_mt5.marketdata.yahoo import YahooHistoricalDataProvider

logger = structlog.get_logger(__name__)

# What the game offers, and how each one is actually obtained. `base` is the
# timeframe really fetched; `factor` is how many of those make one output bar.
REPLAY_TIMEFRAMES: dict[str, dict] = {
    "1m":  {"base": "1m",  "factor": 1, "days": 30,   "label": "1 minute"},
    "5m":  {"base": "5m",  "factor": 1, "days": 60,   "label": "5 minutes"},
    "10m": {"base": "5m",  "factor": 2, "days": 60,   "label": "10 minutes"},
    "15m": {"base": "15m", "factor": 1, "days": 60,   "label": "15 minutes"},
    "30m": {"base": "30m", "factor": 1, "days": 60,   "label": "30 minutes"},
    "1h":  {"base": "1h",  "factor": 1, "days": 725,  "label": "1 hour"},
    "1d":  {"base": "1d",  "factor": 1, "days": 8400, "label": "1 day"},
}

# Bar lengths for the replay timeframes. `TIMEFRAME_SECONDS` covers the
# canonical set; 10m is ours alone and would otherwise have no bucket size,
# which silently turns aggregation into a no-op.
_STEP_SECONDS = {**TIMEFRAME_SECONDS, "10m": 600}

# Yahoo caps a single 1-minute request at 8 days; ask for 7 to stay clear.
_CHUNK_DAYS = 7
_CACHE_DIR = Path(__file__).resolve().parents[2] / ".replay_cache"
# Intraday history rolls forward daily; a day-old cache is still perfectly
# good for a game about the past, and avoids a refetch on every page load.
_CACHE_TTL_S = 6 * 3600


@dataclass
class ReplaySeries:
    """One continuous series, ready to hand to the browser."""

    symbol: str
    timeframe: str
    candles: list[Candle] = field(default_factory=list)
    source: str = ""
    fetched_at: float = 0.0
    stale: bool = False

    def as_arrays(self, price_dp: int = 2) -> dict:
        """Flat parallel arrays — far smaller over the wire than objects."""
        r = lambda v: round(float(v), price_dp)
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "source": self.source,
            "stale": self.stale,
            "fetchedAt": self.fetched_at,
            "bars": len(self.candles),
            "ts": [int(c.timestamp) for c in self.candles],
            "o": [r(c.open) for c in self.candles],
            "h": [r(c.high) for c in self.candles],
            "l": [r(c.low) for c in self.candles],
            "c": [r(c.close) for c in self.candles],
            "v": [int(c.volume or 0) for c in self.candles],
        }


def aggregate(candles: Sequence[Candle], factor: int, timeframe: str) -> list[Candle]:
    """Combine `factor` consecutive bars into one, OHLCV-correctly.

    Groups are aligned to the timeframe boundary rather than to the start of the
    list, so the same bar always lands in the same group no matter where the
    fetch happened to begin. A group is only emitted when its bars are actually
    contiguous — a gap across a session break starts a new bar instead of
    welding Friday's close onto Monday's open.
    """
    if factor <= 1:
        return list(candles)
    step = _STEP_SECONDS.get(timeframe) or 0
    out: list[Candle] = []
    group: list[Candle] = []

    def flush() -> None:
        if not group:
            return
        out.append(Candle(
            timestamp=group[0].timestamp,
            open=group[0].open,
            high=max(c.high for c in group),
            low=min(c.low for c in group),
            close=group[-1].close,
            volume=sum(c.volume or 0 for c in group),
        ))
        group.clear()

    for c in candles:
        if group:
            same_bucket = step and (c.timestamp // step == group[0].timestamp // step)
            if not same_bucket or len(group) >= factor:
                flush()
        group.append(c)
    flush()
    return out


def _fetch(provider, symbol: str, base_tf: str, days: int) -> list[Candle]:
    """Fetch `days` of `base_tf`, in chunks when the provider demands it."""
    end = date.today() + timedelta(days=1)
    start = end - timedelta(days=days)

    if base_tf != "1m":
        return list(provider.get_history(symbol, base_tf, start, end).candles)

    # 1-minute history has to be stitched together a week at a time. Chunks
    # that fall outside the provider's window simply stop the loop; whatever
    # was collected before that is real data and worth keeping.
    seen: dict[int, Candle] = {}
    cursor = end
    while cursor > start:
        chunk_start = max(start, cursor - timedelta(days=_CHUNK_DAYS))
        try:
            for c in provider.get_history(symbol, base_tf, chunk_start, cursor).candles:
                seen[int(c.timestamp)] = c
        except DataUnavailable:
            break
        cursor = chunk_start
    return [seen[t] for t in sorted(seen)]


def _cache_path(symbol: str, timeframe: str) -> Path:
    safe = symbol.replace("/", "").replace("\\", "").upper()
    return _CACHE_DIR / f"{safe}_{timeframe}.json"


def _read_cache(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _from_cache(blob: dict, symbol: str, timeframe: str, stale: bool) -> ReplaySeries:
    candles = [Candle(timestamp=t, open=o, high=h, low=l, close=c, volume=v)
               for t, o, h, l, c, v in zip(blob["ts"], blob["o"], blob["h"],
                                           blob["l"], blob["c"], blob["v"])]
    return ReplaySeries(symbol=symbol, timeframe=timeframe, candles=candles,
                        source=blob.get("source", ""),
                        fetched_at=blob.get("fetchedAt", 0.0), stale=stale)


def load_replay_series(symbol: str = "/NQ", timeframe: str = "1d",
                       provider=None, force: bool = False) -> ReplaySeries:
    """The full continuous series for one timeframe, cached on disk.

    Raises:
        DataUnavailable: the timeframe is not offered, or the fetch failed and
            no cached copy exists to fall back on.
    """
    spec = REPLAY_TIMEFRAMES.get(timeframe)
    if not spec:
        raise DataUnavailable(
            f"{timeframe} is not one of the replay timeframes "
            f"({', '.join(REPLAY_TIMEFRAMES)})."
        )

    path = _cache_path(symbol, timeframe)
    cached = None if force else _read_cache(path)
    if cached and (time.time() - cached.get("fetchedAt", 0)) < _CACHE_TTL_S:
        return _from_cache(cached, symbol, timeframe, stale=False)

    provider = provider or YahooHistoricalDataProvider()
    try:
        raw = _fetch(provider, symbol, spec["base"], spec["days"])
    except Exception as exc:                      # network, rate limit, parse
        if cached:
            logger.warning("replay_fetch_failed_using_cache",
                           timeframe=timeframe, error=str(exc))
            return _from_cache(cached, symbol, timeframe, stale=True)
        raise DataUnavailable(
            f"Could not load {timeframe} history for {symbol}: {exc}"
        ) from exc

    if not raw:
        if cached:
            return _from_cache(cached, symbol, timeframe, stale=True)
        raise DataUnavailable(f"No {timeframe} history returned for {symbol}.")

    candles = aggregate(raw, spec["factor"], timeframe)
    source = (f"{provider.key} {spec['base']}"
              + (f" aggregated {spec['factor']}:1" if spec["factor"] > 1 else ""))
    series = ReplaySeries(symbol=symbol, timeframe=timeframe, candles=candles,
                          source=source, fetched_at=time.time())

    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(series.as_arrays()), encoding="utf-8")
    except OSError as exc:
        logger.warning("replay_cache_write_failed", error=str(exc))

    logger.info("replay_series", symbol=symbol, timeframe=timeframe,
                bars=len(candles), source=source)
    return series
