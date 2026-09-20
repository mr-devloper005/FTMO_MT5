"""Tests for the replay simulator's data layer.

The game replays real history, so the two things that matter here are that a
derived timeframe is derived *correctly* and that an unavailable one fails
loudly instead of quietly serving something else.
"""

import pytest

from ftmo_mt5.marketdata.base import Candle, DataUnavailable
from ftmo_mt5.marketdata.replay import (
    REPLAY_TIMEFRAMES,
    ReplaySeries,
    aggregate,
    load_replay_series,
)


def bar(minute: int, o: float, h: float, l: float, c: float, v: float = 1.0) -> Candle:
    """One 5-minute bar starting `minute` minutes after the hour."""
    return Candle(timestamp=1_700_000_000 + minute * 60, open=o, high=h, low=l,
                  close=c, volume=v)


class TestAggregate:
    def test_factor_one_is_a_passthrough(self):
        src = [bar(0, 1, 2, 0.5, 1.5), bar(5, 1.5, 3, 1, 2)]
        assert aggregate(src, 1, "5m") == src

    def test_two_bars_become_one_correct_ohlcv(self):
        src = [bar(0, 100, 110, 95, 105, 10), bar(5, 105, 120, 90, 118, 7)]
        out = aggregate(src, 2, "10m")
        assert len(out) == 1
        got = out[0]
        assert got.open == 100          # first open
        assert got.close == 118         # last close
        assert got.high == 120          # highest high
        assert got.low == 90            # lowest low
        assert got.volume == 17         # summed
        assert got.timestamp == src[0].timestamp

    def test_groups_align_to_the_clock_not_to_the_list(self):
        # Starting mid-bucket must not shift every later group: the bar at :05
        # belongs to the :00 bucket and ends up alone.
        src = [bar(5, 1, 1, 1, 1), bar(10, 2, 2, 2, 2), bar(15, 3, 3, 3, 3),
               bar(20, 4, 4, 4, 4), bar(25, 5, 5, 5, 5)]
        out = aggregate(src, 2, "10m")
        assert [c.open for c in out] == [1, 2, 4]
        assert [c.close for c in out] == [1, 3, 5]

    def test_a_session_gap_starts_a_new_bar(self):
        # A bar an hour later shares no bucket with the previous one, so the two
        # are never welded into a single candle spanning the break.
        src = [bar(0, 1, 1, 1, 1), bar(60, 9, 9, 9, 9)]
        out = aggregate(src, 2, "10m")
        assert len(out) == 2

    def test_empty_input(self):
        assert aggregate([], 2, "10m") == []


class TestLoadReplaySeries:
    def test_unknown_timeframe_is_refused(self):
        with pytest.raises(DataUnavailable) as err:
            load_replay_series("/NQ", "3m", provider=object())
        assert "3m" in str(err.value)

    def test_every_offered_timeframe_declares_how_it_is_built(self):
        for key, spec in REPLAY_TIMEFRAMES.items():
            assert spec["base"] in ("1m", "5m", "15m", "30m", "1h", "1d")
            assert spec["factor"] >= 1
            assert spec["days"] > 0

    def test_derived_timeframes_are_built_from_real_lower_bars(self):
        # 10m is the only derived one, and it must come from real 5m data.
        assert REPLAY_TIMEFRAMES["10m"]["base"] == "5m"
        assert REPLAY_TIMEFRAMES["10m"]["factor"] == 2

    def test_arrays_round_trip_the_candles(self):
        series = ReplaySeries(symbol="/NQ", timeframe="10m",
                              candles=[bar(0, 1.234, 2, 0.5, 1.5, 3)])
        blob = series.as_arrays()
        assert blob["bars"] == 1
        assert blob["o"] == [1.23]
        assert blob["v"] == [3]
        assert blob["ts"] == [1_700_000_000]

    def test_a_failed_fetch_with_no_cache_raises(self, tmp_path, monkeypatch):
        import ftmo_mt5.marketdata.replay as mod
        monkeypatch.setattr(mod, "_CACHE_DIR", tmp_path)

        class Broken:
            key = "broken"

            def get_history(self, *a, **k):
                raise RuntimeError("network down")

        with pytest.raises(DataUnavailable):
            load_replay_series("/NQ", "15m", provider=Broken())
