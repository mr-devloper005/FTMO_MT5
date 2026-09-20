"""Tests for the multi-timeframe green-stack strategy.

The rule is simple enough that the risk is not the arithmetic — it is whether
the higher timeframes are built from the right bars, and whether any of them
can see a bar the run has not reached yet.
"""

import pytest

from ftmo_mt5.marketdata.base import Candle
from ftmo_mt5.signals.strategies import (
    MultiTimeframeGreenStrategy,
    _bar_seconds,
    _green_series,
    get_strategy,
    list_strategies,
)

FIVE_MIN = 300
T0 = 1_700_000_000 - 1_700_000_000 % 3600      # start on an exact hour


def bar(k: int, o: float, c: float, step: int = FIVE_MIN) -> Candle:
    """Bar k, `step` seconds long, opening at o and closing at c."""
    return Candle(timestamp=T0 + k * step, open=o, high=max(o, c) + 1,
                  low=min(o, c) - 1, close=c, volume=1.0)


def climb(n: int, step: int = FIVE_MIN, start: float = 100.0) -> list[Candle]:
    """Every bar green, so every timeframe above it is green too."""
    return [bar(k, start + k, start + k + 0.5, step) for k in range(n)]


class TestBarSeconds:
    def test_reads_the_spacing_off_the_data(self):
        assert _bar_seconds(climb(20)) == FIVE_MIN
        assert _bar_seconds(climb(20, step=3600)) == 3600

    def test_a_session_gap_does_not_win_the_vote(self):
        # One overnight gap among many 5-minute bars must not be mistaken for
        # the bar length.
        candles = climb(20)
        candles.append(bar(200, 300, 301))
        assert _bar_seconds(candles) == FIVE_MIN

    def test_single_candle_falls_back(self):
        assert _bar_seconds(climb(1)) == 60


class TestGreenSeries:
    def test_forming_bucket_compares_to_the_bucket_open(self):
        # Three 5m bars in one 15m bucket: down, down, then up past the open.
        candles = [bar(0, 100, 99), bar(1, 99, 98), bar(2, 98, 101)]
        green = _green_series(candles, 900, completed=False)
        assert green == [False, False, True]

    def test_completed_mode_reports_the_previous_bucket(self):
        # Bucket A (bars 0-2) closes red; bucket B's first bar should report A.
        candles = [bar(0, 100, 99), bar(1, 99, 98), bar(2, 98, 97),
                   bar(3, 97, 105)]
        green = _green_series(candles, 900, completed=True)
        assert green[:3] == [None, None, None]     # nothing has finished yet
        assert green[3] is False                   # bucket A finished red

    def test_a_bucket_is_green_on_its_own_open_not_the_bar_open(self):
        # The last 5m bar is red, but the 15m bar it sits in is green.
        candles = [bar(0, 100, 110), bar(1, 110, 120), bar(2, 120, 115)]
        assert _green_series(candles, 900, completed=False)[2] is True
        assert _green_series(candles, FIVE_MIN, completed=False)[2] is False

    def test_never_reads_a_later_bar(self):
        # Truncating the series must not change any earlier value.
        candles = climb(40)
        full = _green_series(candles, 900, completed=False)
        cut = _green_series(candles[:25], 900, completed=False)
        assert cut == full[:25]


class TestStrategy:
    def strat(self):
        return MultiTimeframeGreenStrategy()

    def test_all_green_is_a_buy(self):
        s, candles = self.strat(), climb(60)
        state = s.prepare(candles, {})
        v = s.evaluate(candles, 50, state)
        assert v.signal == "BUY"
        assert len(v.reasons) == 3

    def test_every_timeframe_red_is_a_short(self):
        s = self.strat()
        candles = [bar(k, 200 - k, 200 - k - 0.5) for k in range(60)]
        state = s.prepare(candles, {})
        signals = {s.evaluate(candles, i, state).signal for i in range(60)}
        assert signals == {"SELL"}
        assert "red" in s.evaluate(candles, 50, state).reasons[0]

    def test_shorts_can_be_switched_off(self):
        # With shorts off, unanimous red is still a reason not to be long — it
        # is simply not acted on in the other direction.
        s = self.strat()
        candles = [bar(k, 200 - k, 200 - k - 0.5) for k in range(60)]
        state = s.prepare(candles, {"trade_shorts": False})
        signals = {s.evaluate(candles, i, state).signal for i in range(60)}
        assert "SELL" not in signals
        assert signals == {"EXIT"}

    def test_mixed_timeframes_are_an_exit_not_a_short(self):
        # The distinction the rule depends on: disagreement is the absence of a
        # signal, unanimity in either direction is a position.
        candles = climb(60)
        for k in range(48, 60):                    # the hour rolls over
            candles[k] = bar(k, 200 - k, 200 - k - 0.5)
        candles[59] = bar(59, 100, 101)            # green 5m inside a red hour
        s = self.strat()
        v = s.evaluate(candles, 59, s.prepare(candles, {}))
        assert v.signal == "EXIT"
        assert "disagree" in v.reasons[0]

    def test_warm_up_is_flat_not_an_exit(self):
        # No higher bar has finished yet: that is no opinion, not a reason to
        # close a position that may be perfectly fine.
        s, candles = self.strat(), climb(60)
        state = s.prepare(candles, {"require_completed": True})
        assert s.evaluate(candles, 0, state).signal == "FLAT"

    def test_a_green_run_then_a_red_run_flips_the_position(self):
        # Long while every bar is green, then short once every bar is red, with
        # the mixed bars in between coming off rather than reversing early.
        s = self.strat()
        candles = climb(40) + [bar(40 + k, 140 - k, 139.5 - k) for k in range(30)]
        state = s.prepare(candles, {})
        signals = [s.evaluate(candles, i, state).signal for i in range(len(candles))]
        assert set(signals[:40]) == {"BUY"}
        assert "EXIT" in signals[40:50]            # the turn
        assert signals[-1] == "SELL"               # fully red by the end

    def test_timeframes_are_parameters(self):
        s, candles = self.strat(), climb(60)
        state = s.prepare(candles, {"tf1_minutes": 5, "tf2_minutes": 10,
                                    "tf3_minutes": 30})
        assert [leg["minutes"] for leg in state["legs"]] == [5, 10, 30]

    def test_a_timeframe_below_the_data_is_refused_not_faked(self):
        s = self.strat()
        candles = climb(60, step=3600)             # hourly data
        state = s.prepare(candles, {"tf1_minutes": 5})
        v = s.evaluate(candles, 30, state)
        assert v.signal == "FLAT"
        assert "cannot be built" in v.reasons[0]

    def test_the_refusal_names_both_timeframes_and_the_way_out(self):
        # The default 5/15/60 on daily data is the easy mistake to make, and the
        # message has to be enough to fix it without reading the source.
        s = self.strat()
        daily = climb(60, step=86400)
        problems = s.prepare(daily, {})["problems"]
        assert len(problems) == 3
        assert "1d bars" in problems[0] and "5m" in problems[0]
        assert "1440 minutes or more" in problems[0]
        # The consequence has to be in the sentence: a warning that does not say
        # "no signals" reads as advice next to a run that produced nothing.
        assert "No signals will be generated" in problems[0]

    def test_one_good_leg_does_not_quietly_run_a_smaller_stack(self):
        # 1h data with 5m/15m/1h asked for: the 1h leg is buildable, the other
        # two are not. Running on the one leg would be a different strategy.
        s = self.strat()
        hourly = climb(200, step=3600)
        state = s.prepare(hourly, {})
        assert [leg["minutes"] for leg in state["legs"]] == [60]
        assert len(state["problems"]) == 2
        assert s.evaluate(hourly, 150, state).signal == "FLAT"

    def test_problems_are_reported_for_the_run_to_surface(self):
        s = self.strat()
        assert s.prepare(climb(60, step=86400), {})["problems"]
        assert not s.prepare(climb(60), {})["problems"]

    def test_daily_and_above_works_on_daily_data(self):
        # The same rule, re-pointed at timeframes daily bars can actually build.
        s = self.strat()
        daily = climb(120, step=86400)
        state = s.prepare(daily, {"tf1_minutes": 1440, "tf2_minutes": 10080,
                                  "tf3_minutes": 43200})
        assert not state["problems"]
        assert s.evaluate(daily, 100, state).signal == "BUY"

    def test_exit_on_condition_can_be_switched_off(self):
        # With it off, disagreement stops closing the position — the trailing
        # stop or an opposite signal is the only way out.
        s = self.strat()
        candles = climb(40) + [bar(40 + k, 140 - k, 139.5 - k) for k in range(30)]
        on = s.prepare(candles, {})
        off = s.prepare(candles, {"exit_on_condition": False})
        sig_on = {s.evaluate(candles, i, on).signal for i in range(len(candles))}
        sig_off = {s.evaluate(candles, i, off).signal for i in range(len(candles))}
        assert "EXIT" in sig_on
        assert "EXIT" not in sig_off
        assert sig_off == {"BUY", "FLAT", "SELL"}

    def test_switching_the_exit_off_does_not_change_the_entries(self):
        s = self.strat()
        candles = climb(40) + [bar(40 + k, 140 - k, 139.5 - k) for k in range(20)]
        on = s.prepare(candles, {})
        off = s.prepare(candles, {"exit_on_condition": False})
        buys_on = [i for i in range(len(candles))
                   if s.evaluate(candles, i, on).signal == "BUY"]
        buys_off = [i for i in range(len(candles))
                    if s.evaluate(candles, i, off).signal == "BUY"]
        assert buys_on == buys_off

    def test_the_flag_accepts_the_strings_a_form_sends(self):
        s, candles = self.strat(), climb(60)
        assert s.prepare(candles, {"exit_on_condition": "false"})["exit_on_condition"] is False
        assert s.prepare(candles, {"exit_on_condition": "true"})["exit_on_condition"] is True
        assert s.prepare(candles, {})["exit_on_condition"] is True

    def test_completed_mode_is_available(self):
        s, candles = self.strat(), climb(60)
        state = s.prepare(candles, {"require_completed": True})
        assert state["completed"] is True
        assert s.evaluate(candles, 50, state).signal == "BUY"

    def test_verdict_carries_each_timeframe_reading(self):
        s, candles = self.strat(), climb(60)
        v = s.evaluate(candles, 50, s.prepare(candles, {}))
        assert set(v.indicators) == {"5m", "15m", "1h"}
        assert all(v.indicators.values())

    def test_no_look_ahead_across_the_whole_run(self):
        s, candles = self.strat(), climb(80)
        full = s.prepare(candles, {})
        cut = s.prepare(candles[:50], {})
        for i in range(50):
            assert (s.evaluate(candles, i, full).signal ==
                    s.evaluate(candles[:50], i, cut).signal)


class TestRegistry:
    def test_it_is_offered(self):
        # Written when this was the only strategy on the menu. Several have been
        # added since, so the claim worth keeping is that this one is offered —
        # not that nothing else is.
        assert "MTF_GREEN_STACK" in [s["name"] for s in list_strategies()]

    def test_it_is_the_default(self):
        assert get_strategy("").name == "MTF_GREEN_STACK"

    def test_every_offered_strategy_describes_itself(self):
        for s in list_strategies():
            assert s["name"] and s["label"] and s["description"]
            for p in s["parameters"]:
                assert p["key"] and p["label"] and p["type"] in ("int", "float", "bool")

    def test_the_old_strategy_is_hidden_but_still_resolvable(self):
        assert get_strategy("EMA_RSI_PATTERN").name == "EMA_RSI_PATTERN"
        assert "EMA_RSI_PATTERN" not in [s["name"] for s in list_strategies()]
