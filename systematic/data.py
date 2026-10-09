"""Fresh, completed bars for research and paper trading.

The Lab's cache (``study/lab/cache``) is keyed by symbol, timeframe and a day
count — never by date — so a series downloaded once is served unchanged
forever. That is right for reproducible research and wrong for trading: a
paper cycle acting on a month-old bar is acting on nothing.

So this pipeline points the Lab at its own cache folder, one per calendar day.
The first call of the day downloads; every later call that day reuses it.
Nothing about the Lab's own code or cache changes.
"""
from __future__ import annotations

from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import List, Optional
from zoneinfo import ZoneInfo

from ftmo_mt5.lab import windows

NEW_YORK = ZoneInfo("America/New_York")
# A US daily bar is final once the closing auction has printed; 16:15 ET leaves
# a margin for the provider to publish it.
DAILY_FINAL_AFTER = time(16, 15)


def use_daily_cache(state_dir: Path, day: Optional[date] = None) -> Path:
    """Point the Lab's window loader at this pipeline's cache for `day`."""
    day = day or datetime.now(NEW_YORK).date()
    path = Path(state_dir) / "cache" / day.isoformat()
    path.mkdir(parents=True, exist_ok=True)
    windows.CACHE = path
    with windows._lock:
        windows._memory.clear()
    return path


def trading_date(candle) -> date:
    """Yahoo stamps a daily bar at 00:00 UTC of its trading date."""
    return datetime.fromtimestamp(candle.timestamp, timezone.utc).date()


def completed_bars(candles: List, timeframe: str, now: Optional[datetime] = None) -> List:
    """Drop a bar that is still forming.

    During US market hours Yahoo's last daily row is today's bar-so-far. Acting
    on it would mean trading on a close that has not happened yet.
    """
    if timeframe != "1d":
        raise ValueError(f"completed_bars knows daily bars only, not {timeframe!r}")
    if not candles:
        return candles
    now_ny = (now or datetime.now(timezone.utc)).astimezone(NEW_YORK)
    today = now_ny.date()
    last = trading_date(candles[-1])
    if last > today or (last == today and now_ny.time() < DAILY_FINAL_AFTER):
        return candles[:-1]
    return candles


def load_completed(symbol: str, timeframe: str, bars: Optional[int] = None,
                   now: Optional[datetime] = None) -> List:
    """The last `bars` completed candles for one symbol."""
    candles = completed_bars(windows.load(symbol, timeframe), timeframe, now)
    return candles[-int(bars):] if bars else candles
