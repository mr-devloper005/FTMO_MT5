"""The "hold until it breaks" family.

Every other strategy in this project is entry-signal-driven: find a setup,
enter, exit, look for the next one. That family lost to buy & hold on every
one of the ~1,000 backtests run against it -- not because the rules are bad,
but because trading in and out of a compounding trend structurally cannot
capture as much of it as simply staying in.

This module holds the shape that was missing: enter once, stay in through
noise, and leave only on a *structural* break -- then, optionally, come back
when the trend re-establishes. It exists because of one measurement: TARIL ran
to +2,051% by 2025-01-03 and was +945% by 2026-09-04. Holding through the peak
gave back half the gain. Buy & hold is unbeatable only under the assumption
that you never sell; that assumption is what this family tests.

Stated plainly, because it would be easy to oversell: ATR trailing stops
bolted onto the *active* strategies did not beat buy & hold either. This is a
hypothesis with a measurement behind it, not an expected winner.
"""

__all__ = ["HoldUntilBrokenStrategy", "BuyAndHoldBaselineStrategy"]

from typing import Any, Optional, Sequence

from ftmo_mt5.marketdata.base import Candle
from ftmo_mt5.signals import indicators as ind
from ftmo_mt5.signals.strategies import STRATEGIES, Verdict, _param


class HoldUntilBrokenStrategy:
    """Hold a trend until it structurally breaks, not until it wobbles.

    Entry is deliberately dumb: the first bar whose close sits above the long
    average. Nothing about picking a clever entry survived testing, and an
    entry rule that waits for confirmation just donates the first leg of the
    move to the benchmark.

    The exit carries the whole idea. A single close below the average is
    noise -- on daily bars a strong name does that several times a year
    without the trend ending. So the break has to persist for `confirm_bars`
    consecutive closes before it counts. That one parameter is the difference
    between "sold the first dip" and "sold when it actually rolled over".
    """

    name = "HOLD_UNTIL_BROKEN"

    DEFAULTS = {
        "sma_period": 200,      # the line that defines "the trend is intact"
        "confirm_bars": 3,      # consecutive closes beyond it before acting
        "reentry": True,        # come back when price reclaims the average
        "trade_shorts": False,  # a broken uptrend is a reason to be out, not short
        # False -> this rule owns the exit (closes beyond the average).
        # True  -> this rule never exits; the run's own ATR trailing stop
        #          does, and re-entry then requires a new high rather than
        #          the next bar, which would otherwise buy straight back in
        #          and cancel the stop entirely.
        "use_trailing_exit": False,
        "reentry_high_lookback": 20,
    }

    def describe(self, params: Optional[dict] = None) -> dict:
        m = {**self.DEFAULTS, **(params or {})}
        return {
            "name": self.name,
            "label": "Hold until broken (buy & hold with an exit)",
            "description": ("Buys once and holds through everything, exiting only "
                            "after price closes beyond its long moving average for "
                            "several consecutive bars. Re-enters when the trend "
                            "re-establishes."),
            "parameters": [
                {"key": "sma_period", "label": "Trend average period", "type": "int",
                 "default": 200, "value": m["sma_period"]},
                {"key": "confirm_bars", "label": "Closes beyond it before exiting",
                 "type": "int", "default": 3, "value": m["confirm_bars"]},
                {"key": "reentry", "label": "Re-enter when the trend returns",
                 "type": "bool", "default": True, "value": bool(m["reentry"])},
                {"key": "trade_shorts", "label": "Short a broken trend too",
                 "type": "bool", "default": False, "value": bool(m["trade_shorts"])},
                {"key": "use_trailing_exit",
                 "label": "Exit via the run's ATR trailing stop instead of the average",
                 "type": "bool", "default": False,
                 "value": bool(m["use_trailing_exit"])},
                {"key": "reentry_high_lookback",
                 "label": "Bars of new high required to re-enter (trailing-exit mode)",
                 "type": "int", "default": 20, "value": m["reentry_high_lookback"]},
            ],
        }

    def prepare(self, candles: Sequence[Candle], params: dict) -> dict:
        period = int(_param(params, "sma_period", 200, int))
        confirm = max(1, int(_param(params, "confirm_bars", 3, int)))
        closes = [c.close for c in candles]
        mid = ind.sma(closes, period)

        shorts = params.get("trade_shorts", False)
        reentry = params.get("reentry", True)
        for name, val in (("shorts", shorts), ("reentry", reentry)):
            if isinstance(val, str):
                parsed = val.strip().lower() not in ("0", "false", "no", "")
                if name == "shorts":
                    shorts = parsed
                else:
                    reentry = parsed

        problems = []
        if period >= len(candles):
            problems.append(
                f"sma_period {period} needs more bars than the {len(candles)} in this "
                "window, so the trend line never forms and the rule cannot act.")

        trail_exit = params.get("use_trailing_exit", False)
        if isinstance(trail_exit, str):
            trail_exit = trail_exit.strip().lower() not in ("0", "false", "no", "")
        trail_exit = bool(trail_exit)

        look = max(1, int(_param(params, "reentry_high_lookback", 20, int)))
        highs = [c.high for c in candles]
        # Highest high strictly BEFORE bar i, so bar i can be compared against
        # it without reading its own value. candles 0..i-1 only.
        prior_high: list = [None] * len(candles)
        for i in range(1, len(candles)):
            start = max(0, i - look)
            prior_high[i] = max(highs[start:i]) if i > start else None

        return {"mid": mid, "confirm": confirm, "shorts": bool(shorts),
                "reentry": bool(reentry), "trailExit": trail_exit,
                "priorHigh": prior_high, "problems": problems}

    def evaluate(self, candles: Sequence[Candle], i: int, state: dict) -> Verdict:
        mid = state["mid"][i]
        if mid is None:
            return Verdict("FLAT", ["Not enough history for the trend average"])

        close = candles[i].close
        confirm = state["confirm"]
        readings = {"mid": mid, "close": close}

        if state["trailExit"]:
            # This rule never exits in trail mode -- the run's ATR trailing
            # stop is the exit. It only decides when it is allowed back in,
            # and "the next bar" is not an answer: that would re-buy straight
            # after a stop-out and make the stop a no-op.
            ph = state["priorHigh"][i]
            if ph is not None and close > ph:
                return Verdict("BUY", [
                    f"close {close:.2f} above the prior {len(candles) and ''}"
                    f"high {ph:.2f} — strength confirmed, hold and let the "
                    "trailing stop decide the exit"], readings)
            return Verdict("FLAT", [
                f"close {close:.2f} has not exceeded the prior high "
                f"{ph:.2f}" if ph is not None else "no prior high yet"], readings)

        # How many of the last `confirm` closes sit on each side of the line.
        # Only bars 0..i are ever read -- no look-ahead.
        start = max(0, i - confirm + 1)
        window = [(candles[j].close, state["mid"][j])
                  for j in range(start, i + 1) if state["mid"][j] is not None]
        below = len(window) == confirm and all(c < m for c, m in window)
        above = len(window) == confirm and all(c > m for c, m in window)

        if below:
            if state["shorts"]:
                return Verdict("SELL", [
                    f"{confirm} consecutive closes below the {len(state['mid']) and ''}"
                    f"trend average — the trend is broken", ], readings)
            return Verdict("EXIT", [
                f"{confirm} consecutive closes below the trend average "
                f"({close:.2f} vs {mid:.2f}) — the trend is broken"], readings)

        if above:
            if not state["reentry"]:
                # Only the very first qualifying stretch may open a position.
                first = next((j for j in range(len(state["mid"]))
                              if state["mid"][j] is not None), None)
                if first is None or i > first + confirm * 4:
                    return Verdict("FLAT", ["Re-entry disabled"], readings)
            return Verdict("BUY", [
                f"{confirm} consecutive closes above the trend average "
                f"({close:.2f} vs {mid:.2f}) — trend intact, stay in"], readings)

        return Verdict("FLAT", [f"close {close:.2f} straddling the average {mid:.2f}"],
                       readings)


class BuyAndHoldBaselineStrategy:
    """Buy on the first tradeable bar, never sell. The benchmark, as a strategy.

    This is a calibration instrument, not a trading idea. Running it should
    reproduce the engine's own buy & hold benchmark almost exactly; if it ever
    doesn't, something in the execution model has drifted and every other
    result in the lab is suspect. It costs one strategy slot to have a
    permanent canary for that.
    """

    name = "BUY_AND_HOLD_BASELINE"

    DEFAULTS: dict[str, Any] = {"warmup_bars": 1}

    def describe(self, params: Optional[dict] = None) -> dict:
        m = {**self.DEFAULTS, **(params or {})}
        return {
            "name": self.name,
            "label": "Buy & hold (baseline canary)",
            "description": ("Buys on the first tradeable bar and never exits. Exists "
                            "to verify the engine reproduces its own buy & hold "
                            "benchmark — a calibration check, not a strategy."),
            "parameters": [
                {"key": "warmup_bars", "label": "Bars to wait before buying",
                 "type": "int", "default": 1, "value": m["warmup_bars"]},
            ],
        }

    def prepare(self, candles: Sequence[Candle], params: dict) -> dict:
        return {"warmup": max(0, int(_param(params, "warmup_bars", 1, int)))}

    def evaluate(self, candles: Sequence[Candle], i: int, state: dict) -> Verdict:
        if i < state["warmup"]:
            return Verdict("FLAT", ["Waiting for the first tradeable bar"])
        return Verdict("BUY", ["Hold, unconditionally"],
                       {"close": candles[i].close})


STRATEGIES[HoldUntilBrokenStrategy.name] = HoldUntilBrokenStrategy()
STRATEGIES[BuyAndHoldBaselineStrategy.name] = BuyAndHoldBaselineStrategy()
