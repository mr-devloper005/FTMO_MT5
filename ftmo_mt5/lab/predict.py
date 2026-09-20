"""What each instrument's best-scoring rule would do into the next bar.

Two things this is not, stated here because the word on the button invites both
mistakes.

It is not a forecast of the market. Nothing here models what price will do. It
reports the stance a rule already holds — long, short or out — after the last
completed bar, which is a fact about the rule, not a claim about the future.

And "best" means best on the window measured, which is the single most
overfittable idea in the whole tool. A rule is only put forward here if it
cleared the same sanity the rest of the lab applies: a benchmark worth beating,
enough trades to mean something, and no single trade carrying the result. When
nothing clears it, the instrument gets no pick and says so, because "the least
bad of fourteen bad rules" is not a recommendation.
"""
from __future__ import annotations

from typing import Callable, Dict, List, Optional

from ftmo_mt5.backtest import risk_defaults
from ftmo_mt5.lab import metrics, service, verdict, windows
from ftmo_mt5.signals import SignalEngine, StrategyConfig

# What a rule must clear before it is allowed to speak for an instrument.
MIN_TRADES = 10
MAX_TOP_TRADE_SHARE = 50.0


def stance(symbol: str, timeframe: str, strategy: str, parameters: Dict,
           candles: List, allow_short: bool = True) -> Dict:
    """Long, short or out, going into the bar after the last completed one.

    The last bar's own verdict is not enough: FLAT means "no new signal", not
    "no position". So the verdicts are walked the way the engine walks them and
    the resulting position is read off the end.
    """
    engine = SignalEngine(StrategyConfig(strategy, parameters))
    signals = engine.run_all(windows.series(symbol, timeframe, candles))
    if not signals:
        return {"stance": "FLAT", "since": None, "reasons": [],
                "note": "the rule produced no signals over this window"}

    position, since = None, None
    for s in signals:
        if s.signal == "BUY":
            if position != "LONG":
                position, since = "LONG", s
        elif s.signal == "SELL":
            if allow_short:
                if position != "SHORT":
                    position, since = "SHORT", s
            elif position == "LONG":
                position, since = None, None
        elif s.signal == "EXIT":
            position, since = None, None

    last = signals[-1]
    return {
        "stance": {"LONG": "UP", "SHORT": "DOWN"}.get(position, "FLAT"),
        "position": position,
        "since": since.timestamp if since else None,
        "sincePrice": since.price if since else None,
        "lastVerdict": last.signal,
        "lastPrice": last.price,
        "lastBar": last.timestamp,
        "reasons": list(since.reasons) if since else list(last.reasons),
        "indicators": dict(last.indicators or {}),
    }


def for_symbol(symbol: str, timeframes: List[str], bars: Optional[int],
               base: Optional[Dict] = None,
               cached_only: bool = True) -> Dict:
    """Score every strategy on every timeframe, then read the winner's stance.

    The timeframe is part of what is being chosen, not fixed in advance. A rule
    that works on 30-minute bars and nothing else is still the best answer this
    instrument has, and picking the strategy first and the timeframe second
    would never find it.

    With `cached_only` nothing is downloaded: a series that is not already on
    disk is skipped and reported. The whole point is to read what the backtests
    already measured, not to go back to the provider.
    """
    base = dict(base or {})
    defaults = service.strategy_defaults()
    scored: List[Dict] = []
    skipped, failed, windows_used = [], 0, {}

    for timeframe in timeframes:
        if cached_only and not windows.is_cached(symbol, timeframe):
            skipped.append(timeframe)
            continue
        try:
            w = windows.window(symbol, timeframe, bars)
        except Exception as e:                              # noqa: BLE001
            skipped.append(f"{timeframe} ({type(e).__name__})")
            continue
        if not w["candles"]:
            skipped.append(f"{timeframe} (no bars)")
            continue
        windows_used[timeframe] = w

        size = service.size_for(symbol, w["candles"],
                                base.get("initial_capital", 100_000.0))
        # Resolved per instrument and timeframe, never inherited from the form.
        per_run = dict(base)
        per_run.pop("trailing_stop_atr", None)
        measured = risk_defaults.resolve(symbol, timeframe)
        if measured is not None:
            per_run["trailing_stop_atr"] = measured

        for name, params in defaults.items():
            spec = dict(per_run, strategy=name, strategy_parameters=dict(params),
                        position_size=float(size))
            try:
                r = metrics.measure(symbol, timeframe, spec, w["candles"])
                r["trailingStopAtr"] = measured
                r["positionSize"] = size
                scored.append(r)
            except Exception:                               # noqa: BLE001
                failed += 1

    row = {"symbol": symbol, "tested": len(scored), "failedToRun": failed,
           "timeframesTested": list(windows_used), "timeframesSkipped": skipped}

    if not scored:
        row.update({"stance": None, "confidence": "none", "qualified": 0,
                    "why": ("Nothing cached for this instrument — run the backtests "
                            "first, then predict reads what they measured."
                            if skipped else "No strategy produced a result here.")})
        return row

    qualified = [r for r in scored
                 if r["beatsBH"] and r["trades"] >= MIN_TRADES
                 and r["topTradeShare"] < MAX_TOP_TRADE_SHARE
                 and abs(r["bh"]) >= verdict.FLAT_BENCHMARK]
    row["qualified"] = len(qualified)

    # When nothing clears the gates the instrument still gets an answer, but a
    # labelled one. Returning a blank row was honest and useless: the reader
    # wants to know what the best available rule is doing even when "best
    # available" is weak. The grade travels with the number instead.
    confidence = "qualified"
    if qualified:
        pool = qualified
    else:
        confidence = "weak"
        traded = [r for r in scored if r["trades"] > 0]
        pool = [r for r in traded if r["beatsBH"]] or traded
        if not pool:
            row.update({"stance": None, "confidence": "none", "why":
                        "No strategy traded at all on any cached timeframe."})
            return row

    best = max(pool, key=lambda r: r["vsBH"])
    row["confidence"] = confidence
    if confidence == "weak":
        shortfall = []
        if best["trades"] < MIN_TRADES:
            shortfall.append(f"only {best['trades']} trades")
        if best["topTradeShare"] >= MAX_TOP_TRADE_SHARE:
            shortfall.append(f"{best['topTradeShare']:.0f}% of profit from one trade")
        if abs(best["bh"]) < verdict.FLAT_BENCHMARK:
            shortfall.append("buy & hold went nowhere, so beating it means nothing")
        if not best["beatsBH"]:
            shortfall.append("it does not even beat buy & hold")
        row["why"] = (f"Nothing cleared the checks across {len(scored)} combinations. "
                      f"This is the best available rule, and it is weak: "
                      + ", ".join(shortfall) + ".")
    tf = best["timeframe"]
    row.update({
        "timeframe": tf, "strategy": best["strategy"], "trades": best["trades"],
        "bars": best["bars"], "net": best["net"], "bh": best["bh"],
        "vsBH": best["vsBH"], "vsBHPct": best["vsBHPct"], "beatPct": best["beatPct"],
        "pf": best["pf"], "winRate": best["winRate"], "maxDDPct": best["maxDDPct"],
        "topTradeShare": best["topTradeShare"],
        "positionSize": best["positionSize"],
        "trailingStopAtr": best["trailingStopAtr"],
        "runnersUp": [f"{r['strategy']} {r['timeframe']}" for r in
                      sorted(qualified, key=lambda r: -r["vsBH"])[1:4]],
    })
    row.update(stance(symbol, tf, best["strategy"], defaults[best["strategy"]],
                      windows_used[tf]["candles"],
                      allow_short=bool(base.get("allow_short", True))))

    # Which bar the call is actually about. "Next bar" means the next bar of the
    # winning timeframe, not the next day — a 1h pick and a 1w pick are
    # statements about very different stretches of time, and the row should say
    # which one rather than leaving the reader to infer it.
    seconds = windows.SECONDS.get(tf)
    if seconds and row.get("lastBar"):
        row["barSeconds"] = seconds
        row["nextBarStart"] = int(row["lastBar"]) + seconds
        row["nextBarEnd"] = int(row["lastBar"]) + 2 * seconds
    return row


def run(symbols: List[str], timeframes: List[str], bars: Optional[int],
        base: Optional[Dict] = None,
        on_row: Optional[Callable[[Dict], None]] = None,
        cached_only: bool = True) -> List[Dict]:
    out = []
    for i, symbol in enumerate(symbols):
        row = for_symbol(symbol, timeframes, bars, base, cached_only)
        row.update({"index": i, "total": len(symbols)})
        out.append(row)
        if on_row:
            on_row(row)
    return out
