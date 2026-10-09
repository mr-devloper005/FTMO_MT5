"""Completed bars for any of the configured timeframes.

``systematic.data.load_completed`` is the paper trader's loader and knows daily
bars only (it refuses anything else), so it is reused unchanged for 1d. Every
other timeframe comes from the Lab's loader (``ftmo_mt5.lab.windows.load``,
the same cache, grouping and Yahoo source the Lab uses) and drops its last bar
unless that bar's whole period has elapsed. That rule is conservative — a bar
that has in fact closed may wait until its nominal period ends — but it can
never hand a backtest a bar that is still forming.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from ftmo_mt5.lab import windows
from research_agent.config import DAILY, Settings


def completed(candles: List, timeframe: str, now: Optional[datetime] = None) -> List:
    if not candles:
        return candles
    now_ts = (now or datetime.now(timezone.utc)).timestamp()
    if candles[-1].timestamp + windows.SECONDS[timeframe] > now_ts:
        return candles[:-1]
    return candles


def load(symbol: str, timeframe: str, settings: Settings, now: Optional[datetime] = None) -> List:
    """The completed bars a backtest on this timeframe uses, with the history policy applied."""
    if timeframe == DAILY:
        from systematic.data import load_completed
        return load_completed(symbol, DAILY, settings.history_bars(DAILY), now)
    candles = completed(windows.load(symbol, timeframe), timeframe, now)
    n = settings.history_bars(timeframe)
    return candles[-n:] if n else candles
