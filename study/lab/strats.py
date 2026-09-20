"""New strategies designed for this research loop.

None of these is a parameter tweak of an existing rule. Each is a different
hypothesis about where an edge could live, using indicator families the project
had not tried: Bollinger bandwidth, MACD, Keltner channels, session ranges and
an ADX-driven switch between trend-following and mean-reversion.

Look-ahead discipline
---------------------
Every series here is forward-only. Where a rule needs a percentile ("is
volatility unusually low?") it is taken over a *trailing* window ending at the
current bar, never over the whole sample — a whole-sample percentile silently
tells the strategy what volatility was going to be.
"""
import math
from typing import Optional, Sequence

from ftmo_mt5.marketdata.base import Candle
from ftmo_mt5.signals import indicators as ind
from ftmo_mt5.signals.strategies import STRATEGIES, Verdict, _param


def _stdev(values, period):
    """Trailing standard deviation, index-aligned, None until the window fills."""
    out, s, s2 = [], 0.0, 0.0
    for i, v in enumerate(values):
        s += v; s2 += v * v
        if i >= period:
            s -= values[i - period]; s2 -= values[i - period] ** 2
        if i >= period - 1:
            m = s / period
            out.append(math.sqrt(max(s2 / period - m * m, 0.0)))
        else:
            out.append(None)
    return out


def _rolling_rank(values, i, window):
    """Where values[i] sits inside the previous `window` values, 0..1.

    Trailing only: the window ends at i, so nothing after i can influence it.
    """
    lo = max(0, i - window + 1)
    hist = [v for v in values[lo:i + 1] if v is not None]
    if len(hist) < window // 2 or values[i] is None:
        return None
    return sum(1 for v in hist if v <= values[i]) / len(hist)


# ---------------------------------------------------------------------------
class SqueezeBreakoutStrategy:
    """Volatility contraction, then expansion.

    The idea is not "buy a breakout" — the project already tests Donchian. It is
    that a breakout is only worth taking when it emerges from a *quiet* market.
    Bollinger bandwidth measures the quiet; a break of the recent range in the
    direction of the longer trend takes the expansion that follows.

    BUY  bandwidth in the lowest quartile of the trailing year, close above the
         highest close of the last `range_lookback` bars, and above the trend EMA.
    SELL the mirror image.
    EXIT the ATR trailing stop, or price closing back through the trend EMA.
    """

    name = "SQUEEZE_BREAKOUT"
    DEFAULTS = {"bb_period": 20, "squeeze_pct": 0.25, "rank_window": 250,
                "range_lookback": 20, "trend_period": 100, "atr_period": 14,
                "trade_shorts": True}

    def describe(self, params=None):
        m = {**self.DEFAULTS, **(params or {})}
        return {"name": self.name, "label": "Volatility squeeze breakout",
                "description": "Break of the recent range out of a low-bandwidth market.",
                "parameters": [{"key": k, "label": k, "type": "float",
                                "default": v, "value": m[k]} for k, v in self.DEFAULTS.items()]}

    def prepare(self, candles, params):
        closes = [c.close for c in candles]
        bb = int(_param(params, "bb_period", 20, int))
        mid = ind.sma(closes, bb)
        sd = _stdev(closes, bb)
        width = [(2 * s / m) if (s is not None and m) else None for s, m in zip(sd, mid)]
        return {"width": width, "atr": ind.atr(candles, int(_param(params, "atr_period", 14, int))),
                "trend": ind.ema(closes, int(_param(params, "trend_period", 100, int))),
                "closes": closes,
                "squeeze_pct": _param(params, "squeeze_pct", 0.25),
                "rank_window": int(_param(params, "rank_window", 250, int)),
                "lb": int(_param(params, "range_lookback", 20, int)),
                "shorts": bool(params.get("trade_shorts", True))}

    def evaluate(self, candles, i, state):
        trend, atr = state["trend"][i], state["atr"][i]
        if trend is None or atr is None or i < state["lb"] + 1:
            return Verdict("FLAT", ["Insufficient history"])
        close = candles[i].close
        r = {"trend": trend, "atr": atr, "width": state["width"][i]}

        rank = _rolling_rank(state["width"], i, state["rank_window"])
        if rank is None:
            return Verdict("FLAT", ["Bandwidth history too short"], r)
        r["bandwidthRank"] = round(rank, 3)

        prior = state["closes"][i - state["lb"]:i]
        hi, lo = max(prior), min(prior)
        quiet = rank <= state["squeeze_pct"]

        if quiet and close > hi and close > trend:
            return Verdict("BUY", [f"Bandwidth in the lowest {state['squeeze_pct']:.0%} "
                                   f"of the last {state['rank_window']} bars",
                                   f"Close {close:.2f} above the {state['lb']}-bar high {hi:.2f}",
                                   f"Above the trend EMA {trend:.2f}"], r)
        if state["shorts"] and quiet and close < lo and close < trend:
            return Verdict("SELL", [f"Bandwidth in the lowest {state['squeeze_pct']:.0%}",
                                    f"Close below the {state['lb']}-bar low {lo:.2f}",
                                    "Below the trend EMA"], r)
        # No exit of its own: the run's ATR trailing stop closes the trade. The
        # entry is the whole hypothesis here, so nothing else is allowed to
        # take credit for the result.
        return Verdict("FLAT", [], r)


# ---------------------------------------------------------------------------
class MacdAdxStrategy:
    """MACD for direction, ADX for permission.

    MACD alone whipsaws in a range; ADX measures whether a trend exists at all.
    The pair is the classic answer and the project had tested neither indicator.

    BUY  MACD above its signal line, +DI above -DI, ADX above `adx_min`.
    SELL the mirror image.
    EXIT MACD crosses back, or ADX collapses below `adx_exit`.
    """

    name = "MACD_ADX"
    DEFAULTS = {"fast": 12, "slow": 26, "signal": 9, "adx_period": 14,
                "adx_min": 25.0, "adx_exit": 15.0, "trade_shorts": True}

    def describe(self, params=None):
        m = {**self.DEFAULTS, **(params or {})}
        return {"name": self.name, "label": "MACD + ADX trend",
                "description": "MACD chooses the side, ADX decides whether to have one.",
                "parameters": [{"key": k, "label": k, "type": "float",
                                "default": v, "value": m[k]} for k, v in self.DEFAULTS.items()]}

    def prepare(self, candles, params):
        closes = [c.close for c in candles]
        f = ind.ema(closes, int(_param(params, "fast", 12, int)))
        s = ind.ema(closes, int(_param(params, "slow", 26, int)))
        macd = [(a - b) if (a is not None and b is not None) else None for a, b in zip(f, s)]
        clean = [m for m in macd if m is not None]
        sig_raw = ind.ema(clean, int(_param(params, "signal", 9, int)))
        sig, k = [], 0
        for m in macd:
            if m is None:
                sig.append(None)
            else:
                sig.append(sig_raw[k]); k += 1
        p = int(_param(params, "adx_period", 14, int))
        plus, minus = ind.directional(candles, p)
        return {"macd": macd, "signal": sig, "adx": ind.adx(candles, p),
                "plus": plus, "minus": minus, "atr": ind.atr(candles, 14),
                "adx_min": _param(params, "adx_min", 25.0),
                "adx_exit": _param(params, "adx_exit", 15.0),
                "shorts": bool(params.get("trade_shorts", True))}

    def evaluate(self, candles, i, state):
        macd, sig, adx = state["macd"][i], state["signal"][i], state["adx"][i]
        plus, minus = state["plus"][i], state["minus"][i]
        if None in (macd, sig, adx, plus, minus):
            return Verdict("FLAT", ["Insufficient history"])
        r = {"macd": macd, "signal": sig, "adx": adx, "+DI": plus, "-DI": minus,
             "atr": state["atr"][i]}
        if adx < state["adx_exit"]:
            return Verdict("EXIT", [f"ADX {adx:.1f} below {state['adx_exit']:g} - no trend"], r)
        if adx >= state["adx_min"] and macd > sig and plus > minus:
            return Verdict("BUY", [f"MACD {macd:.2f} above signal {sig:.2f}",
                                   f"+DI {plus:.1f} above -DI {minus:.1f}",
                                   f"ADX {adx:.1f} above {state['adx_min']:g}"], r)
        if state["shorts"] and adx >= state["adx_min"] and macd < sig and minus > plus:
            return Verdict("SELL", [f"MACD below signal", f"-DI above +DI",
                                    f"ADX {adx:.1f} above {state['adx_min']:g}"], r)
        return Verdict("FLAT", [], r)


# ---------------------------------------------------------------------------
class AdxSwitchStrategy:
    """One market, two behaviours, chosen by ADX.

    This is the hypothesis the project has never tested: that trend-following
    and mean-reversion are not rivals but the correct answers to two different
    market states. When ADX says a trend exists, follow it. When ADX says the
    market is ranging, fade the extremes instead.
    """

    name = "ADX_SWITCH"
    DEFAULTS = {"adx_period": 14, "trend_above": 25.0, "range_below": 20.0,
                "fast": 20, "slow": 50, "z_period": 50, "entry_z": 2.0,
                "exit_z": 0.3, "trade_shorts": True}

    def describe(self, params=None):
        m = {**self.DEFAULTS, **(params or {})}
        return {"name": self.name, "label": "ADX regime switch",
                "description": "Follow the trend when there is one, fade the range when there is not.",
                "parameters": [{"key": k, "label": k, "type": "float",
                                "default": v, "value": m[k]} for k, v in self.DEFAULTS.items()]}

    def prepare(self, candles, params):
        closes = [c.close for c in candles]
        zp = int(_param(params, "z_period", 50, int))
        mid = ind.sma(closes, zp)
        sd = _stdev(closes, zp)
        return {"adx": ind.adx(candles, int(_param(params, "adx_period", 14, int))),
                "fast": ind.ema(closes, int(_param(params, "fast", 20, int))),
                "slow": ind.ema(closes, int(_param(params, "slow", 50, int))),
                "mid": mid, "sd": sd, "atr": ind.atr(candles, 14),
                "trend_above": _param(params, "trend_above", 25.0),
                "range_below": _param(params, "range_below", 20.0),
                "entry_z": _param(params, "entry_z", 2.0),
                "exit_z": _param(params, "exit_z", 0.3),
                "shorts": bool(params.get("trade_shorts", True))}

    def evaluate(self, candles, i, state):
        adx, fast, slow = state["adx"][i], state["fast"][i], state["slow"][i]
        mid, sd = state["mid"][i], state["sd"][i]
        if None in (adx, fast, slow, mid, sd) or sd == 0:
            return Verdict("FLAT", ["Insufficient history"])
        close = candles[i].close
        z = (close - mid) / sd
        r = {"adx": adx, "z": z, "ema_fast": fast, "ema_slow": slow, "atr": state["atr"][i]}

        if adx >= state["trend_above"]:
            if fast > slow and close > fast:
                return Verdict("BUY", [f"ADX {adx:.1f} - trending", "Price above a rising EMA pair"], r)
            if state["shorts"] and fast < slow and close < fast:
                return Verdict("SELL", [f"ADX {adx:.1f} - trending", "Price below a falling EMA pair"], r)
            return Verdict("FLAT", [], r)

        if adx <= state["range_below"]:
            if z <= -state["entry_z"]:
                return Verdict("BUY", [f"ADX {adx:.1f} - ranging",
                                       f"Price {z:.1f} deviations below the mean"], r)
            if state["shorts"] and z >= state["entry_z"]:
                return Verdict("SELL", [f"ADX {adx:.1f} - ranging",
                                        f"Price {z:.1f} deviations above the mean"], r)
            if abs(z) <= state["exit_z"]:
                return Verdict("EXIT", [f"Back at the mean ({z:.1f} deviations)"], r)
        return Verdict("FLAT", [], r)


# ---------------------------------------------------------------------------
class KeltnerRideStrategy:
    """Enter on a genuine thrust, hold until the middle breaks.

    A close more than `entry_atr` ATR above the middle band is evidence of a
    move with force behind it rather than drift. The exit is deliberately loose
    — the middle band — because the purpose is to test whether *holding* is what
    the earlier gold work suggested: exposure, not entry quality, is the edge.
    """

    name = "KELTNER_RIDE"
    DEFAULTS = {"ema_period": 20, "atr_period": 14, "entry_atr": 1.0,
                "trend_period": 200, "trade_shorts": True}

    def describe(self, params=None):
        m = {**self.DEFAULTS, **(params or {})}
        return {"name": self.name, "label": "Keltner channel ride",
                "description": "Enter on an ATR thrust, hold until the middle band breaks.",
                "parameters": [{"key": k, "label": k, "type": "float",
                                "default": v, "value": m[k]} for k, v in self.DEFAULTS.items()]}

    def prepare(self, candles, params):
        closes = [c.close for c in candles]
        return {"mid": ind.ema(closes, int(_param(params, "ema_period", 20, int))),
                "atr": ind.atr(candles, int(_param(params, "atr_period", 14, int))),
                "trend": ind.sma(closes, int(_param(params, "trend_period", 200, int))),
                "entry_atr": _param(params, "entry_atr", 1.0),
                "shorts": bool(params.get("trade_shorts", True))}

    def evaluate(self, candles, i, state):
        mid, atr, trend = state["mid"][i], state["atr"][i], state["trend"][i]
        if None in (mid, atr, trend):
            return Verdict("FLAT", ["Insufficient history"])
        close = candles[i].close
        k = state["entry_atr"]
        r = {"mid": mid, "atr": atr, "trend": trend,
             "upper": mid + k * atr, "lower": mid - k * atr}
        if close > mid + k * atr and close > trend:
            return Verdict("BUY", [f"Close {close:.2f} more than {k:g} ATR above the band",
                                   "Above the long trend"], r)
        if state["shorts"] and close < mid - k * atr and close < trend:
            return Verdict("SELL", [f"Close more than {k:g} ATR below the band",
                                    "Below the long trend"], r)
        return Verdict("EXIT", [f"Price back inside the channel"], r)


# ---------------------------------------------------------------------------
class TurtleLongShortStrategy:
    """The Donchian system as it was actually traded: both sides, ATR stop.

    The project's existing Donchian rule uses 55/20 and reverses on the opposite
    channel. What changes here is the exit — a 2 ATR stop plus a shorter 10-bar
    channel — which is what the original system used and which is the part that
    controls the drawdown the existing version suffers.
    """

    name = "TURTLE_LS"
    DEFAULTS = {"entry_lookback": 20, "exit_lookback": 10, "atr_period": 14,
                "trade_shorts": True}

    def describe(self, params=None):
        m = {**self.DEFAULTS, **(params or {})}
        return {"name": self.name, "label": "Turtle 20/10 long+short",
                "description": "20-bar channel entry, 10-bar channel exit, ATR stop.",
                "parameters": [{"key": k, "label": k, "type": "float",
                                "default": v, "value": m[k]} for k, v in self.DEFAULTS.items()]}

    def prepare(self, candles, params):
        return {"atr": ind.atr(candles, int(_param(params, "atr_period", 14, int))),
                "hi": [c.high for c in candles], "lo": [c.low for c in candles],
                "n": int(_param(params, "entry_lookback", 20, int)),
                "x": int(_param(params, "exit_lookback", 10, int)),
                "shorts": bool(params.get("trade_shorts", True))}

    def evaluate(self, candles, i, state):
        n, x = state["n"], state["x"]
        if i < n + 1 or state["atr"][i] is None:
            return Verdict("FLAT", ["Insufficient history"])
        close = candles[i].close
        eh, el = max(state["hi"][i - n:i]), min(state["lo"][i - n:i])
        xh, xl = max(state["hi"][i - x:i]), min(state["lo"][i - x:i])
        r = {"entryHigh": eh, "entryLow": el, "exitHigh": xh, "exitLow": xl,
             "atr": state["atr"][i]}
        if close > eh:
            return Verdict("BUY", [f"New {n}-bar high above {eh:.2f}"], r)
        if state["shorts"] and close < el:
            return Verdict("SELL", [f"New {n}-bar low below {el:.2f}"], r)
        if close < xl or close > xh:
            return Verdict("EXIT", [f"{x}-bar channel broken against the position"], r)
        return Verdict("FLAT", [], r)


for _s in (SqueezeBreakoutStrategy, MacdAdxStrategy, AdxSwitchStrategy,
           KeltnerRideStrategy, TurtleLongShortStrategy):
    STRATEGIES[_s.name] = _s()


def specs(tf):
    """The five new ideas, with the timeframe-appropriate context set."""
    return [
     ("Volatility squeeze breakout", dict(strategy="SQUEEZE_BREAKOUT", allow_short=True,
        trailing_stop_atr=2.5, strategy_parameters={"bb_period": 20, "squeeze_pct": 0.25,
        "rank_window": 250, "range_lookback": 20, "trend_period": 100, "trade_shorts": True})),
     ("MACD + ADX trend", dict(strategy="MACD_ADX", allow_short=True,
        strategy_parameters={"adx_min": 25.0, "adx_exit": 15.0, "trade_shorts": True})),
     ("ADX regime switch", dict(strategy="ADX_SWITCH", allow_short=True,
        stop_loss_atr=3.0, strategy_parameters={"trend_above": 25.0, "range_below": 20.0,
        "entry_z": 2.0, "exit_z": 0.3, "trade_shorts": True})),
     ("Keltner channel ride", dict(strategy="KELTNER_RIDE", allow_short=True,
        strategy_parameters={"ema_period": 20, "entry_atr": 1.0, "trend_period": 200,
        "trade_shorts": True})),
     ("Turtle 20/10 long+short", dict(strategy="TURTLE_LS", allow_short=True,
        stop_loss_atr=2.0, strategy_parameters={"entry_lookback": 20, "exit_lookback": 10,
        "trade_shorts": True})),
    ]


# ---------------------------------------------------------------------------
class DualRegimeStrategy:
    """Participation decided by two timeframes at once, with a dead band.

    Why this and not the existing SMA200 filter: that rule flips the moment
    price grazes its average, so a market oscillating around the line pays the
    spread over and over. Two changes address that directly — a percentage dead
    band price must clear before the regime is allowed to change, and a second,
    higher timeframe that has to agree. Neither adds a new indicator; both
    reduce the number of times the rule can be wrong for one bar.

    LONG    entry close above the entry-timeframe average by more than `buffer`,
            and the last COMPLETED higher-timeframe bar also above its average.
    EXIT    entry close below its average by more than `buffer`.
    SHORT   optional, the mirror image.
    """

    name = "DUAL_REGIME"
    DEFAULTS = {"period": 200, "buffer_pct": 0.5, "higher_minutes": 240,
                "higher_period": 100, "trade_shorts": False}

    def describe(self, params=None):
        m = {**self.DEFAULTS, **(params or {})}
        return {"name": self.name, "label": "Dual-timeframe regime filter",
                "description": "Hold the market only while two timeframes agree it is above trend.",
                "parameters": [{"key": k, "label": k, "type": "float",
                                "default": v, "value": m[k]} for k, v in self.DEFAULTS.items()]}

    def prepare(self, candles, params):
        from ftmo_mt5.signals.strategies import _bar_seconds, _higher_frame
        closes = [c.close for c in candles]
        base = _bar_seconds(candles)
        hm = int(_param(params, "higher_minutes", 240, int))
        higher, hidx = _higher_frame(candles, max(hm * 60, base))
        hsma = ind.sma([c.close for c in higher], int(_param(params, "higher_period", 100, int)))
        problems = []
        if len(higher) < 120:
            problems.append(f"Only {len(higher)} bars on the {hm}-minute timeframe - "
                            "the higher filter has little history to work with.")
        return {"sma": ind.sma(closes, int(_param(params, "period", 200, int))),
                "atr": ind.atr(candles, 14), "higher": higher, "hidx": hidx, "hsma": hsma,
                "buffer": _param(params, "buffer_pct", 0.5) / 100.0,
                "shorts": bool(params.get("trade_shorts", False)),
                "problems": problems}

    def evaluate(self, candles, i, state):
        sma = state["sma"][i]
        if sma is None:
            return Verdict("FLAT", ["Insufficient history"])
        close, b = candles[i].close, state["buffer"]
        r = {"sma": sma, "atr": state["atr"][i],
             "upper": sma * (1 + b), "lower": sma * (1 - b)}

        # The higher timeframe reads its last *completed* bar only.
        j = state["hidx"][i]
        hs = state["hsma"][j] if j >= 0 and j < len(state["hsma"]) else None
        if hs is None:
            return Verdict("FLAT", ["Higher timeframe has no average yet"], r)
        hclose = state["higher"][j].close
        r["higherClose"], r["higherSma"] = hclose, hs
        up, down = hclose > hs, hclose < hs

        if close > sma * (1 + b) and up:
            return Verdict("BUY", [f"Close {close:.2f} more than {b:.2%} above SMA {sma:.2f}",
                                   "Higher timeframe also above its average"], r)
        if state["shorts"] and close < sma * (1 - b) and down:
            return Verdict("SELL", [f"Close more than {b:.2%} below SMA {sma:.2f}",
                                    "Higher timeframe also below its average"], r)
        if close < sma * (1 - b) or (state["shorts"] and close > sma * (1 + b)):
            return Verdict("EXIT", [f"Close crossed the {b:.2%} band the other way"], r)
        return Verdict("FLAT", [], r)


STRATEGIES[DualRegimeStrategy.name] = DualRegimeStrategy()


# ---------------------------------------------------------------------------
class RegimeRatchetStrategy:
    """The regime filter, with a long entry that may not pay up.

    The rule the buffered regime filter follows is symmetric: short below the
    band, long above it. This variant adds one asymmetric condition asked for
    after reading a whipsaw sequence — a long is only allowed if it can be
    opened *below* the price at which the last short was opened. If price has
    risen above that level, the long is refused and the position goes flat.

    The intent is to refuse the second half of a whipsaw: short, price snaps
    back up, buy higher, lose again. The cost is that the same test refuses a
    genuine reversal, because a market turning from below its average into a
    sustained uptrend does exactly what a whipsaw does at the moment of the
    turn. Which effect dominates is an empirical question, not a design choice.

    The signal series is resolved once in `prepare`, walking bars forward in
    order, so the ratchet reference can never be set from a bar the decision
    had not yet reached.
    """

    name = "REGIME_RATCHET"
    DEFAULTS = {"regime_period": 200, "buffer_pct": 0.5, "rearm_bars": 0}

    def describe(self, params=None):
        m = {**self.DEFAULTS, **(params or {})}
        return {"name": self.name, "label": "Trend regime filter with buy-below-last-short",
                "description": ("Short below the band, long above it - but only if the long "
                                "can be opened below the last short's price."),
                "parameters": [
                    {"key": "regime_period", "label": "Regime average (bars)", "type": "int",
                     "default": 200, "value": m["regime_period"]},
                    {"key": "buffer_pct", "label": "Re-entry buffer %", "type": "float",
                     "default": 0.5, "value": m["buffer_pct"]},
                    {"key": "rearm_bars", "label": "Forget the reference after N bars "
                     "(0 = never)", "type": "int", "default": 0, "value": m["rearm_bars"]},
                ]}

    def prepare(self, candles, params):
        closes = [c.close for c in candles]
        period = int(_param(params, "regime_period", 200, int))
        buf = _param(params, "buffer_pct", 0.5) / 100.0
        rearm = int(_param(params, "rearm_bars", 0, int))
        sma = ind.sma(closes, period)

        signals, blocked = [], 0
        last_short = None          # close at which the most recent short opened
        since = 0                  # bars since that short opened
        held = None                # what the rule believes it is holding

        for i, c in enumerate(candles):
            line = sma[i]
            if line is None:
                signals.append(("FLAT", None)); continue
            close = c.close
            if last_short is not None:
                since += 1
                if rearm and since > rearm:
                    last_short = None

            if close < line * (1 - buf):
                if held != "SHORT":
                    last_short, since, held = close, 0, "SHORT"
                signals.append(("SELL", None))
            elif close > line * (1 + buf):
                if last_short is not None and close >= last_short:
                    # The long would be opened above the short it is replacing.
                    if held is not None:
                        held = None
                        signals.append(("EXIT", f"Long refused: {close:.2f} is not below "
                                                f"the last short at {last_short:.2f}"))
                    else:
                        signals.append(("FLAT", f"Long refused at {close:.2f}"))
                    blocked += 1
                else:
                    held = "LONG"
                    signals.append(("BUY", None))
            else:
                signals.append(("FLAT", None))

        return {"sma": sma, "signals": signals, "period": period, "buffer": buf,
                "blockedLongs": blocked,
                "problems": ([] if blocked else
                             ["The buy-below-last-short test never refused an entry, so this "
                              "run is identical to the plain regime filter."])}

    def evaluate(self, candles, i, state):
        sig, why = state["signals"][i]
        line = state["sma"][i]
        r = {} if line is None else {
            "sma": line, "lower": line * (1 - state["buffer"]),
            "upper": line * (1 + state["buffer"]),
            "distance_pct": (candles[i].close / line - 1) * 100}
        return Verdict(sig, [why] if why else [], r)


STRATEGIES[RegimeRatchetStrategy.name] = RegimeRatchetStrategy()



