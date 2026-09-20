"""Turning one form submission into a set of runs.

A matrix is symbols × timeframes × parameter values of a single frozen
strategy. Freezing is the point: the reason a matrix is more informative than
the same runs done one at a time is that only the axes vary, so a difference
in the grid is a difference in the axis and not in the setup.

Two rules are enforced here rather than left to the caller. Each
symbol-and-timeframe is fetched exactly once and shared by every cell that
needs it, so no comparison spans two downloads. And a submission is capped at
300 cells — above that the form is told which dimension to cut, because a
question needing more than 300 cells is usually one that has not been narrowed.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Callable, Dict, Iterable, List, Optional

from ftmo_mt5.instruments.metadata import resolve_instrument
from ftmo_mt5.lab import metrics, verdict, windows
from ftmo_mt5.signals import list_strategies

MAX_CELLS = 300


def size_for(symbol: str, candles: List, capital: float) -> int:
    """How many units to trade so the position is worth about the capital.

    One unit means wildly different money depending on the instrument. One /NQ
    contract is roughly $364,000 of index; one share of a $120 stock is $120.
    Left at one unit, a stock backtest moves the account by tens of dollars, its
    buy-and-hold benchmark lands under the lab's flat-benchmark floor, and every
    result reads as "no opponent" — a units problem wearing the costume of a
    finding.

    Futures clamp to a single contract, because one is already more notional
    than the account.
    """
    if not candles:
        return 1
    try:
        multiplier = resolve_instrument(symbol).contract_multiplier or 1.0
    except Exception:                                # noqa: BLE001 — unknown symbol trades as one unit
        multiplier = 1.0
    per_unit = candles[0].close * multiplier
    if per_unit <= 0:
        return 1
    return max(1, int(capital // per_unit))


def strategy_defaults() -> Dict[str, Dict]:
    """Each strategy's own shipped parameters, keyed by name.

    A strategy axis cannot reuse the parameters on the form: those belong to
    the one strategy that was selected, and an SMA period means nothing to a
    Donchian breakout. Every strategy on the axis therefore runs on its own
    defaults, which is also the only setting all of them can be compared on
    fairly.
    """
    return {s["name"]: {p["key"]: p.get("value", p["default"])
                        for p in s["parameters"]}
            for s in list_strategies()}


class TooManyCells(ValueError):
    """The submission is larger than the cap, with advice on what to cut."""


def _cells(symbols: List[str], timeframes: List[str], param_key: Optional[str],
           values: List, strategies: Optional[List[str]] = None) -> List[Dict]:
    values = list(values) if (param_key and values) else [None]
    strategies = list(strategies) if strategies else [None]
    total = len(symbols) * len(timeframes) * len(values) * len(strategies)
    if total > MAX_CELLS:
        widest = max((len(strategies), "strategies"), (len(symbols), "symbols"),
                     (len(timeframes), "timeframes"),
                     (len(values), "parameter values"))[1]
        raise TooManyCells(
            f"{total} runs is over the {MAX_CELLS}-run cap "
            f"({len(strategies)} strategies x {len(symbols)} symbols x "
            f"{len(timeframes)} timeframes x {len(values)} parameter values). "
            f"Cut the {widest}.")
    return [{"symbol": sym, "timeframe": t, "value": v, "strategy": st}
            for st in strategies for sym in symbols
            for t in timeframes for v in values]


def _spec_for(spec: Dict, param_key: Optional[str], value,
              strategy: Optional[str] = None,
              defaults: Optional[Dict[str, Dict]] = None) -> Dict:
    out = deepcopy(spec)
    if strategy is not None:
        out["strategy"] = strategy
        out["strategy_parameters"] = dict((defaults or {}).get(strategy, {}))
    if param_key is not None and value is not None:
        params = dict(out.get("strategy_parameters") or {})
        params[param_key] = value
        out["strategy_parameters"] = params
    return out


def plan(symbols: List[str], timeframes: List[str], bars: Optional[int],
         param_key: Optional[str] = None, values: Optional[List] = None,
         strategies: Optional[List[str]] = None) -> Dict:
    """What a submission would run, without running it. Used to refuse early."""
    cells = _cells(symbols, timeframes, param_key, values or [], strategies)
    return {"cells": len(cells), "cap": MAX_CELLS, "bars": bars,
            "strategies": len(strategies or []) or 1,
            "series": len({(c["symbol"], c["timeframe"]) for c in cells})}


def matrix(symbols: List[str], timeframes: List[str], spec: Dict,
           bars: Optional[int] = None, param_key: Optional[str] = None,
           values: Optional[List] = None,
           on_cell: Optional[Callable[[Dict], None]] = None,
           strategies: Optional[List[str]] = None,
           size_to_capital: bool = False) -> List[Dict]:
    """Run every cell, reporting each one as it lands.

    `on_cell` is how the grid fills in live instead of the user waiting on a
    single response. A cell that raises is reported as an error cell and the
    rest of the matrix continues — one bad combination should not cost the
    other twenty-nine.
    """
    cells = _cells(symbols, timeframes, param_key, values or [], strategies)
    defaults = strategy_defaults() if strategies else {}
    fetched: Dict[tuple, Dict] = {}
    out: List[Dict] = []

    for index, cell in enumerate(cells):
        key = (cell["symbol"], cell["timeframe"])
        try:
            if key not in fetched:
                fetched[key] = windows.window(cell["symbol"], cell["timeframe"], bars)
            w = fetched[key]
            if not w["candles"]:
                raise ValueError("the provider returned no bars for this window")
            cell_spec = _sized(
                _spec_for(spec, param_key, cell["value"], cell["strategy"], defaults),
                cell["symbol"], w["candles"], size_to_capital)
            row = metrics.measure(cell["symbol"], cell["timeframe"], cell_spec,
                                  w["candles"])
            row["positionSize"] = cell_spec["position_size"]
            row.update({"index": index, "total": len(cells),
                        "paramKey": param_key, "paramValue": cell["value"],
                        "requestedBars": w["requested"], "shortWindow": w["short"],
                        "ceiling": w["ceiling"]})
        except Exception as e:                      # noqa: BLE001
            row = {"index": index, "total": len(cells), "symbol": cell["symbol"],
                   "timeframe": cell["timeframe"], "paramKey": param_key,
                   "paramValue": cell["value"],
                   "strategy": cell["strategy"] or spec.get("strategy"),
                   "error": f"{type(e).__name__}: {e}"}
        out.append(row)
        if on_cell:
            on_cell(row)
    return out


def single(symbol: str, timeframe: str, spec: Dict, bars: Optional[int] = None,
           size_to_capital: bool = False) -> Dict:
    """One run, with the trade list, equity curve and candles attached."""
    w = windows.window(symbol, timeframe, bars)
    if not w["candles"]:
        raise ValueError(f"No bars available for {symbol} on {timeframe}.")
    spec = _sized(spec, symbol, w["candles"], size_to_capital)
    result = metrics.measure(symbol, timeframe, spec, w["candles"], detail=True)
    result["positionSize"] = spec["position_size"]
    result.update({"requestedBars": w["requested"], "shortWindow": w["short"],
                   "ceiling": w["ceiling"]})
    result["warnings"] = warnings_for(result)
    return result


def validate(symbol: str, timeframe: str, spec: Dict, bars: Optional[int] = None,
             param_key: Optional[str] = None,
             size_to_capital: bool = False) -> Dict:
    w = windows.window(symbol, timeframe, bars)
    if not w["candles"]:
        raise ValueError(f"No bars available for {symbol} on {timeframe}.")
    spec = _sized(spec, symbol, w["candles"], size_to_capital)
    out = verdict.validate(symbol, timeframe, spec, w["candles"], param_key)
    out["full"].update({"requestedBars": w["requested"], "shortWindow": w["short"],
                        "ceiling": w["ceiling"]})
    out["warnings"] = warnings_for(out["full"])
    return out


def _sized(spec: Dict, symbol: str, candles: List, enabled: bool) -> Dict:
    if not enabled:
        return spec
    out = deepcopy(spec)
    out["position_size"] = float(
        size_for(symbol, candles, out.get("initial_capital", 100_000.0)))
    return out


def warnings_for(result: Dict) -> List[str]:
    """The guardrails, said out loud on the result rather than left to be noticed."""
    out: List[str] = []
    if abs(result.get("bh", 0.0)) < verdict.FLAT_BENCHMARK:
        out.append(
            f"Buy & hold made only ${result['bh']:,.0f} over this window. A flat "
            "benchmark is not an opponent — beating it says the market went "
            "nowhere, not that the rule works.")
    if result.get("warmupBars") and result.get("usableBars", 0) < result["warmupBars"]:
        out.append(
            f"{result['bars']} bars, but the longest lookback is "
            f"{result['warmupBars']}, leaving {result['usableBars']} bars the rule "
            "could act on. The window is too short for these settings.")
    if result.get("shortWindow"):
        out.append(
            f"Asked for {result.get('requestedBars')} bars, served "
            f"{result.get('bars')}. {result.get('ceiling', '')}".strip())
    if result.get("topTradeShare", 0) >= 50 and result.get("trades", 0) > 1:
        out.append(
            f"{result['topTradeShare']:.0f}% of gross profit came from a single "
            "trade. That is one outcome wearing the costume of a system.")
    if result.get("trades", 0) == 0:
        out.append(
            "This configuration never traded over this window, so there is no "
            "result to read. If it is marked as beating buy & hold, that only "
            "means the benchmark lost money while the rule sat out.")
    if 0 < result.get("trades", 0) < 10:
        out.append(f"{result['trades']} trades is too small a sample to separate a "
                   "rule from a coincidence.")
    return out
