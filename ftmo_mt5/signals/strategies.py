"""Pluggable strategies.

A strategy is two methods:

    prepare(candles, params) -> state      computed once over the whole series
    evaluate(candles, i, state) -> Verdict decided for bar i alone

`prepare` exists so indicators are computed once per run instead of once per
bar, and it is safe precisely because every indicator series is index-aligned:
`state` at index i only ever contains values derived from candles 0..i.

Adding a strategy means writing a class with those two methods and registering
it in `STRATEGIES`. The engine, the backtest and the panel all pick it up with
no further changes.
"""

__all__ = ["Strategy", "Verdict", "STRATEGIES", "get_strategy", "list_strategies"]

from dataclasses import dataclass, field
from typing import Any, Optional, Protocol, Sequence

from ftmo_mt5.marketdata.base import Candle
from ftmo_mt5.signals import indicators as ind
from ftmo_mt5.signals import patterns as pat


@dataclass
class Verdict:
    """A strategy's decision at one bar."""

    # BUY / SELL open a position; EXIT closes an open one without opening
    # anything (which is what distinguishes it from SELL); FLAT is no opinion
    # and leaves whatever is open alone.
    signal: str = "FLAT"                       # BUY | SELL | EXIT | FLAT
    reasons: list[str] = field(default_factory=list)
    indicators: dict[str, Any] = field(default_factory=dict)
    # A stop the rule itself chose — "below the most recent swing low" is a
    # property of the setup, not of the run, so the strategy is the only thing
    # that can know it. The run's own stop still applies; whichever is tighter
    # wins, exactly as a fixed stop and a trailing stop already resolve.
    stop_price: Optional[float] = None


class Strategy(Protocol):
    name: str

    def describe(self, params: dict) -> dict:
        """Name, description and the parameters this strategy accepts."""

    def prepare(self, candles: Sequence[Candle], params: dict) -> dict:
        """Precompute index-aligned indicator series for the whole run.

        A `"problems"` key holding a list of strings is surfaced by the backtest
        as run warnings. Use it when the configuration cannot be honoured — a
        strategy that quietly returns FLAT for every bar is indistinguishable
        from one that simply found nothing.
        """

    def evaluate(self, candles: Sequence[Candle], i: int, state: dict) -> Verdict:
        """Decide bar `i` using only state derived from candles 0..i."""


def _param(params: dict, key: str, default, cast=float):
    """Read one parameter, falling back to the default on anything unusable."""
    if not params or key not in params or params[key] in (None, ""):
        return default
    try:
        return cast(params[key])
    except (TypeError, ValueError):
        return default


class EmaRsiPatternStrategy:
    """Trend alignment (EMA), momentum confirmation (RSI), candlestick trigger.

    BUY when price is above the fast EMA, the fast EMA is above the slow EMA,
    RSI is above its buy threshold, and a bullish pattern prints on the bar.
    SELL is the mirror image against the sell threshold. Anything else is FLAT.

    The two RSI thresholds are independent and deliberately wide by default
    (buy above 30, sell below 65): momentum is used to veto the extremes rather
    than to pick a side. Direction comes from the EMA pair, which is what keeps
    the two rules mutually exclusive even where the RSI bands overlap.

    These are the initial default rules, not a fixed definition of the system:
    every period, threshold and the pattern requirement itself is a parameter.
    """

    name = "EMA_RSI_PATTERN"

    DEFAULTS = {
        "fast_period": 20,
        "slow_period": 50,
        "rsi_period": 14,
        "rsi_buy_above": 30.0,
        "rsi_sell_below": 65.0,
        "atr_period": 14,
        "require_pattern": True,
    }

    def describe(self, params: Optional[dict] = None) -> dict:
        merged = {**self.DEFAULTS, **(params or {})}
        return {
            "name": self.name,
            "label": "EMA + RSI + Candlestick pattern",
            "description": ("Trend from a fast/slow EMA pair, momentum from RSI, "
                            "entry timed by a candlestick pattern."),
            "parameters": [
                {"key": "fast_period", "label": "Fast EMA", "type": "int",
                 "default": self.DEFAULTS["fast_period"], "value": merged["fast_period"]},
                {"key": "slow_period", "label": "Slow EMA", "type": "int",
                 "default": self.DEFAULTS["slow_period"], "value": merged["slow_period"]},
                {"key": "rsi_period", "label": "RSI period", "type": "int",
                 "default": self.DEFAULTS["rsi_period"], "value": merged["rsi_period"]},
                {"key": "rsi_buy_above", "label": "RSI buy above", "type": "float",
                 "default": self.DEFAULTS["rsi_buy_above"], "value": merged["rsi_buy_above"]},
                {"key": "rsi_sell_below", "label": "RSI sell below", "type": "float",
                 "default": self.DEFAULTS["rsi_sell_below"], "value": merged["rsi_sell_below"]},
                {"key": "atr_period", "label": "ATR period", "type": "int",
                 "default": self.DEFAULTS["atr_period"], "value": merged["atr_period"]},
                {"key": "require_pattern", "label": "Require candlestick pattern",
                 "type": "bool", "default": True, "value": bool(merged["require_pattern"])},
            ],
        }

    def prepare(self, candles: Sequence[Candle], params: dict) -> dict:
        closes = [c.close for c in candles]
        fast = int(_param(params, "fast_period", self.DEFAULTS["fast_period"], int))
        slow = int(_param(params, "slow_period", self.DEFAULTS["slow_period"], int))
        rsi_p = int(_param(params, "rsi_period", self.DEFAULTS["rsi_period"], int))
        atr_p = int(_param(params, "atr_period", self.DEFAULTS["atr_period"], int))

        require = params.get("require_pattern", self.DEFAULTS["require_pattern"])
        if isinstance(require, str):
            require = require.strip().lower() not in ("0", "false", "no", "")

        return {
            "ema_fast": ind.ema(closes, fast),
            "ema_slow": ind.ema(closes, slow),
            "rsi": ind.rsi(closes, rsi_p),
            "atr": ind.atr(candles, atr_p),
            "fast_period": fast,
            "slow_period": slow,
            "rsi_buy_above": _param(params, "rsi_buy_above", self.DEFAULTS["rsi_buy_above"]),
            "rsi_sell_below": _param(params, "rsi_sell_below", self.DEFAULTS["rsi_sell_below"]),
            "require_pattern": bool(require),
        }

    def evaluate(self, candles: Sequence[Candle], i: int, state: dict) -> Verdict:
        fast = state["ema_fast"][i]
        slow = state["ema_slow"][i]
        rsi_now = state["rsi"][i]
        atr_now = state["atr"][i]

        # Not enough history yet — no opinion, and importantly no default BUY.
        if fast is None or slow is None or rsi_now is None:
            return Verdict("FLAT", ["Insufficient history for indicators"])

        close = candles[i].close
        found = pat.detect_all(candles, i)
        bullish = [p for p in found if p in pat.BULLISH]
        bearish = [p for p in found if p in pat.BEARISH]

        readings = {
            "ema_fast": fast, "ema_slow": slow, "rsi": rsi_now,
            "atr": atr_now, "patterns": found,
        }

        want_pattern = state["require_pattern"]
        buy_above = state["rsi_buy_above"]
        sell_below = state["rsi_sell_below"]

        long_conditions = [
            (close > fast, f"Price {close:.2f} above EMA{state['fast_period']} {fast:.2f}"),
            (fast > slow, f"EMA{state['fast_period']} above EMA{state['slow_period']}"),
            (rsi_now > buy_above, f"RSI {rsi_now:.1f} above {buy_above:g}"),
            (bool(bullish) or not want_pattern,
             f"Bullish pattern: {', '.join(bullish)}" if bullish else "Pattern not required"),
        ]
        if all(ok for ok, _ in long_conditions):
            return Verdict("BUY", [why for _, why in long_conditions], readings)

        short_conditions = [
            (close < fast, f"Price {close:.2f} below EMA{state['fast_period']} {fast:.2f}"),
            (fast < slow, f"EMA{state['fast_period']} below EMA{state['slow_period']}"),
            (rsi_now < sell_below, f"RSI {rsi_now:.1f} below {sell_below:g}"),
            (bool(bearish) or not want_pattern,
             f"Bearish pattern: {', '.join(bearish)}" if bearish else "Pattern not required"),
        ]
        if all(ok for ok, _ in short_conditions):
            return Verdict("SELL", [why for _, why in short_conditions], readings)

        return Verdict("FLAT", [], readings)


STRATEGIES: dict[str, Strategy] = {
    EmaRsiPatternStrategy.name: EmaRsiPatternStrategy(),
}

# Registered but not offered: `list_strategies()` is what the panel renders, so
# a name in here disappears from the UI and stops being anybody's default while
# staying resolvable by name for existing runs and tests. Deleting the line
# below is all it takes to put EMA_RSI_PATTERN back on the menu.
HIDDEN: set[str] = {"EMA_RSI_PATTERN"}


def get_strategy(name: str) -> Strategy:
    key = (name or "MTF_GREEN_STACK").strip().upper()
    strategy = STRATEGIES.get(key)
    if strategy is None:
        raise ValueError(
            f"Unknown strategy '{name}'. Available: {', '.join(sorted(STRATEGIES))}."
        )
    return strategy


def list_strategies() -> list[dict]:
    """What the panel offers, in registration order, hidden entries removed."""
    return [s.describe({}) for k, s in STRATEGIES.items() if k not in HIDDEN]


# ---------------------------------------------------------------------------
# Strategy 2 — multi-timeframe green-bar stack
# ---------------------------------------------------------------------------

def _bar_seconds(candles: Sequence[Candle]) -> int:
    """The base timeframe's bar length, read off the data itself.

    A strategy is handed candles, not a timeframe label, so the spacing has to
    be inferred. The most common gap wins rather than the average: session
    breaks and weekends leave huge gaps that would drag a mean far off.
    """
    gaps: dict[int, int] = {}
    for a, b in zip(candles, candles[1:]):
        d = int(b.timestamp - a.timestamp)
        if d > 0:
            gaps[d] = gaps.get(d, 0) + 1
    return max(gaps, key=gaps.get) if gaps else 60


def _fmt_tf(seconds: int) -> str:
    """Seconds as the timeframe a trader would name: 5m, 1h, 1d, 1w."""
    for size, suffix in ((604800, "w"), (86400, "d"), (3600, "h"), (60, "m")):
        if seconds >= size and seconds % size == 0:
            return f"{seconds // size}{suffix}"
    return f"{seconds}s"


def _green_series(candles: Sequence[Candle], seconds: int,
                  completed: bool) -> list[Optional[bool]]:
    """Index-aligned: is the `seconds`-long bar covering candle i green?

    Two readings, because traders mean both things by "the 1h bar is green":

    * forming (default) — the higher bar currently being built. Its open is the
      first base bar's open in the bucket, its close is the latest base close.
      This is what three live charts show side by side.
    * completed — the last higher bar that has actually finished. Slower and
      one bar behind, but it can never flip against you mid-bar.

    Either way only candles 0..i are read, so there is no look-ahead.
    """
    out: list[Optional[bool]] = []
    bucket = b_open = b_close = None
    last_done: Optional[bool] = None

    for c in candles:
        b = int(c.timestamp) // seconds
        if bucket is None:
            bucket, b_open = b, c.open
        elif b != bucket:
            last_done = b_close > b_open          # the bucket that just ended
            bucket, b_open = b, c.open
        b_close = c.close
        out.append(last_done if completed else b_close > b_open)
    return out


class MultiTimeframeGreenStrategy:
    """Trade only when every configured timeframe agrees on direction.

    One idea, no indicators: agreement across timeframes. A 5-minute bar can be
    green inside a falling hour; this refuses to act until the short, medium and
    long views all point the same way at the same instant.

    Long and short are the same rule read in both directions — every bar green
    is a long, every bar red is a short. Anything mixed is not a weaker signal,
    it is the absence of one, and the position comes off.

    The exit is the entry condition failing. The moment any one timeframe stops
    printing green the reason for being long is gone, so the position is closed
    — not reversed, since one red bar is not evidence for a short. A stop or a
    target can still be configured for the run and will be honoured, but this
    rule does not need either to close a position.

    The timeframes are parameters, and the higher ones are built by grouping the
    base bars the run is already using — so a run on 5-minute data can confirm
    against 15m and 1h without a second data feed. Anything shorter than the
    base timeframe cannot be built and is reported rather than faked.
    """

    name = "MTF_GREEN_STACK"

    DEFAULTS = {
        "tf1_minutes": 5,
        "tf2_minutes": 15,
        "tf3_minutes": 60,
        "require_completed": False,
        "exit_on_condition": True,
        "trade_shorts": True,
    }

    def describe(self, params: Optional[dict] = None) -> dict:
        merged = {**self.DEFAULTS, **(params or {})}
        return {
            "name": self.name,
            "label": "Multi-timeframe green stack (5m + 15m + 1h)",
            "description": ("Long when every timeframe is green, short when every "
                            "one is red, flat when they disagree. The position "
                            "closes as soon as the timeframes stop agreeing; "
                            "switch that off to let the trailing stop be the only "
                            "exit."),
            "parameters": [
                {"key": "tf1_minutes", "label": "Timeframe 1 (minutes)", "type": "int",
                 "default": 5, "value": merged["tf1_minutes"]},
                {"key": "tf2_minutes", "label": "Timeframe 2 (minutes)", "type": "int",
                 "default": 15, "value": merged["tf2_minutes"]},
                {"key": "tf3_minutes", "label": "Timeframe 3 (minutes)", "type": "int",
                 "default": 60, "value": merged["tf3_minutes"]},
                {"key": "require_completed", "label": "Use completed higher bars only",
                 "type": "bool", "default": False,
                 "value": bool(merged["require_completed"])},
                {"key": "exit_on_condition",
                 "label": "Exit when the stack breaks (off = trailing stop only)",
                 "type": "bool", "default": True,
                 "value": bool(merged["exit_on_condition"])},
                {"key": "trade_shorts",
                 "label": "Short when every timeframe is red",
                 "type": "bool", "default": True,
                 "value": bool(merged["trade_shorts"])},
            ],
        }

    @staticmethod
    def _flag(params: dict, key: str, default: bool) -> bool:
        value = params.get(key, default)
        if isinstance(value, str):
            return value.strip().lower() not in ("0", "false", "no", "")
        return bool(value)

    def prepare(self, candles: Sequence[Candle], params: dict) -> dict:
        completed = self._flag(params, "require_completed",
                               self.DEFAULTS["require_completed"])
        exit_on_condition = self._flag(params, "exit_on_condition",
                                       self.DEFAULTS["exit_on_condition"])
        trade_shorts = self._flag(params, "trade_shorts",
                                  self.DEFAULTS["trade_shorts"])

        base = _bar_seconds(candles)
        wanted = []
        for key in ("tf1_minutes", "tf2_minutes", "tf3_minutes"):
            minutes = int(_param(params, key, self.DEFAULTS[key], int))
            if minutes > 0:
                wanted.append(minutes)

        legs, problems = [], []
        for minutes in sorted(set(wanted)):
            seconds = minutes * 60
            if seconds < base:
                # Refuse rather than substitute: a 5-minute confirmation read off
                # hourly bars is not a 5-minute confirmation.
                problems.append(
                    f"No signals will be generated: {_fmt_tf(seconds)} "
                    f"confirmation cannot be built from {_fmt_tf(base)} bars — "
                    f"a lower timeframe cannot be recovered from a higher one. "
                    f"Either run this backtest on {_fmt_tf(seconds)} data or "
                    f"lower, or set the timeframe parameters to "
                    f"{_fmt_tf(base)} or above ({base // 60} minutes or more)."
                )
                continue
            legs.append({
                "minutes": minutes,
                "green": _green_series(candles, seconds, completed),
            })

        return {"legs": legs, "problems": problems, "base_minutes": base // 60,
                "base_label": _fmt_tf(base), "completed": bool(completed),
                "exit_on_condition": exit_on_condition,
                "trade_shorts": trade_shorts}

    def evaluate(self, candles: Sequence[Candle], i: int, state: dict) -> Verdict:
        # One unusable leg disables the whole rule. Quietly running a
        # three-timeframe stack on whichever legs happened to survive would be
        # a different strategy wearing this one's name.
        if state["problems"]:
            return Verdict("FLAT", state["problems"])
        if not state["legs"]:
            return Verdict("FLAT", ["No timeframes configured"])

        # Read every leg before deciding. Returning on the first disagreement
        # would make "all red" indistinguishable from "mixed", and those are
        # opposite instructions.
        readings = {}
        for leg in state["legs"]:
            green = leg["green"][i]
            label = _fmt_tf(leg["minutes"] * 60)
            readings[label] = green
            if green is None:
                # Warm-up, not disagreement: there is no higher bar to read yet,
                # so this is no opinion rather than a reason to close.
                return Verdict("FLAT", [f"{label} bar not complete yet"], readings)

        if all(readings.values()):
            return Verdict("BUY", [f"{k} bar is green" for k in readings], readings)

        if not any(readings.values()):
            if state["trade_shorts"]:
                return Verdict("SELL", [f"{k} bar is red" for k in readings], readings)
            # Shorts switched off: unanimous red is still a reason not to be
            # long, it is simply not acted on in the other direction.
            return Verdict("EXIT" if state["exit_on_condition"] else "FLAT",
                           ["every timeframe is red — shorts are switched off"],
                           readings)

        # Mixed. Not a weak signal — no signal. Whatever is open comes off.
        disagree = [k for k, v in readings.items() if not v]
        why = "timeframes disagree — " + ", ".join(f"{k} is red" for k in disagree)
        if not state["exit_on_condition"]:
            return Verdict("FLAT", [why], readings)
        return Verdict("EXIT", [why], readings)


STRATEGIES[MultiTimeframeGreenStrategy.name] = MultiTimeframeGreenStrategy()


# ---------------------------------------------------------------------------
# Strategy 3 — EMA/RSI pullback with a higher-timeframe trend filter
# ---------------------------------------------------------------------------

def _higher_frame(candles: Sequence[Candle], seconds: int) -> tuple[list[Candle], list[int]]:
    """Build a higher timeframe, and map each base bar to the last COMPLETED one.

    The trend filter reads a finished higher bar, not the one still forming. A
    forming bar's EMA changes with every base bar inside it, so a filter read
    that way flickers on and off within the hour and is not what an EA watching
    "the 1H trend" actually sees. `index[i]` is the position in the higher list
    of the last bar that had closed by base bar i, or -1 if none has yet.
    """
    higher: list[Candle] = []
    index: list[int] = []
    bucket = b_open = b_high = b_low = b_close = None

    for c in candles:
        b = int(c.timestamp) // seconds
        if bucket is None:
            bucket, b_open, b_high, b_low = b, c.open, c.high, c.low
        elif b != bucket:
            higher.append(Candle(timestamp=bucket * seconds, open=b_open,
                                 high=b_high, low=b_low, close=b_close, volume=0.0))
            bucket, b_open, b_high, b_low = b, c.open, c.high, c.low
        else:
            b_high, b_low = max(b_high, c.high), min(b_low, c.low)
        b_close = c.close
        index.append(len(higher) - 1)
    return higher, index


class EmaRsiPullbackStrategy:
    """Buy a pullback to the moving averages inside an established uptrend.

    Two timeframes with different jobs. The higher one decides whether to be
    looking for longs at all: price above its slow average, fast average above
    slow, momentum above the midline. The lower one times the entry: price pulls
    back into the moving-average band, momentum dips, then recovers through the
    midline on a bar that closes up.

    Two things the specification does not say, and which are therefore the run's
    to decide rather than this rule's:

    * **There is no exit.** The rule describes entries only, so the position is
      closed by whatever stop, target or trailing stop the backtest configures.
      With none of them set, a position runs to the end of the data.
    * **There are no shorts.** It is a long-only rule as written; the mirror is
      not implied and is not invented here.
    """

    name = "EMA_RSI_PULLBACK"

    DEFAULTS = {
        "trend_minutes": 60,        # the higher timeframe
        "trend_fast": 50,           # 1H EMA 50
        "trend_slow": 200,          # 1H EMA 200
        "trend_rsi_period": 14,
        "trend_rsi_above": 52.0,
        "entry_fast": 20,           # 15M EMA 20
        "entry_slow": 50,           # 15M EMA 50
        "entry_rsi_period": 14,
        "rsi_dip_below": 45.0,      # "RSI was below 45"
        "rsi_cross_above": 50.0,    # "RSI crosses above 50"
        "dip_lookback": 10,         # how recently the dip must have happened
        "require_touch": True,      # "price touches EMA 20/50"
    }

    def describe(self, params: Optional[dict] = None) -> dict:
        m = {**self.DEFAULTS, **(params or {})}
        p = lambda k, label, t="int": {"key": k, "label": label, "type": t,
                                       "default": self.DEFAULTS[k], "value": m[k]}
        return {
            "name": self.name,
            "label": "EMA + RSI pullback (1H trend, 15M entry)",
            "description": ("Long only. The higher timeframe must be in an uptrend "
                            "with momentum above its threshold; the entry timeframe "
                            "must pull back into the moving-average band, dip, then "
                            "cross back above the midline on an up bar. The rule has "
                            "no exit of its own — set a stop, target or trailing stop."),
            "parameters": [
                p("trend_minutes", "Trend timeframe (minutes)"),
                p("trend_fast", "Trend fast EMA"),
                p("trend_slow", "Trend slow EMA"),
                p("trend_rsi_above", "Trend RSI above", "float"),
                p("entry_fast", "Entry fast EMA"),
                p("entry_slow", "Entry slow EMA"),
                p("rsi_dip_below", "RSI must have dipped below", "float"),
                p("rsi_cross_above", "RSI crosses above", "float"),
                p("dip_lookback", "Dip within (bars)"),
                {"key": "require_touch", "label": "Require touch of the EMA band",
                 "type": "bool", "default": True, "value": bool(m["require_touch"])},
            ],
        }

    def prepare(self, candles: Sequence[Candle], params: dict) -> dict:
        g = lambda k, cast=int: cast(_param(params, k, self.DEFAULTS[k], cast))
        trend_minutes = g("trend_minutes")
        base = _bar_seconds(candles)
        problems = []
        if trend_minutes * 60 < base:
            problems.append(
                f"No signals will be generated: a {_fmt_tf(trend_minutes*60)} trend "
                f"filter cannot be built from {_fmt_tf(base)} bars — a lower "
                f"timeframe cannot be recovered from a higher one."
            )

        higher, hidx = _higher_frame(candles, max(trend_minutes * 60, base))
        hcloses = [c.close for c in higher]
        closes = [c.close for c in candles]

        touch = params.get("require_touch", self.DEFAULTS["require_touch"])
        if isinstance(touch, str):
            touch = touch.strip().lower() not in ("0", "false", "no", "")

        return {
            "problems": problems,
            "hidx": hidx,
            "h_fast": ind.ema(hcloses, g("trend_fast")),
            "h_slow": ind.ema(hcloses, g("trend_slow")),
            "h_rsi": ind.rsi(hcloses, g("trend_rsi_period")),
            "h_close": hcloses,
            "e_fast": ind.ema(closes, g("entry_fast")),
            "e_slow": ind.ema(closes, g("entry_slow")),
            "e_rsi": ind.rsi(closes, g("entry_rsi_period")),
            "atr": ind.atr(candles, 14),
            "trend_rsi_above": g("trend_rsi_above", float),
            "dip_below": g("rsi_dip_below", float),
            "cross_above": g("rsi_cross_above", float),
            "lookback": g("dip_lookback"),
            "require_touch": bool(touch),
        }

    def evaluate(self, candles: Sequence[Candle], i: int, state: dict) -> Verdict:
        if state["problems"]:
            return Verdict("FLAT", state["problems"])

        h = state["hidx"][i]
        if h < 0:
            return Verdict("FLAT", ["No completed trend bar yet"])
        hf, hs, hr = state["h_fast"][h], state["h_slow"][h], state["h_rsi"][h]
        ef, es, er = state["e_fast"][i], state["e_slow"][i], state["e_rsi"][i]
        if None in (hf, hs, hr, ef, es, er):
            return Verdict("FLAT", ["Indicators still warming up"])

        bar = candles[i]
        readings = {"trend_close": state["h_close"][h], "trend_fast": hf,
                    "trend_slow": hs, "trend_rsi": hr, "ema_fast": ef,
                    "ema_slow": es, "rsi": er, "atr": state["atr"][i]}

        # --- the higher timeframe decides whether to look for longs at all ---
        trend = [
            (state["h_close"][h] > hs, "trend price above slow EMA"),
            (hf > hs, "trend fast EMA above slow EMA"),
            (hr > state["trend_rsi_above"], f"trend RSI {hr:.1f} above {state['trend_rsi_above']:g}"),
        ]
        failed = [why for ok, why in trend if not ok]
        if failed:
            return Verdict("FLAT", ["trend filter: " + failed[0]], readings)

        # --- the entry timeframe times it -----------------------------------
        if state["require_touch"]:
            lo, hi = min(ef, es), max(ef, es)
            if not (bar.low <= hi and bar.high >= lo):
                return Verdict("FLAT", ["price did not reach the EMA band"], readings)

        prev = state["e_rsi"][i - 1] if i else None
        if prev is None or not (prev <= state["cross_above"] < er):
            return Verdict("FLAT", ["RSI did not cross the midline on this bar"], readings)

        window = state["e_rsi"][max(0, i - state["lookback"]):i + 1]
        if not any(v is not None and v < state["dip_below"] for v in window):
            return Verdict("FLAT",
                           [f"RSI never dipped below {state['dip_below']:g} recently"],
                           readings)

        if bar.close <= bar.open:
            return Verdict("FLAT", ["entry bar did not close up"], readings)

        return Verdict("BUY", [
            "trend: price and fast EMA above the slow EMA, RSI above threshold",
            "pullback into the EMA band",
            f"RSI dipped below {state['dip_below']:g} then crossed {state['cross_above']:g}",
            "bullish close",
        ], readings)


STRATEGIES[EmaRsiPullbackStrategy.name] = EmaRsiPullbackStrategy()


# ---------------------------------------------------------------------------
# Strategy 4 — trend-regime pullback with a trend-following exit
# ---------------------------------------------------------------------------

class TrendRegimePullbackStrategy:
    """The pullback rule again, but with a regime test and an exit of its own.

    Three things separate this from the plain pullback: the higher timeframe
    must be *turning* as well as aligned (its slow average has to be rising, not
    merely below price), the position is closed by the entry timeframe losing
    its own trend rather than by a fixed target, and the initial stop is placed
    at the setup's own swing low instead of an arbitrary distance.

    That last one is why `Verdict` carries a stop price. "Below the most recent
    swing low" is a fact about this particular setup; nothing outside the rule
    can know where that is.
    """

    name = "TREND_REGIME_PULLBACK"

    DEFAULTS = {
        "trend_minutes": 60,
        "trend_fast": 50,
        "trend_slow": 200,
        "trend_rsi_above": 50.0,
        "slope_lookback": 5,        # bars used to call the slow EMA "rising"
        "entry_fast": 20,
        "entry_slow": 50,
        "rsi_dip_below": 45.0,
        "rsi_cross_above": 50.0,
        "dip_lookback": 10,
        "exit_rsi_below": 45.0,     # exit: RSI under this AND close under EMA 50
        "swing_lookback": 20,       # how far back to look for the swing low
        "swing_radius": 2,          # bars either side that define a swing low
        "stop_buffer_pct": 0.0,     # extra room under the swing low
    }

    def describe(self, params: Optional[dict] = None) -> dict:
        m = {**self.DEFAULTS, **(params or {})}
        p = lambda k, label, t="int": {"key": k, "label": label, "type": t,
                                       "default": self.DEFAULTS[k], "value": m[k]}
        return {
            "name": self.name,
            "label": "Trend regime pullback (1H regime, 15M entry and exit)",
            "description": ("Long only. The higher timeframe must be in an uptrend "
                            "that is still turning upward; the entry timeframe must "
                            "pull back, dip and recover. The position closes when the "
                            "entry timeframe loses its own trend, and the initial stop "
                            "sits below the setup's swing low."),
            "parameters": [
                p("trend_minutes", "Trend timeframe (minutes)"),
                p("trend_fast", "Trend fast EMA"),
                p("trend_slow", "Trend slow EMA"),
                p("trend_rsi_above", "Trend RSI above", "float"),
                p("slope_lookback", "Slow EMA rising over (bars)"),
                p("entry_fast", "Entry fast EMA"),
                p("entry_slow", "Entry slow EMA"),
                p("rsi_dip_below", "RSI must have dipped below", "float"),
                p("rsi_cross_above", "RSI crosses above", "float"),
                p("dip_lookback", "Dip within (bars)"),
                p("exit_rsi_below", "Exit when RSI below", "float"),
                p("swing_lookback", "Swing low search (bars)"),
                p("swing_radius", "Swing low radius (bars)"),
                p("stop_buffer_pct", "Extra room under the swing low (%)", "float"),
            ],
        }

    def prepare(self, candles: Sequence[Candle], params: dict) -> dict:
        g = lambda k, cast=int: cast(_param(params, k, self.DEFAULTS[k], cast))
        trend_minutes = g("trend_minutes")
        base = _bar_seconds(candles)
        problems = []
        if trend_minutes * 60 < base:
            problems.append(
                f"No signals will be generated: a {_fmt_tf(trend_minutes*60)} regime "
                f"filter cannot be built from {_fmt_tf(base)} bars."
            )

        higher, hidx = _higher_frame(candles, max(trend_minutes * 60, base))
        hcloses = [c.close for c in higher]
        closes = [c.close for c in candles]

        return {
            "problems": problems, "hidx": hidx, "h_close": hcloses,
            "h_fast": ind.ema(hcloses, g("trend_fast")),
            "h_slow": ind.ema(hcloses, g("trend_slow")),
            "h_rsi": ind.rsi(hcloses, 14),
            "e_fast": ind.ema(closes, g("entry_fast")),
            "e_slow": ind.ema(closes, g("entry_slow")),
            "e_rsi": ind.rsi(closes, 14),
            "trend_rsi_above": g("trend_rsi_above", float),
            "slope": g("slope_lookback"),
            "dip_below": g("rsi_dip_below", float),
            "cross_above": g("rsi_cross_above", float),
            "lookback": g("dip_lookback"),
            "exit_rsi": g("exit_rsi_below", float),
            "swing_lookback": g("swing_lookback"),
            "swing_radius": g("swing_radius"),
            "buffer": g("stop_buffer_pct", float),
        }

    @staticmethod
    def _swing_low(candles: Sequence[Candle], i: int, lookback: int, radius: int):
        """The most recent low with `radius` higher lows either side of it.

        Searched backwards from the bar before this one, and only over bars that
        have already closed — a swing low needs bars on both sides to exist, so
        the newest one that qualifies is always at least `radius` bars old.
        """
        for k in range(i - radius, max(0, i - lookback) - 1, -1):
            if k - radius < 0:
                break
            window = candles[k - radius:k + radius + 1]
            if all(c.low >= candles[k].low for c in window):
                return candles[k].low
        return None

    def evaluate(self, candles: Sequence[Candle], i: int, state: dict) -> Verdict:
        if state["problems"]:
            return Verdict("FLAT", state["problems"])

        h = state["hidx"][i]
        if h < 0:
            return Verdict("FLAT", ["No completed regime bar yet"])
        hf, hs, hr = state["h_fast"][h], state["h_slow"][h], state["h_rsi"][h]
        ef, es, er = state["e_fast"][i], state["e_slow"][i], state["e_rsi"][i]
        if None in (hf, hs, hr, ef, es, er):
            return Verdict("FLAT", ["Indicators still warming up"])

        bar = candles[i]
        readings = {"trend_close": state["h_close"][h], "trend_fast": hf,
                    "trend_slow": hs, "trend_rsi": hr, "ema_fast": ef,
                    "ema_slow": es, "rsi": er}

        # --- exit first: a rule that can close should be asked before it opens
        if bar.close < es and er < state["exit_rsi"]:
            return Verdict("EXIT", [
                f"close {bar.close:.2f} below EMA{state['lookback'] and ''}slow {es:.2f}",
                f"RSI {er:.1f} below {state['exit_rsi']:g}",
            ], readings)

        # --- regime -----------------------------------------------------
        prior = state["h_slow"][max(0, h - state["slope"])]
        rising = prior is not None and hs > prior
        regime = [
            (state["h_close"][h] > hs, "price above the slow EMA"),
            (hf > hs, "fast EMA above slow EMA"),
            (rising, "slow EMA is rising"),
            (hr > state["trend_rsi_above"], f"RSI {hr:.1f} above {state['trend_rsi_above']:g}"),
        ]
        failed = [why for ok, why in regime if not ok]
        if failed:
            return Verdict("FLAT", ["regime: " + failed[0]], readings)

        # --- pullback ---------------------------------------------------
        lo, hi = min(ef, es), max(ef, es)
        if not (bar.low <= hi and bar.high >= lo):
            return Verdict("FLAT", ["price did not reach the EMA zone"], readings)

        prev = state["e_rsi"][i - 1] if i else None
        if prev is None or not (prev <= state["cross_above"] < er):
            return Verdict("FLAT", ["RSI did not cross back above the midline"], readings)

        window = state["e_rsi"][max(0, i - state["lookback"]):i + 1]
        if not any(v is not None and v < state["dip_below"] for v in window):
            return Verdict("FLAT", [f"RSI never dipped below {state['dip_below']:g}"],
                           readings)

        if bar.close <= bar.open:
            return Verdict("FLAT", ["entry candle did not close bullish"], readings)

        # --- the stop belongs to this setup ------------------------------
        swing = self._swing_low(candles, i, state["swing_lookback"], state["swing_radius"])
        stop = None
        if swing is not None:
            stop = swing * (1 - state["buffer"] / 100.0)
            readings["swing_low"] = swing
            if stop >= bar.close:
                stop = None       # the swing is not below us; leave it to the run

        return Verdict("BUY", [
            "regime: uptrend, aligned and still rising",
            "pullback into the EMA zone",
            f"RSI dipped below {state['dip_below']:g} then crossed {state['cross_above']:g}",
            "bullish close",
            f"stop at the swing low {swing:.2f}" if swing else "no swing low found",
        ], readings, stop_price=stop)


STRATEGIES[TrendRegimePullbackStrategy.name] = TrendRegimePullbackStrategy()


# ---------------------------------------------------------------------------
# Two older rules, brought back as comparison baselines
#
# Both were written earlier in this project and then taken off the menu. They
# are restored here unchanged because a new idea should have to beat something,
# and these two are the honest something: one is the oldest systematic trend
# rule there is, the other is barely a strategy at all.
# ---------------------------------------------------------------------------

class DonchianBreakoutStrategy:
    """Buy a new N-bar high, leave on a new M-bar low.

    The oldest systematic trend rule there is — the one the Turtles traded. It
    holds no opinion about value: a market making new highs is assumed to keep
    doing so until it makes a new low. Nothing here is symmetric with the exit,
    because a trend takes longer to build than to break.
    """

    name = "DONCHIAN_BREAKOUT"
    DEFAULTS = {"entry_lookback": 55, "exit_lookback": 20}

    def describe(self, params: Optional[dict] = None) -> dict:
        merged = {**self.DEFAULTS, **(params or {})}
        return {
            "name": self.name,
            "label": "Donchian breakout (trend following)",
            "description": ("Enter on a new high of the entry window, exit on a "
                            "new low of the shorter exit window."),
            "parameters": [
                {"key": "entry_lookback", "label": "Entry high (bars)", "type": "int",
                 "default": 55, "value": merged["entry_lookback"]},
                {"key": "exit_lookback", "label": "Exit low (bars)", "type": "int",
                 "default": 20, "value": merged["exit_lookback"]},
            ],
        }

    def prepare(self, candles: Sequence[Candle], params: dict) -> dict:
        entry = int(_param(params, "entry_lookback", 55, int))
        exit_n = int(_param(params, "exit_lookback", 20, int))
        highs = [c.high for c in candles]
        lows = [c.low for c in candles]

        # Rolling extremes of the bars BEFORE the current one: a bar cannot
        # break out above a level its own high helped set.
        top: list[Optional[float]] = [None] * len(candles)
        bottom: list[Optional[float]] = [None] * len(candles)
        for i in range(len(candles)):
            if i >= entry:
                top[i] = max(highs[i - entry:i])
            if i >= exit_n:
                bottom[i] = min(lows[i - exit_n:i])

        return {"top": top, "bottom": bottom, "entry": entry, "exit": exit_n}

    def evaluate(self, candles: Sequence[Candle], i: int, state: dict) -> Verdict:
        top, bottom = state["top"][i], state["bottom"][i]
        if top is None:
            return Verdict("FLAT", ["Not enough history for the breakout window"])

        close = candles[i].close
        readings = {"entry_high": top, "exit_low": bottom}

        if close > top:
            return Verdict("BUY", [
                f"Close {close:.2f} above the {state['entry']}-bar high {top:.2f}"
            ], readings)
        if bottom is not None and close < bottom:
            return Verdict("SELL", [
                f"Close {close:.2f} below the {state['exit']}-bar low {bottom:.2f}"
            ], readings)
        return Verdict("FLAT", [], readings)


class TrendRegimeStrategy:
    """Hold the market while it is above its long average; stand aside below it.

    Not a trading strategy so much as a participation rule. It never tries to
    time an entry — it owns the market almost all the time and exists purely to
    be absent during sustained declines. Its case against buy-and-hold is made
    in drawdown, not in return.
    """

    name = "TREND_REGIME"
    DEFAULTS = {"regime_period": 200, "buffer_pct": 0.0}

    def describe(self, params: Optional[dict] = None) -> dict:
        merged = {**self.DEFAULTS, **(params or {})}
        return {
            "name": self.name,
            "label": "Trend regime filter (long or flat)",
            "description": ("Long while price holds above its long moving average, "
                            "flat below it. Designed to sidestep bear markets."),
            "parameters": [
                {"key": "regime_period", "label": "Regime average (bars)", "type": "int",
                 "default": 200, "value": merged["regime_period"]},
                {"key": "buffer_pct", "label": "Re-entry buffer %", "type": "float",
                 "default": 0.0, "value": merged["buffer_pct"]},
            ],
        }

    def prepare(self, candles: Sequence[Candle], params: dict) -> dict:
        period = int(_param(params, "regime_period", 200, int))
        buffer_pct = _param(params, "buffer_pct", 0.0)
        return {
            "sma": ind.sma([c.close for c in candles], period),
            "period": period,
            "buffer": buffer_pct / 100.0,
        }

    def evaluate(self, candles: Sequence[Candle], i: int, state: dict) -> Verdict:
        line = state["sma"][i]
        if line is None:
            return Verdict("FLAT", ["Not enough history for the regime average"])

        close = candles[i].close
        readings = {"sma": line, "distance_pct": (close / line - 1) * 100}
        # A buffer stops the position flipping on every wobble across the line.
        up = line * (1 + state["buffer"])
        down = line * (1 - state["buffer"])

        if close > up:
            return Verdict("BUY",
                           [f"Close {close:.2f} above the {state['period']}-bar average {line:.2f}"],
                           readings)
        if close < down:
            return Verdict("SELL",
                           [f"Close {close:.2f} below the {state['period']}-bar average {line:.2f}"],
                           readings)
        return Verdict("FLAT", [], readings)


STRATEGIES[DonchianBreakoutStrategy.name] = DonchianBreakoutStrategy()
STRATEGIES[TrendRegimeStrategy.name] = TrendRegimeStrategy()


class EmaRegimeLongShortStrategy:
    """Long above one moving average, short below it. Nothing else.

    The simplest trend rule that can be written, and it exists here because
    three separate findings in this project pointed at it: shorts carry the
    bear markets, simpler timeframe stacks beat more elaborate ones, and the
    exit does more work than the entry. This keeps one condition and hands
    everything else to the run's trailing stop.

    It is the control that a more complicated idea has to beat. If a rule with
    seven conditions cannot out-perform one condition, the six extra conditions
    are decoration.
    """

    name = "EMA_REGIME_LS"

    DEFAULTS = {"period": 100, "buffer_pct": 0.0, "trade_shorts": True}

    def describe(self, params: Optional[dict] = None) -> dict:
        m = {**self.DEFAULTS, **(params or {})}
        return {
            "name": self.name,
            "label": "EMA regime, long and short",
            "description": ("Long while price is above the moving average, short "
                            "while it is below. One condition, both directions, and "
                            "the run's stop decides when a position is wrong."),
            "parameters": [
                {"key": "period", "label": "EMA period", "type": "int",
                 "default": 100, "value": m["period"]},
                {"key": "buffer_pct", "label": "Buffer either side (%)", "type": "float",
                 "default": 0.0, "value": m["buffer_pct"]},
                {"key": "trade_shorts", "label": "Take short trades", "type": "bool",
                 "default": True, "value": bool(m["trade_shorts"])},
            ],
        }

    def prepare(self, candles: Sequence[Candle], params: dict) -> dict:
        shorts = params.get("trade_shorts", True)
        if isinstance(shorts, str):
            shorts = shorts.strip().lower() not in ("0", "false", "no", "")
        return {
            "ema": ind.ema([c.close for c in candles],
                           int(_param(params, "period", 100, int))),
            "buffer": _param(params, "buffer_pct", 0.0),
            "shorts": bool(shorts),
        }

    def evaluate(self, candles: Sequence[Candle], i: int, state: dict) -> Verdict:
        line = state["ema"][i]
        if line is None:
            return Verdict("FLAT", ["Not enough history for the average"])
        close = candles[i].close
        b = state["buffer"] / 100.0
        readings = {"ema": line, "close": close}

        if close > line * (1 + b):
            return Verdict("BUY", [f"close {close:.2f} above the average {line:.2f}"],
                           readings)
        if close < line * (1 - b):
            if state["shorts"]:
                return Verdict("SELL", [f"close {close:.2f} below the average {line:.2f}"],
                               readings)
            return Verdict("EXIT", ["below the average, shorts are switched off"],
                           readings)
        return Verdict("FLAT", ["inside the buffer around the average"], readings)


STRATEGIES[EmaRegimeLongShortStrategy.name] = EmaRegimeLongShortStrategy()


# ---------------------------------------------------------------------------
# Strategy 6 — four-regime switching system
# ---------------------------------------------------------------------------

class FourRegimeStrategy:
    """Classify the market first, then apply the playbook that regime deserves.

    Four states, checked in the order that matters. Two of them trade and two
    of them refuse to, and the refusals are the point: a system that knows when
    not to act is doing something a rule with no regime test cannot.

        4  downtrend       price and fast EMA below the slow EMA  -> no longs
        3  sideways        ADX below its threshold                -> stay in cash
        2  breakout        a new N-bar high with volatility rising -> enter the break
        1  strong uptrend  aligned, rising, ADX above threshold    -> enter the pullback

    Downtrend is tested before everything because it is a veto rather than an
    opinion: "stay out of long positions" has to outrank any entry rule beneath
    it, or a breakout would fire inside a bear market. Sideways comes next for
    the same reason — refusing to trade noise is worthless if a later rule can
    still trade it.

    Which regime produced each trade is recorded in the verdict, so a backtest
    can show which playbook actually earned the money rather than only the total.
    """

    name = "FOUR_REGIME"

    DEFAULTS = {
        "trend_minutes": 60,
        "trend_fast": 50,
        "trend_slow": 200,
        "slope_lookback": 5,
        "adx_period": 14,
        "adx_above": 20.0,
        "breakout_lookback": 20,
        "atr_period": 14,
        "atr_expanding_over": 10,
        "entry_fast": 20,
        "entry_slow": 50,
        "rsi_dip_below": 45.0,
        "rsi_cross_above": 50.0,
        "dip_lookback": 10,
        "exit_on_downtrend": False,
        # Two levers the original four-regime description does not have. The
        # first turns the downtrend regime from dead time into the mirror of the
        # breakout; the second lets the pullback playbook be switched off, which
        # matters because it earns a fifth of the profit for two thirds of the
        # complexity.
        "short_downtrend": False,
        "use_pullback": True,
        "use_breakout": True,
    }

    def describe(self, params: Optional[dict] = None) -> dict:
        m = {**self.DEFAULTS, **(params or {})}
        p = lambda k, label, t="int": {"key": k, "label": label, "type": t,
                                       "default": self.DEFAULTS[k], "value": m[k]}
        return {
            "name": self.name,
            "label": "Four-regime switcher (uptrend, breakout, sideways, downtrend)",
            "description": ("Classifies the market into one of four regimes and only "
                            "trades two of them: pullbacks in a confirmed uptrend and "
                            "breakouts with expanding volatility. Sideways and "
                            "downtrend regimes stay in cash. Exits are the run's "
                            "ATR trailing stop."),
            "parameters": [
                p("trend_minutes", "Regime timeframe (minutes)"),
                p("trend_fast", "Regime fast EMA"),
                p("trend_slow", "Regime slow EMA"),
                p("slope_lookback", "Slow EMA rising over (bars)"),
                p("adx_above", "ADX above (regimes 1 and 2)", "float"),
                p("breakout_lookback", "Breakout high (bars)"),
                p("atr_expanding_over", "ATR expanding over (bars)"),
                p("entry_fast", "Entry fast EMA"),
                p("entry_slow", "Entry slow EMA"),
                p("rsi_dip_below", "RSI must have dipped below", "float"),
                p("rsi_cross_above", "RSI crosses above", "float"),
                p("dip_lookback", "Dip within (bars)"),
                {"key": "exit_on_downtrend",
                 "label": "Close longs when the downtrend regime starts",
                 "type": "bool", "default": False,
                 "value": bool(m["exit_on_downtrend"])},
                {"key": "short_downtrend",
                 "label": "Short breakdowns in the downtrend regime",
                 "type": "bool", "default": False,
                 "value": bool(m["short_downtrend"])},
                {"key": "use_pullback", "label": "Trade regime 1 pullbacks",
                 "type": "bool", "default": True, "value": bool(m["use_pullback"])},
                {"key": "use_breakout", "label": "Trade regime 2 breakouts",
                 "type": "bool", "default": True, "value": bool(m["use_breakout"])},
            ],
        }

    def prepare(self, candles: Sequence[Candle], params: dict) -> dict:
        g = lambda k, cast=int: cast(_param(params, k, self.DEFAULTS[k], cast))
        trend_minutes = g("trend_minutes")
        base = _bar_seconds(candles)
        problems = []
        if trend_minutes * 60 < base:
            problems.append(
                f"No signals will be generated: a {_fmt_tf(trend_minutes*60)} regime "
                f"filter cannot be built from {_fmt_tf(base)} bars."
            )

        higher, hidx = _higher_frame(candles, max(trend_minutes * 60, base))
        hcloses = [c.close for c in higher]
        closes = [c.close for c in candles]

        def flagged(key, default):
            v = params.get(key, default)
            if isinstance(v, str):
                return v.strip().lower() not in ("0", "false", "no", "")
            return bool(v)
        flag = flagged("exit_on_downtrend", False)

        return {
            "problems": problems, "hidx": hidx, "h_close": hcloses,
            "h_fast": ind.ema(hcloses, g("trend_fast")),
            "h_slow": ind.ema(hcloses, g("trend_slow")),
            "h_adx": ind.adx(higher, g("adx_period")),
            "e_fast": ind.ema(closes, g("entry_fast")),
            "e_slow": ind.ema(closes, g("entry_slow")),
            "e_rsi": ind.rsi(closes, 14),
            "atr": ind.atr(candles, g("atr_period")),
            "slope": g("slope_lookback"),
            "adx_above": g("adx_above", float),
            "breakout": g("breakout_lookback"),
            "atr_over": g("atr_expanding_over"),
            "dip_below": g("rsi_dip_below", float),
            "cross_above": g("rsi_cross_above", float),
            "lookback": g("dip_lookback"),
            "exit_on_downtrend": bool(flag),
            "short_downtrend": flagged("short_downtrend", False),
            "use_pullback": flagged("use_pullback", True),
            "use_breakout": flagged("use_breakout", True),
        }

    def evaluate(self, candles: Sequence[Candle], i: int, state: dict) -> Verdict:
        if state["problems"]:
            return Verdict("FLAT", state["problems"])

        h = state["hidx"][i]
        if h < 0:
            return Verdict("FLAT", ["No completed regime bar yet"])
        hf, hs = state["h_fast"][h], state["h_slow"][h]
        adx_now = state["h_adx"][h]
        ef, es, er = state["e_fast"][i], state["e_slow"][i], state["e_rsi"][i]
        if None in (hf, hs, adx_now, ef, es, er):
            return Verdict("FLAT", ["Indicators still warming up"])

        bar = candles[i]
        hclose = state["h_close"][h]
        readings = {"regime_close": hclose, "regime_fast": hf, "regime_slow": hs,
                    "adx": adx_now, "ema_fast": ef, "ema_slow": es, "rsi": er}

        # --- regime 4: downtrend. A veto, so it is tested first --------------
        if hclose < hs and hf < hs:
            readings["regime"] = 4
            if state["short_downtrend"] and adx_now >= state["adx_above"]:
                # The mirror of regime 2: a new low with volatility expanding,
                # in a market whose regime already says down. Without this the
                # downtrend regime is 14% of all bars spent doing nothing.
                n_ = state["breakout"]
                if i >= n_:
                    prior_low = min(c.low for c in candles[i - n_:i])
                    atr_now = state["atr"][i]
                    atr_then = state["atr"][max(0, i - state["atr_over"])]
                    expanding = (atr_now is not None and atr_then is not None
                                 and atr_now > atr_then)
                    if bar.close < prior_low and expanding:
                        return Verdict("SELL", [
                            f"regime 4 breakdown — close {bar.close:.2f} below the "
                            f"{n_}-bar low {prior_low:.2f}",
                            f"volatility expanding, ATR {atr_then:.2f} to {atr_now:.2f}",
                            f"ADX {adx_now:.1f}",
                        ], readings)
                return Verdict("FLAT", ["regime 4 — waiting for a breakdown"], readings)
            if state["exit_on_downtrend"]:
                return Verdict("EXIT", ["regime 4 downtrend — closing longs"], readings)
            return Verdict("FLAT", ["regime 4 downtrend — no long entries"], readings)

        # --- regime 3: sideways. Also a refusal, so it outranks the entries ---
        if adx_now < state["adx_above"]:
            readings["regime"] = 3
            return Verdict("FLAT",
                           [f"regime 3 sideways — ADX {adx_now:.1f} below "
                            f"{state['adx_above']:g}, staying in cash"], readings)

        # --- regime 2: breakout ---------------------------------------------
        n = state["breakout"]
        if state["use_breakout"] and i >= n:
            prior_high = max(c.high for c in candles[i - n:i])
            atr_now = state["atr"][i]
            atr_then = state["atr"][max(0, i - state["atr_over"])]
            expanding = (atr_now is not None and atr_then is not None
                         and atr_now > atr_then)
            if bar.close > prior_high and expanding:
                readings["regime"] = 2
                return Verdict("BUY", [
                    f"regime 2 breakout — close {bar.close:.2f} above the "
                    f"{n}-bar high {prior_high:.2f}",
                    f"volatility expanding, ATR {atr_then:.2f} to {atr_now:.2f}",
                    f"ADX {adx_now:.1f}",
                ], readings)

        # --- regime 1: strong uptrend, entered on a pullback -----------------
        prior = state["h_slow"][max(0, h - state["slope"])]
        rising = prior is not None and hs > prior
        aligned = hclose > hs and hf > hs
        if not (aligned and rising):
            readings["regime"] = 0
            return Verdict("FLAT", ["no regime — trend not aligned or not rising"],
                           readings)

        readings["regime"] = 1
        if not state["use_pullback"]:
            return Verdict("FLAT", ["regime 1 — pullback playbook switched off"], readings)
        lo, hi = min(ef, es), max(ef, es)
        if not (bar.low <= hi and bar.high >= lo):
            return Verdict("FLAT", ["regime 1 — waiting for a pullback to the EMA zone"],
                           readings)

        prev = state["e_rsi"][i - 1] if i else None
        if prev is None or not (prev <= state["cross_above"] < er):
            return Verdict("FLAT", ["regime 1 — RSI has not crossed back up"], readings)

        window = state["e_rsi"][max(0, i - state["lookback"]):i + 1]
        if not any(v is not None and v < state["dip_below"] for v in window):
            return Verdict("FLAT", ["regime 1 — no recent RSI dip"], readings)

        if bar.close <= bar.open:
            return Verdict("FLAT", ["regime 1 — entry candle did not close up"], readings)

        return Verdict("BUY", [
            "regime 1 strong uptrend — aligned, rising, "
            f"ADX {adx_now:.1f} above {state['adx_above']:g}",
            "pullback into the EMA zone",
            f"RSI dipped below {state['dip_below']:g} then crossed "
            f"{state['cross_above']:g}",
            "bullish close",
        ], readings)


STRATEGIES[FourRegimeStrategy.name] = FourRegimeStrategy()


class MeanReversionStrategy:
    """Fade the stretch instead of following it.

    Every other rule in this project follows trends, and every one of them
    fails on short timeframes. That failure is itself information: if price on a
    15-minute chart crosses its own average every few hours, it is not trending,
    it is oscillating — and the way to trade an oscillation is to take the other
    side of it.

    So this buys when price has fallen unusually far below its average and sells
    when it has risen unusually far above, measured in standard deviations so
    the threshold means the same thing in a calm week and a violent one. The
    position is closed when price returns to the average, which is the whole
    thesis: not "it will keep going" but "it has gone too far".
    """

    name = "MEAN_REVERSION"

    DEFAULTS = {
        "period": 50,
        "entry_z": 2.0,        # standard deviations from the average to act on
        "exit_z": 0.3,         # how close to the average counts as "back"
        "trade_shorts": True,
        "max_bars": 0,         # give up after this many bars, 0 = never
    }

    def describe(self, params: Optional[dict] = None) -> dict:
        m = {**self.DEFAULTS, **(params or {})}
        return {
            "name": self.name,
            "label": "Mean reversion (fade the stretch)",
            "description": ("Buys when price is unusually far below its moving "
                            "average and sells when it is unusually far above, "
                            "measured in standard deviations. Closes when price "
                            "comes back to the average."),
            "parameters": [
                {"key": "period", "label": "Average period", "type": "int",
                 "default": 50, "value": m["period"]},
                {"key": "entry_z", "label": "Enter at (std deviations)", "type": "float",
                 "default": 2.0, "value": m["entry_z"]},
                {"key": "exit_z", "label": "Exit within (std deviations)", "type": "float",
                 "default": 0.3, "value": m["exit_z"]},
                {"key": "max_bars", "label": "Give up after (bars, 0 = never)",
                 "type": "int", "default": 0, "value": m["max_bars"]},
                {"key": "trade_shorts", "label": "Fade upside stretches too",
                 "type": "bool", "default": True, "value": bool(m["trade_shorts"])},
            ],
        }

    def prepare(self, candles: Sequence[Candle], params: dict) -> dict:
        period = int(_param(params, "period", 50, int))
        closes = [c.close for c in candles]
        mid = ind.sma(closes, period)

        # Rolling standard deviation, computed forward so the value at bar i
        # uses bars 0..i only.
        sd: list[Optional[float]] = [None] * len(closes)
        for i in range(period - 1, len(closes)):
            window = closes[i - period + 1:i + 1]
            mean = mid[i]
            if mean is None:
                continue
            var = sum((x - mean) ** 2 for x in window) / period
            sd[i] = var ** 0.5

        shorts = params.get("trade_shorts", True)
        if isinstance(shorts, str):
            shorts = shorts.strip().lower() not in ("0", "false", "no", "")

        return {"mid": mid, "sd": sd,
                "entry_z": _param(params, "entry_z", 2.0),
                "exit_z": _param(params, "exit_z", 0.3),
                "shorts": bool(shorts)}

    def evaluate(self, candles: Sequence[Candle], i: int, state: dict) -> Verdict:
        mid, sd = state["mid"][i], state["sd"][i]
        if mid is None or sd is None or sd <= 0:
            return Verdict("FLAT", ["Not enough history for the average"])

        close = candles[i].close
        z = (close - mid) / sd
        readings = {"mid": mid, "sd": sd, "z": z}

        # Back at the average: the reason for the position has been met.
        if abs(z) <= state["exit_z"]:
            return Verdict("EXIT", [f"price back at the average, z {z:+.2f}"], readings)

        if z <= -state["entry_z"]:
            return Verdict("BUY", [
                f"price {abs(z):.2f} deviations below the average — stretched down",
            ], readings)

        if z >= state["entry_z"] and state["shorts"]:
            return Verdict("SELL", [
                f"price {z:.2f} deviations above the average — stretched up",
            ], readings)

        return Verdict("FLAT", [f"z {z:+.2f}, not stretched enough"], readings)


STRATEGIES[MeanReversionStrategy.name] = MeanReversionStrategy()


class GoldMasterTrendStrategy:
    """1H master trend filter, 15M pullback entry, staged exits.

    The most fully specified rule in this project: it says what makes a trend,
    what makes an entry, where the first stop goes, how the stop is promoted as
    profit arrives, and two separate ways to get out. Almost everything here is
    the specification's, not a choice of mine.

    Two things the specification leaves to interpretation, both recorded rather
    than hidden:

    * **"RSI falls below 45 during the pullback"** does not say how recently, so
      a window is used and is a parameter.
    * **"A completely new setup is required"** after an exit cannot be enforced
      by a rule that cannot see positions. It does not need to be: entry demands
      an RSI *crossing* of the midline on this exact bar, so two entries can
      never share one crossing, and a fresh dip-and-recover is required anyway.

    The stop, the breakeven promotion and the trailing activation are the run's
    to apply — this supplies the initial stop level, which only the setup knows.
    """

    name = "GOLD_MASTER_TREND"

    DEFAULTS = {
        "trend_minutes": 60,
        "trend_fast": 8, "trend_mid": 14, "trend_slow": 200,
        "trend_rsi_above": 50.0,
        "entry_fast": 8, "entry_mid": 14,
        "rsi_dip_below": 45.0,
        "rsi_cross_above": 50.0,
        "dip_lookback": 10,
        "atr_period": 14,
        "stop_atr": 1.5,
        "exit_rsi_below": 45.0,
        "trend_exit_rsi": 45.0,
    }

    def describe(self, params: Optional[dict] = None) -> dict:
        m = {**self.DEFAULTS, **(params or {})}
        p = lambda k, label, t="int": {"key": k, "label": label, "type": t,
                                       "default": self.DEFAULTS[k], "value": m[k]}
        return {
            "name": self.name,
            "label": "Gold master trend (1H filter, 15M pullback, staged exits)",
            "description": ("Long only. The 1-hour chart must be in a stacked "
                            "uptrend with momentum; the 15-minute chart must pull "
                            "back into the EMA 8/14 zone, dip and recover. The "
                            "initial stop is 1.5 ATR below entry. Exits on a 15M "
                            "trend break or a 1H regime break."),
            "parameters": [
                p("trend_minutes", "Trend timeframe (minutes)"),
                p("trend_fast", "Trend fast EMA"), p("trend_mid", "Trend mid EMA"),
                p("trend_slow", "Trend slow EMA"),
                p("trend_rsi_above", "Trend RSI above", "float"),
                p("entry_fast", "Entry fast EMA"), p("entry_mid", "Entry mid EMA"),
                p("rsi_dip_below", "RSI must dip below", "float"),
                p("rsi_cross_above", "RSI crosses above", "float"),
                p("dip_lookback", "Dip within (bars)"),
                p("stop_atr", "Initial stop (× ATR)", "float"),
                p("exit_rsi_below", "15M exit: RSI below", "float"),
                p("trend_exit_rsi", "1H exit: RSI below", "float"),
            ],
        }

    def prepare(self, candles: Sequence[Candle], params: dict) -> dict:
        g = lambda k, cast=int: cast(_param(params, k, self.DEFAULTS[k], cast))
        trend_minutes = g("trend_minutes")
        base = _bar_seconds(candles)
        problems = []
        if trend_minutes * 60 < base:
            problems.append(
                f"No signals will be generated: a {_fmt_tf(trend_minutes*60)} trend "
                f"filter cannot be built from {_fmt_tf(base)} bars."
            )
        higher, hidx = _higher_frame(candles, max(trend_minutes * 60, base))
        hc = [c.close for c in higher]
        closes = [c.close for c in candles]
        return {
            "problems": problems, "hidx": hidx, "h_close": hc,
            "h_fast": ind.ema(hc, g("trend_fast")),
            "h_mid": ind.ema(hc, g("trend_mid")),
            "h_slow": ind.ema(hc, g("trend_slow")),
            "h_rsi": ind.rsi(hc, 14),
            "e_fast": ind.ema(closes, g("entry_fast")),
            "e_mid": ind.ema(closes, g("entry_mid")),
            "e_rsi": ind.rsi(closes, 14),
            "atr": ind.atr(candles, g("atr_period")),
            "trend_rsi_above": g("trend_rsi_above", float),
            "dip_below": g("rsi_dip_below", float),
            "cross_above": g("rsi_cross_above", float),
            "lookback": g("dip_lookback"),
            "stop_atr": g("stop_atr", float),
            "exit_rsi": g("exit_rsi_below", float),
            "trend_exit_rsi": g("trend_exit_rsi", float),
        }

    def evaluate(self, candles: Sequence[Candle], i: int, state: dict) -> Verdict:
        if state["problems"]:
            return Verdict("FLAT", state["problems"])
        h = state["hidx"][i]
        if h < 0:
            return Verdict("FLAT", ["No completed 1H bar yet"])
        hf, hm, hs, hr = (state["h_fast"][h], state["h_mid"][h],
                          state["h_slow"][h], state["h_rsi"][h])
        ef, em, er = state["e_fast"][i], state["e_mid"][i], state["e_rsi"][i]
        atr_now = state["atr"][i]
        if None in (hf, hm, hs, hr, ef, em, er, atr_now):
            return Verdict("FLAT", ["Indicators still warming up"])

        bar, hclose = candles[i], state["h_close"][h]
        readings = {"h_fast": hf, "h_mid": hm, "h_slow": hs, "h_rsi": hr,
                    "ema_fast": ef, "ema_mid": em, "rsi": er, "atr": atr_now}

        # --- exits are tested before entries: a rule that can close should be
        # --- asked to close before it is asked to open.
        if hclose < hs:
            return Verdict("EXIT", [f"1H close {hclose:.2f} below EMA200 {hs:.2f}"],
                           readings)
        if hf < hm and hr < state["trend_exit_rsi"]:
            return Verdict("EXIT", [
                "1H EMA8 below EMA14",
                f"1H RSI {hr:.1f} below {state['trend_exit_rsi']:g}",
            ], readings)
        if bar.close < em and er < state["exit_rsi"]:
            return Verdict("EXIT", [
                f"15M close {bar.close:.2f} below EMA14 {em:.2f}",
                f"RSI {er:.1f} below {state['exit_rsi']:g}",
            ], readings)

        # --- 1H master trend filter -----------------------------------------
        master = [
            (hf > hm, "1H EMA8 above EMA14"),
            (hm > hs, "1H EMA14 above EMA200"),
            (hclose > hs, "1H close above EMA200"),
            (hr > state["trend_rsi_above"],
             f"1H RSI {hr:.1f} above {state['trend_rsi_above']:g}"),
        ]
        failed = [why for ok, why in master if not ok]
        if failed:
            return Verdict("FLAT", ["1H filter: " + failed[0]], readings)

        # --- 15M entry --------------------------------------------------------
        if ef <= em:
            return Verdict("FLAT", ["15M EMA8 not above EMA14"], readings)

        lo, hi = min(ef, em), max(ef, em)
        if not (bar.low <= hi and bar.high >= lo):
            return Verdict("FLAT", ["no pullback into the EMA 8/14 zone"], readings)

        prev = state["e_rsi"][i - 1] if i else None
        if prev is None or not (prev <= state["cross_above"] < er):
            return Verdict("FLAT", ["RSI did not cross the midline on this bar"],
                           readings)

        window = state["e_rsi"][max(0, i - state["lookback"]):i + 1]
        if not any(v is not None and v < state["dip_below"] for v in window):
            return Verdict("FLAT",
                           [f"RSI never dipped below {state['dip_below']:g}"], readings)

        if bar.close <= bar.open:
            return Verdict("FLAT", ["confirmation candle not bullish"], readings)
        if bar.close <= ef:
            return Verdict("FLAT", ["confirmation candle did not close above EMA8"],
                           readings)

        stop = bar.close - state["stop_atr"] * atr_now
        readings["initial_stop"] = stop
        return Verdict("BUY", [
            "1H master trend bullish: EMA8 > EMA14 > EMA200, close above EMA200, RSI above 50",
            "15M pullback into the EMA 8/14 zone",
            f"RSI dipped below {state['dip_below']:g} then crossed "
            f"{state['cross_above']:g}",
            "bullish confirmation candle, closed above EMA8",
            f"initial stop {state['stop_atr']:g} ATR below at {stop:.2f}",
        ], readings, stop_price=stop)


STRATEGIES[GoldMasterTrendStrategy.name] = GoldMasterTrendStrategy()


class DailySwingSma200Strategy:
    """Daily 200-SMA participation with an entry gate and a patient exit.

    Get long only when the daily close is clearly above the 200-day average —
    not when it is merely touching it, and not when it has already run away —
    then hold through everything until a daily close goes back below it.

    Each entry condition is a separate switch so its contribution can be
    measured rather than assumed:

      A  daily close above the 200-day SMA
      B  and at least `buffer_pct` above it — no entry inside the band
      C  and the candle itself closes up, if `require_bullish_candle`
      D  and not more than `max_extension_pct` above the SMA — do not chase
      E  and not more than `chase_pct` above the previous exit price, if
         `use_last_sell_filter` is on

    E is off by default, and that default is a measurement rather than a
    preference. Over 26 years of daily bars it reduced the rule to between one
    and four trades on every market tested, because once price rises past a
    sale and never returns, the condition locks the strategy out permanently.
    On /NQ, turning it off moved the result from $3,040 to $509,860. It is kept
    as a switch so the difference can be seen rather than argued about.

    D and E are entry gates only. Once a position is open the holding rule
    governs: stay long while the close is above the average, whatever else
    happens. Re-testing an entry gate on every bar would turn it into an exit
    and cut winners short.

    The signal series is resolved once in `prepare`, walking bars forward in
    order, so no decision can be influenced by a bar it had not yet reached.
    """

    name = "DAILY_SWING_SMA200"

    DEFAULTS = {
        "sma_period": 200,
        "buffer_pct": 1.0,
        "max_extension_pct": 5.0,
        "require_bullish_candle": True,
        "use_last_sell_filter": False,
        "chase_pct": 2.0,
        "exit_confirm_days": 0,
        "trade_shorts": False,
    }

    def describe(self, params: Optional[dict] = None) -> dict:
        m = {**self.DEFAULTS, **(params or {})}
        return {
            "name": self.name,
            "label": "Daily 200-SMA swing (buffered, no-chase)",
            "description": ("Long only. Entered on a confirmed daily close above the "
                            "200-day average, held until a daily close below it."),
            "parameters": [
                {"key": "sma_period", "label": "SMA period (days)", "type": "int",
                 "default": 200, "value": m["sma_period"]},
                {"key": "buffer_pct", "label": "Minimum % above SMA to buy", "type": "float",
                 "default": 1.0, "value": m["buffer_pct"]},
                {"key": "max_extension_pct", "label": "Do not chase above % of SMA",
                 "type": "float", "default": 5.0, "value": m["max_extension_pct"]},
                {"key": "require_bullish_candle", "label": "Require an up candle",
                 "type": "bool", "default": True, "value": bool(m["require_bullish_candle"])},
                {"key": "use_last_sell_filter",
                 "label": "Only buy near/below the last sell price", "type": "bool",
                 "default": False, "value": bool(m["use_last_sell_filter"])},
                {"key": "chase_pct", "label": "...allowed % above that last sell",
                 "type": "float", "default": 2.0, "value": m["chase_pct"]},
                {"key": "exit_confirm_days", "label": "Extra days below SMA before exiting",
                 "type": "int", "default": 0, "value": m["exit_confirm_days"]},
                {"key": "trade_shorts", "label": "Also short below the SMA (mirror of the "
                 "buy gate)", "type": "bool", "default": False,
                 "value": bool(m["trade_shorts"])},
            ],
        }

    def prepare(self, candles: Sequence[Candle], params: dict) -> dict:
        closes = [c.close for c in candles]
        period = int(_param(params, "sma_period", 200, int))
        buf = _param(params, "buffer_pct", 1.0) / 100.0
        ext = _param(params, "max_extension_pct", 5.0) / 100.0
        chase = _param(params, "chase_pct", 2.0) / 100.0
        confirm = int(_param(params, "exit_confirm_days", 0, int))

        def flag(key, default):
            v = params.get(key, default)
            if isinstance(v, str):
                return v.strip().lower() not in ("0", "false", "no", "")
            return bool(v)

        want_bull = flag("require_bullish_candle", True)
        use_last_sell = flag("use_last_sell_filter", False)
        shorts = flag("trade_shorts", False)
        sma = ind.sma(closes, period)

        signals: list[tuple[str, Any]] = []
        held: Optional[str] = None          # None | "LONG" | "SHORT"
        last_sell: Optional[float] = None
        below_run = 0                       # consecutive daily closes below the SMA
        above_run = 0                       # and the mirror, for the short side
        blocked = {"buffer": 0, "extension": 0, "candle": 0, "chase": 0}

        for c in candles:
            line = sma[len(signals)]
            if line is None:
                signals.append(("FLAT", None))
                continue
            close = c.close
            below_run = below_run + 1 if close < line else 0
            above_run = above_run + 1 if close > line else 0

            if held == "LONG":
                # Hold through everything except a close below the average.
                if below_run > confirm:
                    held = None
                    last_sell = close
                    signals.append(("EXIT", f"Daily close {close:.2f} below the "
                                            f"{period}-day average {line:.2f}"))
                else:
                    signals.append(("FLAT", None))
                continue
            if held == "SHORT":
                # The mirror: a short is covered on a close back above the average.
                if above_run > confirm:
                    held = None
                    signals.append(("EXIT", f"Daily close {close:.2f} back above the "
                                            f"{period}-day average {line:.2f}"))
                else:
                    signals.append(("FLAT", None))
                continue

            if close < line * (1 - buf):
                if not shorts:
                    signals.append(("FLAT", None)); continue
                if close < line * (1 - ext):
                    blocked["extension"] += 1
                    signals.append(("FLAT", None)); continue
                if want_bull and close >= c.open:
                    blocked["candle"] += 1
                    signals.append(("FLAT", None)); continue
                held = "SHORT"
                signals.append(("SELL", [
                    f"Close {close:.2f} is {(1 - close / line) * 100:.1f}% below the "
                    f"{period}-day average {line:.2f}",
                    f"Inside the {buf:.0%}-{ext:.0%} entry window",
                    "Bearish candle" if want_bull else "Candle direction not required",
                ]))
                continue
            if close <= line * (1 + buf):
                if close > line:
                    blocked["buffer"] += 1
                signals.append(("FLAT", None)); continue
            if close > line * (1 + ext):
                blocked["extension"] += 1
                signals.append(("FLAT", None)); continue
            if want_bull and close <= c.open:
                blocked["candle"] += 1
                signals.append(("FLAT", None)); continue
            if use_last_sell and last_sell is not None and close > last_sell * (1 + chase):
                blocked["chase"] += 1
                signals.append(("FLAT", None)); continue

            held = "LONG"
            signals.append(("BUY", [
                f"Close {close:.2f} is {(close / line - 1) * 100:.1f}% above the "
                f"{period}-day average {line:.2f}",
                f"Inside the {buf:.0%}-{ext:.0%} entry window",
                "Bullish candle" if want_bull else "Candle direction not required",
            ]))

        problems = []
        if not any(s[0] == "BUY" for s in signals):
            problems.append("The entry gate never opened — no bar satisfied every "
                            "entry condition. Widen the entry window or switch off "
                            "the last-sell filter.")
        elif use_last_sell and blocked["chase"] > 0:
            problems.append(f"The last-sell filter refused {blocked['chase']} entries. "
                            "On a market that rises past a sale and does not return, "
                            "this condition locks the strategy out permanently.")
        return {"sma": sma, "signals": signals, "blocked": blocked, "period": period,
                "buffer": buf, "ext": ext, "problems": problems}

    def evaluate(self, candles: Sequence[Candle], i: int, state: dict) -> Verdict:
        sig, why = state["signals"][i]
        line = state["sma"][i]
        readings = {} if line is None else {
            "sma": line,
            "buyFloor": line * (1 + state["buffer"]),
            "chaseCeiling": line * (1 + state["ext"]),
            "distance_pct": (candles[i].close / line - 1) * 100,
        }
        reasons = why if isinstance(why, list) else ([why] if why else [])
        return Verdict(sig, reasons, readings)


STRATEGIES[DailySwingSma200Strategy.name] = DailySwingSma200Strategy()


class VolatilityBreakoutTrendStrategy:
    """Break of a 20-bar range, taken only while volatility is expanding and
    the long trend agrees.

    Three conditions, all required, in both directions:

      BUY   close above the previous `lookback`-bar high
            ATR above its own `atr_average` -bar average
            close above the EMA `trend_period`
      SELL  the mirror of each

    The volatility test is what separates this from a plain channel breakout:
    a break that happens while ATR is below its own average is a break into a
    quiet market, which is where breakouts fail most often.

    The exit is not defined here. Stop and target are ATR multiples supplied by
    the run, so the rule states only when to be in and on which side.

    Note on the ATR length: a specification that says "current ATR" without a
    period is read here as the conventional ATR(14), averaged over 20 bars.
    Both are parameters.
    """

    name = "VOL_BREAKOUT_TREND"

    DEFAULTS = {
        "lookback": 20,
        "atr_period": 14,
        "atr_average": 20,
        "trend_period": 200,
        "trade_shorts": True,
        # Off by default, so the rule's original exit-free form is unchanged.
        # When on, a close that crosses the trend EMA closes the position.
        "exit_on_trend_cross": False,
        # Three optional tighteners, all neutral at their defaults so the
        # original rule is exactly reproduced when none is set.
        "atr_multiple": 1.0,      # ATR must clear its average by this factor
        "adx_min": 0.0,           # 0 disables the trend-strength gate
        "atr_rising_bars": 0,     # 0 disables the expansion test
    }

    def describe(self, params: Optional[dict] = None) -> dict:
        m = {**self.DEFAULTS, **(params or {})}
        return {
            "name": self.name,
            "label": "Volatility breakout + trend filter",
            "description": ("Break of the 20-bar range, taken only when ATR is above "
                            "its 20-bar average and price is on the right side of "
                            "the EMA 200."),
            "parameters": [
                {"key": "lookback", "label": "Breakout channel (bars)", "type": "int",
                 "default": 20, "value": m["lookback"]},
                {"key": "atr_period", "label": "ATR period", "type": "int",
                 "default": 14, "value": m["atr_period"]},
                {"key": "atr_average", "label": "ATR average (bars)", "type": "int",
                 "default": 20, "value": m["atr_average"]},
                {"key": "trend_period", "label": "Trend EMA", "type": "int",
                 "default": 200, "value": m["trend_period"]},
                {"key": "trade_shorts", "label": "Trade shorts", "type": "bool",
                 "default": True, "value": bool(m["trade_shorts"])},
                {"key": "exit_on_trend_cross",
                 "label": "Exit when price closes back across the trend EMA",
                 "type": "bool", "default": False,
                 "value": bool(m["exit_on_trend_cross"])},
                {"key": "atr_multiple", "label": "ATR must exceed its average by",
                 "type": "float", "default": 1.0, "value": m["atr_multiple"]},
                {"key": "adx_min", "label": "Minimum ADX (0 = off)", "type": "float",
                 "default": 0.0, "value": m["adx_min"]},
                {"key": "atr_rising_bars",
                 "label": "ATR must be rising over N bars (0 = off)", "type": "int",
                 "default": 0, "value": m["atr_rising_bars"]},
            ],
        }

    def prepare(self, candles: Sequence[Candle], params: dict) -> dict:
        closes = [c.close for c in candles]
        atr_p = int(_param(params, "atr_period", 14, int))
        atr = ind.atr(candles, atr_p)
        avg_n = int(_param(params, "atr_average", 20, int))
        # Average of the ATR series itself, index-aligned and trailing only.
        clean = [a for a in atr if a is not None]
        avg_clean = ind.sma(clean, avg_n)
        atr_avg: list = []
        k = 0
        for a in atr:
            if a is None:
                atr_avg.append(None)
            else:
                atr_avg.append(avg_clean[k]); k += 1

        def flag(key, default):
            v = params.get(key, default)
            if isinstance(v, str):
                return v.strip().lower() not in ("0", "false", "no", "")
            return bool(v)

        shorts = flag("trade_shorts", True)
        adx_min = _param(params, "adx_min", 0.0)
        return {
            "atr": atr, "atr_avg": atr_avg,
            "cross_exit": flag("exit_on_trend_cross", False),
            "atr_mult": _param(params, "atr_multiple", 1.0),
            "adx_min": adx_min,
            "adx": ind.adx(candles, atr_p) if adx_min > 0 else None,
            "rising": int(_param(params, "atr_rising_bars", 0, int)),
            "closes": closes,
            "ema": ind.ema(closes, int(_param(params, "trend_period", 200, int))),
            "highs": [c.high for c in candles], "lows": [c.low for c in candles],
            "lookback": int(_param(params, "lookback", 20, int)),
            "shorts": bool(shorts),
        }

    def evaluate(self, candles: Sequence[Candle], i: int, state: dict) -> Verdict:
        n = state["lookback"]
        atr_now, atr_avg, ema = state["atr"][i], state["atr_avg"][i], state["ema"][i]
        if i < n or None in (atr_now, atr_avg, ema):
            return Verdict("FLAT", ["Insufficient history for indicators"])

        close = candles[i].close
        prior_high = max(state["highs"][i - n:i])
        prior_low = min(state["lows"][i - n:i])
        expanding = atr_now > atr_avg * state["atr_mult"]
        readings = {"atr": atr_now, "atrAverage": atr_avg, "ema": ema,
                    "priorHigh": prior_high, "priorLow": prior_low,
                    "volatilityExpanding": expanding}

        # Optional tighteners. Each is skipped entirely at its neutral default,
        # so a run that sets none of them behaves exactly as before.
        if state["rising"]:
            k = i - state["rising"]
            then = state["atr"][k] if k >= 0 else None
            if then is None or atr_now <= then:
                return Verdict("FLAT", ["ATR not rising over the window"], readings)
        if state["adx_min"] > 0:
            adx_now = state["adx"][i]
            readings["adx"] = adx_now
            if adx_now is None or adx_now < state["adx_min"]:
                return Verdict("FLAT", [f"ADX below {state['adx_min']:g}"], readings)

        if close > prior_high and expanding and close > ema:
            return Verdict("BUY", [
                f"Close {close:.2f} above the {n}-bar high {prior_high:.2f}",
                f"ATR {atr_now:.2f} above its {n}-bar average {atr_avg:.2f}",
                f"Close above the trend EMA {ema:.2f}",
            ], readings)

        if state["shorts"] and close < prior_low and expanding and close < ema:
            return Verdict("SELL", [
                f"Close {close:.2f} below the {n}-bar low {prior_low:.2f}",
                f"ATR {atr_now:.2f} above its {n}-bar average {atr_avg:.2f}",
                f"Close below the trend EMA {ema:.2f}",
            ], readings)

        # A long can only have been opened above the EMA and a short only below
        # it, so a close that crosses the line is unambiguously against whatever
        # is open. Tested after the entries so a bar that both crosses and
        # breaks out is treated as the entry it is.
        if state["cross_exit"]:
            prev_close = state["closes"][i - 1]
            prev_ema = state["ema"][i - 1]
            if prev_ema is not None:
                crossed = ((prev_close >= prev_ema and close < ema)
                           or (prev_close <= prev_ema and close > ema))
                if crossed:
                    return Verdict("EXIT", [
                        f"Close {close:.2f} crossed back over the trend EMA {ema:.2f}"
                    ], readings)

        return Verdict("FLAT", [], readings)


STRATEGIES[VolatilityBreakoutTrendStrategy.name] = VolatilityBreakoutTrendStrategy()


class TrendPullbackStackStrategy:
    """A stacked trend filter entered on a pullback to the fast average.

    Seven conditions, all required, mirrored for shorts:

      1  EMA 50 above EMA 200
      2  close above EMA 50
      3  RSI 14 above 55
      4  MACD line above its signal line
      5  ATR 14 above its own recent average
      6  price has pulled back to the EMA 20 within the last `dip_lookback` bars
      7  the entry bar closes up

    The exit is not defined here — stop, target, breakeven and trailing come
    from the run, so the same rule can be measured under different exit
    structures without changing what it considers a setup.

    Two readings the specification leaves open are fixed here and stated rather
    than hidden: "pullback toward EMA 20" is taken as the bar's low reaching the
    EMA 20 (its high, for shorts) at some point in the last ten bars, and "its
    recent ATR average" is the 20-bar average of ATR 14, matching the other
    volatility rule in this module.
    """

    name = "TREND_PULLBACK_STACK"

    DEFAULTS = {
        "fast_period": 20,
        "mid_period": 50,
        "slow_period": 200,
        "rsi_period": 14,
        "rsi_long_above": 55.0,
        "rsi_short_below": 45.0,
        "macd_fast": 12,
        "macd_slow": 26,
        "macd_signal": 9,
        "atr_period": 14,
        "atr_average": 20,
        "dip_lookback": 10,
        "trade_shorts": True,
    }

    def describe(self, params: Optional[dict] = None) -> dict:
        m = {**self.DEFAULTS, **(params or {})}
        keys = [("fast_period", "Pullback EMA"), ("mid_period", "Mid EMA"),
                ("slow_period", "Trend EMA"), ("rsi_period", "RSI period"),
                ("rsi_long_above", "RSI long above"), ("rsi_short_below", "RSI short below"),
                ("macd_fast", "MACD fast"), ("macd_slow", "MACD slow"),
                ("macd_signal", "MACD signal"), ("atr_period", "ATR period"),
                ("atr_average", "ATR average (bars)"), ("dip_lookback", "Pullback lookback")]
        return {
            "name": self.name,
            "label": "EMA + RSI + MACD + ATR trend pullback",
            "description": ("Stacked trend, momentum and volatility filters, entered on "
                            "a pullback to the fast EMA and a candle in the trend's "
                            "direction."),
            "parameters": [{"key": k, "label": l, "type": "float",
                            "default": self.DEFAULTS[k], "value": m[k]} for k, l in keys]
                          + [{"key": "trade_shorts", "label": "Trade shorts", "type": "bool",
                              "default": True, "value": bool(m["trade_shorts"])}],
        }

    def prepare(self, candles: Sequence[Candle], params: dict) -> dict:
        closes = [c.close for c in candles]
        P = lambda k, cast=float: cast(_param(params, k, self.DEFAULTS[k], cast))

        mf, ms, sg = P("macd_fast", int), P("macd_slow", int), P("macd_signal", int)
        f_ema, s_ema = ind.ema(closes, mf), ind.ema(closes, ms)
        macd = [(a - b) if (a is not None and b is not None) else None
                for a, b in zip(f_ema, s_ema)]
        clean = [x for x in macd if x is not None]
        sig_clean = ind.ema(clean, sg)
        signal, k = [], 0
        for x in macd:
            if x is None:
                signal.append(None)
            else:
                signal.append(sig_clean[k]); k += 1

        atr = ind.atr(candles, P("atr_period", int))
        a_clean = [a for a in atr if a is not None]
        avg_clean = ind.sma(a_clean, P("atr_average", int))
        atr_avg, k = [], 0
        for a in atr:
            if a is None:
                atr_avg.append(None)
            else:
                atr_avg.append(avg_clean[k]); k += 1

        shorts = params.get("trade_shorts", True)
        if isinstance(shorts, str):
            shorts = shorts.strip().lower() not in ("0", "false", "no", "")
        return {
            "ema_fast": ind.ema(closes, P("fast_period", int)),
            "ema_mid": ind.ema(closes, P("mid_period", int)),
            "ema_slow": ind.ema(closes, P("slow_period", int)),
            "rsi": ind.rsi(closes, P("rsi_period", int)),
            "macd": macd, "signal": signal, "atr": atr, "atr_avg": atr_avg,
            "rsi_long": P("rsi_long_above"), "rsi_short": P("rsi_short_below"),
            "dip": P("dip_lookback", int), "shorts": bool(shorts),
        }

    def evaluate(self, candles: Sequence[Candle], i: int, state: dict) -> Verdict:
        fast, mid, slow = state["ema_fast"][i], state["ema_mid"][i], state["ema_slow"][i]
        rsi_now, macd_now, sig_now = state["rsi"][i], state["macd"][i], state["signal"][i]
        atr_now, atr_avg = state["atr"][i], state["atr_avg"][i]
        if None in (fast, mid, slow, rsi_now, macd_now, sig_now, atr_now, atr_avg):
            return Verdict("FLAT", ["Insufficient history for indicators"])

        bar = candles[i]
        close = bar.close
        readings = {"ema20": fast, "ema50": mid, "ema200": slow, "rsi": rsi_now,
                    "macd": macd_now, "macdSignal": sig_now, "atr": atr_now,
                    "atrAverage": atr_avg}

        expanding = atr_now > atr_avg
        lo = max(0, i - state["dip"] + 1)
        # Rule 6: price has reached the fast EMA at some point in the window.
        touched_up = any(candles[j].low <= (state["ema_fast"][j] or float("inf"))
                         for j in range(lo, i + 1))
        touched_dn = any(candles[j].high >= (state["ema_fast"][j] or float("-inf"))
                         for j in range(lo, i + 1))

        long_ok = [
            (mid > slow, f"EMA50 {mid:.2f} above EMA200 {slow:.2f}"),
            (close > mid, f"Close {close:.2f} above EMA50"),
            (rsi_now > state["rsi_long"], f"RSI {rsi_now:.1f} above {state['rsi_long']:g}"),
            (macd_now > sig_now, "MACD above its signal line"),
            (expanding, f"ATR {atr_now:.2f} above its average {atr_avg:.2f}"),
            (touched_up, f"Pulled back to the EMA20 within {state['dip']} bars"),
            (close > bar.open, "Bullish candle"),
        ]
        if all(ok for ok, _ in long_ok):
            return Verdict("BUY", [why for _, why in long_ok], readings)

        if state["shorts"]:
            short_ok = [
                (mid < slow, f"EMA50 {mid:.2f} below EMA200 {slow:.2f}"),
                (close < mid, f"Close {close:.2f} below EMA50"),
                (rsi_now < state["rsi_short"], f"RSI {rsi_now:.1f} below {state['rsi_short']:g}"),
                (macd_now < sig_now, "MACD below its signal line"),
                (expanding, f"ATR {atr_now:.2f} above its average {atr_avg:.2f}"),
                (touched_dn, f"Pulled back to the EMA20 within {state['dip']} bars"),
                (close < bar.open, "Bearish candle"),
            ]
            if all(ok for ok, _ in short_ok):
                return Verdict("SELL", [why for _, why in short_ok], readings)

        return Verdict("FLAT", [], readings)


STRATEGIES[TrendPullbackStackStrategy.name] = TrendPullbackStackStrategy()


class SimplePullbackStrategy:
    """Trend, pullback, momentum reset. Nothing else.

    Six conditions, mirrored for shorts, with no volatility or oscillator
    filter beyond RSI itself:

      1  close above EMA 200
      2  EMA 50 above EMA 200
      3  price has pulled back to the EMA 20 within the last `dip_lookback` bars
      4  RSI dipped below `rsi_dip` inside that same window
      5  RSI crosses back above `rsi_cross` on this bar
      6  the bar closes up

    Condition 5 is a crossing, not a level: RSI must have been at or below the
    threshold on the previous bar and above it on this one. A level test would
    fire on every bar of a strong move; a crossing fires once, which is what
    "crosses back above" means.

    The exit is the run's stop and target. This rule says only when to enter.
    """

    name = "SIMPLE_PULLBACK"

    DEFAULTS = {
        "fast_period": 20,
        "mid_period": 50,
        "slow_period": 200,
        "rsi_period": 14,
        "rsi_dip": 45.0,
        "rsi_cross": 50.0,
        "rsi_spike": 55.0,
        "dip_lookback": 10,
        "trade_shorts": True,
    }

    def describe(self, params: Optional[dict] = None) -> dict:
        m = {**self.DEFAULTS, **(params or {})}
        keys = [("fast_period", "Pullback EMA"), ("mid_period", "Mid EMA"),
                ("slow_period", "Trend EMA"), ("rsi_period", "RSI period"),
                ("rsi_dip", "RSI dip below (long)"), ("rsi_cross", "RSI crosses"),
                ("rsi_spike", "RSI spike above (short)"),
                ("dip_lookback", "Pullback lookback")]
        return {
            "name": self.name,
            "label": "Simple trend pullback (EMA + RSI)",
            "description": ("Trend from the EMA stack, timing from a pullback to the "
                            "fast EMA and an RSI reset. No other filters."),
            "parameters": [{"key": k, "label": l, "type": "float",
                            "default": self.DEFAULTS[k], "value": m[k]} for k, l in keys]
                          + [{"key": "trade_shorts", "label": "Trade shorts", "type": "bool",
                              "default": True, "value": bool(m["trade_shorts"])}],
        }

    def prepare(self, candles: Sequence[Candle], params: dict) -> dict:
        closes = [c.close for c in candles]
        P = lambda k, cast=float: cast(_param(params, k, self.DEFAULTS[k], cast))
        shorts = params.get("trade_shorts", True)
        if isinstance(shorts, str):
            shorts = shorts.strip().lower() not in ("0", "false", "no", "")
        return {
            "ema_fast": ind.ema(closes, P("fast_period", int)),
            "ema_mid": ind.ema(closes, P("mid_period", int)),
            "ema_slow": ind.ema(closes, P("slow_period", int)),
            "rsi": ind.rsi(closes, P("rsi_period", int)),
            "atr": ind.atr(candles, 14),
            "dip": P("dip_lookback", int),
            "rsi_dip": P("rsi_dip"), "rsi_cross": P("rsi_cross"),
            "rsi_spike": P("rsi_spike"), "shorts": bool(shorts),
        }

    def evaluate(self, candles: Sequence[Candle], i: int, state: dict) -> Verdict:
        fast, mid, slow = state["ema_fast"][i], state["ema_mid"][i], state["ema_slow"][i]
        rsi_now = state["rsi"][i]
        rsi_prev = state["rsi"][i - 1] if i else None
        if None in (fast, mid, slow, rsi_now, rsi_prev):
            return Verdict("FLAT", ["Insufficient history for indicators"])

        bar = candles[i]
        close = bar.close
        lo = max(0, i - state["dip"] + 1)
        window = range(lo, i + 1)
        readings = {"ema20": fast, "ema50": mid, "ema200": slow, "rsi": rsi_now,
                    "atr": state["atr"][i]}
        cross = state["rsi_cross"]

        touched_up = any(candles[j].low <= (state["ema_fast"][j] or float("inf"))
                         for j in window)
        dipped = any((state["rsi"][j] or 100.0) < state["rsi_dip"] for j in window)
        long_ok = [
            (close > slow, f"Close {close:.2f} above EMA200 {slow:.2f}"),
            (mid > slow, f"EMA50 {mid:.2f} above EMA200"),
            (touched_up, f"Pulled back to the EMA20 within {state['dip']} bars"),
            (dipped, f"RSI dipped below {state['rsi_dip']:g} in that window"),
            (rsi_prev <= cross < rsi_now, f"RSI crossed back above {cross:g}"),
            (close > bar.open, "Bullish candle"),
        ]
        if all(ok for ok, _ in long_ok):
            return Verdict("BUY", [why for _, why in long_ok], readings)

        if state["shorts"]:
            touched_dn = any(candles[j].high >= (state["ema_fast"][j] or float("-inf"))
                             for j in window)
            spiked = any((state["rsi"][j] or 0.0) > state["rsi_spike"] for j in window)
            short_ok = [
                (close < slow, f"Close {close:.2f} below EMA200 {slow:.2f}"),
                (mid < slow, f"EMA50 {mid:.2f} below EMA200"),
                (touched_dn, f"Pulled back to the EMA20 within {state['dip']} bars"),
                (spiked, f"RSI rose above {state['rsi_spike']:g} in that window"),
                (rsi_prev >= cross > rsi_now, f"RSI crossed back below {cross:g}"),
                (close < bar.open, "Bearish candle"),
            ]
            if all(ok for ok, _ in short_ok):
                return Verdict("SELL", [why for _, why in short_ok], readings)

        return Verdict("FLAT", [], readings)


STRATEGIES[SimplePullbackStrategy.name] = SimplePullbackStrategy()


class RegimeSwitchStrategy:
    """Three states decided by the EMA stack, with an asymmetric trigger.

        bull      close above EMA 200 and EMA 50 above EMA 200   -> long
        bear      close below EMA 200 and EMA 50 below EMA 200,
                  and the close breaks the previous N-bar low     -> short
        neither   -> flat

    The asymmetry is deliberate and comes from the specification: the long side
    needs only the regime, so it participates in the whole of an uptrend, while
    the short side additionally needs a break of the recent range, so it only
    engages once a decline is confirmed. That is not an oversight — a market
    below its long average often drifts sideways, and a rule that shorted the
    regime alone would be short through all of it.

    The exit is the run's stop and trailing stop. This rule adds one exit of its
    own: when neither regime holds, it returns EXIT rather than FLAT, because
    "sideways -> cash" is a position instruction, not an absence of opinion.
    """

    name = "REGIME_SWITCH_LS"

    DEFAULTS = {
        "mid_period": 50,
        "slow_period": 200,
        "breakout_lookback": 20,
        "atr_period": 14,
        "trade_shorts": True,
    }

    def describe(self, params: Optional[dict] = None) -> dict:
        m = {**self.DEFAULTS, **(params or {})}
        keys = [("mid_period", "Mid EMA"), ("slow_period", "Trend EMA"),
                ("breakout_lookback", "Short breakout channel (bars)"),
                ("atr_period", "ATR period")]
        return {
            "name": self.name,
            "label": "Regime switch (long / short / cash)",
            "description": ("Long while the EMA stack is bullish, short on a range "
                            "break while it is bearish, cash otherwise."),
            "parameters": [{"key": k, "label": l, "type": "int",
                            "default": self.DEFAULTS[k], "value": m[k]} for k, l in keys]
                          + [{"key": "trade_shorts", "label": "Trade shorts", "type": "bool",
                              "default": True, "value": bool(m["trade_shorts"])}],
        }

    def prepare(self, candles: Sequence[Candle], params: dict) -> dict:
        closes = [c.close for c in candles]
        shorts = params.get("trade_shorts", True)
        if isinstance(shorts, str):
            shorts = shorts.strip().lower() not in ("0", "false", "no", "")
        return {
            "ema_mid": ind.ema(closes, int(_param(params, "mid_period", 50, int))),
            "ema_slow": ind.ema(closes, int(_param(params, "slow_period", 200, int))),
            "atr": ind.atr(candles, int(_param(params, "atr_period", 14, int))),
            "lows": [c.low for c in candles],
            "lookback": int(_param(params, "breakout_lookback", 20, int)),
            "shorts": bool(shorts),
        }

    def evaluate(self, candles: Sequence[Candle], i: int, state: dict) -> Verdict:
        mid, slow = state["ema_mid"][i], state["ema_slow"][i]
        if mid is None or slow is None:
            return Verdict("FLAT", ["Insufficient history for indicators"])

        close = candles[i].close
        readings = {"ema50": mid, "ema200": slow, "atr": state["atr"][i]}

        if close > slow and mid > slow:
            readings["regime"] = "bull"
            return Verdict("BUY", [f"Close {close:.2f} above EMA200 {slow:.2f}",
                                   f"EMA50 {mid:.2f} above EMA200"], readings)

        if close < slow and mid < slow:
            readings["regime"] = "bear"
            n = state["lookback"]
            if state["shorts"] and i >= n:
                prior_low = min(state["lows"][i - n:i])
                readings["priorLow"] = prior_low
                if close < prior_low:
                    return Verdict("SELL", [f"Close {close:.2f} below EMA200",
                                            f"EMA50 below EMA200",
                                            f"Close below the {n}-bar low {prior_low:.2f}"],
                                   readings)
            # Bear regime without a break is not a reason to hold a long.
            return Verdict("EXIT", ["Bear regime, no range break yet"], readings)

        readings["regime"] = "sideways"
        return Verdict("EXIT", ["Neither regime confirmed - cash"], readings)


STRATEGIES[RegimeSwitchStrategy.name] = RegimeSwitchStrategy()
