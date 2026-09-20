"""FTMO_MT5 Lab -- MCP server.

Four atomic, single-responsibility tools over the existing, unmodified Lab
(ftmo_mt5/lab/). Each wraps exactly one Lab capability; none of them make a
judgment call the Lab's own engine doesn't already make. This file adds no
new trading logic -- it is a protocol adapter, nothing else.

    lab_list_strategies   -- the strategy catalog (read-only, no computation)
    lab_run_backtest      -- one symbol, one timeframe, one strategy
    lab_run_matrix        -- many symbols x timeframes x strategies, one screen
    lab_validate_strategy -- the OOS / walk-forward / cost-ladder robustness test

Design decisions (settled in the MCP grilling session this server implements):
  - Client-agnostic: stdio transport, works unmodified with Claude Code and
    Claude Desktop.
  - Every optional parameter defaults to the canonical values used consistently
    across this project's own research this session (bars=600, allow_short=True,
    size_to_capital=True) -- overridable, never silently different per caller.
  - Cache-first: a symbol/timeframe not already on disk is refused with a clear
    error unless allow_network=True is passed explicitly. No tool call may
    trigger an unbounded number of live Yahoo Finance fetches by accident.
  - lab_run_matrix defaults to a condensed summary (top candidates + aggregate
    counts) with a full_detail escape hatch, so a 300-cell scan doesn't flood
    an agent's context by default.
  - A bad cell inside lab_run_matrix becomes an error entry *inside* a
    successful response (matching ftmo_mt5.lab.service.matrix()'s own
    behaviour: one bad combination should not cost the rest of the matrix).
    lab_run_backtest and lab_validate_strategy are single-target calls with no
    "rest of the batch" to protect, so they raise a real tool error on failure.

Import boundary, restated because it is the entire safety story: this module
imports only from ftmo_mt5.lab and ftmo_mt5.signals. It cannot import
ftmo_mt5.execution, ftmo_mt5.agents, or ftmo_mt5.main -- so it is structurally
incapable of placing an order, closing a position, connecting to a broker, or
touching a credential, no matter what any tool call asks for.
"""
from __future__ import annotations

import concurrent.futures
from typing import Any, Dict, List, Optional

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from ftmo_mt5.lab import service, verdict, windows
from ftmo_mt5.lab.service import TooManyCells
from ftmo_mt5.signals import list_strategies as _list_strategies_raw

from mcp_server import reference, results_index

# The lab's detailed results echo their own inputs back: a 600-bar candle
# array and a 602-point equity curve per run. Measured, that is ~18,000 tokens
# for a single lab_validate_strategy call -- for data the caller already has,
# since it chose the symbol, timeframe and window. Dropped by default.
_HEAVY_KEYS = ("candles", "equity", "trades_list")

mcp = MCPServer(
    name="ftmo-lab",
    title="FTMO_MT5 Research Lab",
    description=(
        "Backtesting and robustness-validation tools over the FTMO_MT5 project's "
        "Lab. Research and screening only -- no tool here can place, modify, or "
        "close a trade, connect to a broker, or read a credential."
    ),
    version="0.1.0",
)

# Canonical defaults -- the exact values used consistently across this
# project's own research once the allow_short default-mismatch bug was found
# and fixed. Baking them in here is the point: no future caller has to
# rediscover that bug.
DEFAULT_BARS = 600
DEFAULT_ALLOW_SHORT = True
DEFAULT_SIZE_TO_CAPITAL = True
DEFAULT_ALLOW_NETWORK = False


def _spec(strategy: str, strategy_parameters: Optional[Dict[str, Any]],
          allow_short: bool) -> Dict[str, Any]:
    return {
        "strategy": strategy,
        "strategy_parameters": dict(strategy_parameters) if strategy_parameters else {},
        "allow_long": True,
        "allow_short": allow_short,
    }


def _require_cached(pairs: List[tuple], allow_network: bool) -> None:
    """Refuse a network fetch unless explicitly allowed. Raises, doesn't fetch.

    Raises ToolError, not ValueError: this is a *designed refusal*, not a crash.
    The SDK reports an uncaught non-ToolError exception as UnexpectedToolError
    with a full traceback, which would make an intentional guard read like a
    bug to both the calling agent and anyone reading the server log.
    """
    if allow_network:
        return
    missing = [(sym, tf) for sym, tf in pairs if not windows.is_cached(sym, tf)]
    if missing:
        pretty = ", ".join(f"{s}/{t}" for s, t in missing)
        raise ToolError(
            f"Not cached and allow_network=False: {pretty}. "
            "Pass allow_network=True to fetch from Yahoo Finance, or run a "
            "cached-data-only query."
        )


def _trim(obj: Any, full_detail: bool) -> Any:
    """Strip the echoed-back input data unless it was explicitly asked for.

    Recurses because validate() nests three of these blocks (full/dev/oos).
    Adds `detailOmitted` so a caller can tell the difference between "this run
    had no trades" and "the trade list was trimmed to save context".
    """
    if full_detail or not isinstance(obj, dict):
        return obj
    out = {}
    dropped = []
    for k, v in obj.items():
        if k in _HEAVY_KEYS:
            dropped.append(f"{k}[{len(v)}]" if hasattr(v, "__len__") else k)
            continue
        out[k] = _trim(v, full_detail) if isinstance(v, dict) else v
    if dropped:
        out["detailOmitted"] = dropped + ["pass full_detail=true to include"]
    return out


def _check_timeframes(timeframes: List[str]) -> None:
    bad = [t for t in timeframes if t not in windows.TIMEFRAMES]
    if bad:
        raise ToolError(
            f"Unknown timeframe(s): {bad}. Use one of: {', '.join(windows.TIMEFRAMES)}."
        )


def _run_one(index: int, request: Dict[str, Any], bars: int, allow_short: bool,
             size_to_capital: bool) -> Dict[str, Any]:
    """Runs inside a worker process spawned by lab_run_batch.

    Pure compute only -- no results_index writes here. Every worker would open
    its own SQLite connection to the same file; keeping all writes in the
    parent after the pool closes avoids relying on SQLite's busy-timeout to
    paper over concurrent writers for something this cheap to serialize.
    Must stay a module-level function: ProcessPoolExecutor pickles a reference
    to it by name and re-imports this module in the child, so a closure or a
    method wouldn't survive the trip.
    """
    try:
        spec = _spec(request["strategy"], request.get("strategy_parameters"),
                     request.get("allow_short", allow_short))
        out = service.single(
            request["symbol"], request["timeframe"], spec,
            request.get("bars", bars),
            size_to_capital=request.get("size_to_capital", size_to_capital),
        )
        return {"index": index, "request": request, "result": out}
    except Exception as e:                          # noqa: BLE001
        return {"index": index, "request": request,
                "error": f"{type(e).__name__}: {e}"}


@mcp.tool()
def lab_list_strategies() -> List[Dict[str, Any]]:
    """The Lab's full strategy catalog: name, label, description, and default
    parameters for every strategy available to lab_run_backtest / lab_run_matrix
    / lab_validate_strategy. Read-only, no computation, no network access.
    Call this first when a strategy name isn't already known -- never guess one."""
    return list(_list_strategies_raw())


@mcp.tool()
def lab_run_backtest(
    symbol: str,
    timeframe: str,
    strategy: str,
    strategy_parameters: Optional[Dict[str, Any]] = None,
    bars: int = DEFAULT_BARS,
    allow_short: bool = DEFAULT_ALLOW_SHORT,
    size_to_capital: bool = DEFAULT_SIZE_TO_CAPITAL,
    allow_network: bool = DEFAULT_ALLOW_NETWORK,
    full_detail: bool = False,
) -> Dict[str, Any]:
    """Run exactly one backtest: one symbol, one timeframe, one strategy.

    Returns the full single-run result (trades, win rate, return, Buy & Hold
    comparison, Sharpe, drawdown, trade list, equity curve). This is an
    in-sample screen only -- a result here says nothing about whether the
    edge survives unseen data. Use lab_validate_strategy before trusting any
    "beats Buy & Hold" result from this tool.

    The trade list, equity curve and candle array are omitted unless
    full_detail=true -- they are the inputs echoed back, and including them
    costs ~17,000 tokens per call.

    Raises on failure (bad strategy name, uncached data with allow_network=False,
    no bars available) -- there is no batch here to protect from one bad call."""
    _check_timeframes([timeframe])
    _require_cached([(symbol, timeframe)], allow_network)
    spec = _spec(strategy, strategy_parameters, allow_short)
    out = service.single(symbol, timeframe, spec, bars, size_to_capital=size_to_capital)
    results_index.record(out, strategy=strategy, source="lab_run_backtest")
    return _trim(out, full_detail)


@mcp.tool()
def lab_run_matrix(
    symbols: List[str],
    timeframes: List[str],
    strategies: Optional[List[str]] = None,
    strategy: Optional[str] = None,
    strategy_parameters: Optional[Dict[str, Any]] = None,
    param_key: Optional[str] = None,
    values: Optional[List[Any]] = None,
    bars: int = DEFAULT_BARS,
    allow_short: bool = DEFAULT_ALLOW_SHORT,
    size_to_capital: bool = DEFAULT_SIZE_TO_CAPITAL,
    allow_network: bool = DEFAULT_ALLOW_NETWORK,
    full_detail: bool = False,
) -> Dict[str, Any]:
    """Screen many symbols x timeframes x strategies in one call (capped at 300
    cells by the Lab itself). Two modes: pass `strategies` (a list) to test each
    strategy's own defaults across the grid -- the common case, matching every
    sweep run this project has done; or pass a single `strategy` with optional
    `param_key` + `values` to sweep one parameter instead.

    Returns a condensed summary by default: the top 10 cells by alpha over Buy
    & Hold, plus counts (cells beating B&H, cells resting on fewer than 10
    trades -- the Lab's own reliability threshold, cells that errored). Pass
    full_detail=True for every raw cell instead -- a 300-cell matrix's full
    detail is a lot of tokens, so this stays off unless asked for.

    A bad cell (unresolvable symbol, no data, a strategy that never trades)
    becomes an error entry inside this response; it does not fail the call --
    matching the Lab's own service.matrix() behaviour. This tool never decides
    which cells are "good" -- it reports every number it computed; deciding
    what's worth a second look is the caller's job, using lab_validate_strategy."""
    _check_timeframes(timeframes)
    if strategies:
        pairs = [(s, t) for s in symbols for t in timeframes]
    else:
        if not strategy:
            raise ToolError("Pass either `strategies` (a list) or `strategy` (one name).")
        pairs = [(s, t) for s in symbols for t in timeframes]
    _require_cached(pairs, allow_network)

    base_spec = _spec(strategy or "", strategy_parameters, allow_short)
    try:
        rows = service.matrix(
            symbols, timeframes, base_spec, bars=bars, param_key=param_key,
            values=values, strategies=strategies, size_to_capital=size_to_capital,
        )
    except TooManyCells as e:
        # The Lab's own 300-cell cap. A request-level refusal with advice on
        # which axis to cut -- an expected answer, not a crash.
        raise ToolError(str(e)) from e

    if full_detail:
        return {"cells": rows, "cellCount": len(rows)}

    ok = [r for r in rows if "error" not in r]
    errors = [r for r in rows if "error" in r]
    beats = [r for r in ok if r.get("beatsBH")]
    thin = [r for r in ok if (r.get("trades") or 0) < 10]
    top = sorted(ok, key=lambda r: -(r.get("vsBHPct") or -1e18))[:10]

    return {
        "cellCount": len(rows),
        "beatCount": len(beats),
        "beatRatePct": round(len(beats) / len(ok) * 100, 1) if ok else 0.0,
        "thinSampleCount": len(thin),
        "errorCount": len(errors),
        "topByAlpha": top,
        "errors": errors[:10],
        "note": ("Screen only, in-sample. Pass full_detail=True for every cell, "
                "or call lab_validate_strategy on a specific candidate before "
                "treating any 'beats Buy & Hold' result here as real."),
    }


@mcp.tool()
def lab_run_batch(
    requests: List[Dict[str, Any]],
    bars: int = DEFAULT_BARS,
    allow_short: bool = DEFAULT_ALLOW_SHORT,
    size_to_capital: bool = DEFAULT_SIZE_TO_CAPITAL,
    allow_network: bool = DEFAULT_ALLOW_NETWORK,
    full_detail: bool = False,
) -> Dict[str, Any]:
    """Run up to 10 independent backtests concurrently, across real CPU cores,
    in a single call -- give it a list of requests, get every result back
    together.

    Each entry in `requests` needs symbol, timeframe, strategy (the same
    shape lab_run_backtest takes), plus an optional strategy_parameters dict.
    Per-request bars/allow_short/size_to_capital override the call-level
    defaults above when present, so a batch can mix settings across requests.

    This is a process pool, not a thread pool: the Lab's backtest loop
    (ftmo_mt5/backtest/engine.py) is plain Python iterating bar-by-bar, so it
    holds the GIL for its whole run -- threads would only interleave, never
    overlap. Separate processes give real wall-clock concurrency, one request
    per core up to the pool size. At 600 bars a single run is already fast
    (tens of milliseconds), so the main win here is one round-trip for up to
    10 requests rather than raw speed -- the gap widens for heavier runs
    (more bars, or the same pattern applied to lab_validate_strategy's
    request shape, which is not what this tool does).

    A failing request becomes an error entry at its own index rather than
    failing the whole batch, same philosophy as lab_run_matrix. Capped at 10
    -- for a larger sweep use lab_run_matrix, which is built for hundreds of
    cells and shares fetched data across cells instead of paying per-request
    process-spawn overhead.

    Results come back sorted by the original request order, each carrying its
    index and the request that produced it, so a caller can match rows back
    up without relying on submission order being preserved."""
    if not requests:
        raise ToolError("Pass at least one request.")
    if len(requests) > 10:
        raise ToolError(f"{len(requests)} requests is over the 10-call cap for "
                        "lab_run_batch. Use lab_run_matrix for a larger sweep.")
    required = {"symbol", "timeframe", "strategy"}
    for i, r in enumerate(requests):
        missing = required - r.keys()
        if missing:
            raise ToolError(f"requests[{i}] missing {sorted(missing)}")

    _check_timeframes([r["timeframe"] for r in requests])

    # Per-request cache check, not the batch-wide _require_cached: one
    # uncached symbol must become an error at its own index, not take down
    # every other request in the call.
    runnable, rows = [], []
    for i, r in enumerate(requests):
        if not allow_network and not windows.is_cached(r["symbol"], r["timeframe"]):
            rows.append({"index": i, "request": r,
                        "error": f"Not cached and allow_network=False: "
                                 f"{r['symbol']}/{r['timeframe']}. Pass "
                                 "allow_network=True or run a cached-data-only query."})
        else:
            runnable.append((i, r))

    if runnable:
        with concurrent.futures.ProcessPoolExecutor(max_workers=len(runnable)) as pool:
            futures = [pool.submit(_run_one, i, r, bars, allow_short, size_to_capital)
                      for i, r in runnable]
            rows.extend(f.result() for f in futures)

    rows.sort(key=lambda row: row["index"])
    for row in rows:
        if "result" in row:
            results_index.record(row["result"], strategy=row["request"]["strategy"],
                                 source="lab_run_batch")
            row["result"] = _trim(row["result"], full_detail)

    ok = [r for r in rows if "result" in r]
    errors = [r for r in rows if "error" in r]
    return {"count": len(rows), "okCount": len(ok), "errorCount": len(errors),
            "results": rows}


@mcp.tool()
def lab_validate_strategy(
    symbol: str,
    timeframe: str,
    strategy: str,
    strategy_parameters: Optional[Dict[str, Any]] = None,
    bars: int = DEFAULT_BARS,
    allow_short: bool = DEFAULT_ALLOW_SHORT,
    size_to_capital: bool = DEFAULT_SIZE_TO_CAPITAL,
    allow_network: bool = DEFAULT_ALLOW_NETWORK,
    param_key: Optional[str] = None,
    full_detail: bool = False,
) -> Dict[str, Any]:
    """Subject one candidate (symbol, timeframe, strategy) to the Lab's full
    robustness suite: a 70/30 out-of-sample split, a slippage cost ladder
    (1/2/4/8 ticks), top-trade removal (1/3/5), a 5-fold walk-forward test, and
    parameter sensitivity -- then the resulting grade: ROBUST, MODERATE,
    FRAGILE, or FAILED, exactly as ftmo_mt5.lab.verdict.py computes it.

    This is the only tool in this server whose output should ever be treated
    as evidence that something "really" beats Buy & Hold. A raw win from
    lab_run_backtest or lab_run_matrix is a screen, not a finding -- this tool
    is what tells you whether it survives contact with unseen data. In this
    project's own testing, the grade ROBUST has never yet been reached on any
    symbol tested; MODERATE is already a meaningful result.

    Trade lists, equity curves and candle arrays are omitted unless
    full_detail=true: this call carries three of them (full/dev/oos), which
    measured ~18,000 tokens per invocation for data the caller supplied.

    Raises on failure (bad strategy name, uncached data with allow_network=False,
    no bars available)."""
    _check_timeframes([timeframe])
    _require_cached([(symbol, timeframe)], allow_network)
    spec = _spec(strategy, strategy_parameters, allow_short)
    out = service.validate(symbol, timeframe, spec, bars, param_key,
                           size_to_capital=size_to_capital)
    results_index.record(out.get("full", {}), strategy=strategy,
                         verdict=out.get("verdict"), source="lab_validate_strategy")
    return _trim(out, full_detail)


@mcp.tool()
def lab_lookup(
    symbol: Optional[str] = None,
    timeframe: Optional[str] = None,
    strategy: Optional[str] = None,
    verdict_filter: Optional[str] = None,
    beats_bh_only: bool = False,
    limit: int = 25,
) -> Dict[str, Any]:
    """Has this already been tested? Check before spending a run on it.

    Searches ~3,500 backtests this project has already computed and indexed.
    Answers the question that otherwise costs a full sweep: "did we try this,
    and what happened?" The lab is deterministic on cached data, so a repeat
    run of an indexed combination produces the same number at full cost.

    Returns a verdict tally first and rows second, so "tested 30 times, all
    FAILED" costs one short answer instead of thirty. Filter with
    verdict_filter ("ROBUST"/"MODERATE"/"FRAGILE"/"FAILED") or beats_bh_only
    to skip straight to whatever cleared a bar.

    Every row records the bar window it was computed over. Yahoo history moves,
    so treat an old row as a strong prior about where to look, not as a result
    that never needs recomputing."""
    return results_index.lookup(symbol=symbol, timeframe=timeframe,
                                strategy=strategy, verdict=verdict_filter,
                                beats_bh_only=beats_bh_only, limit=limit)


@mcp.resource("lab://reference", name="Lab interface reference",
              description="Tools, canonical defaults, verdict rubric, strategy list, "
                          "and standing findings. Read this instead of the source.",
              mime_type="text/plain")
def _reference() -> str:
    return reference.overview_reference()


@mcp.resource("lab://metrics", name="Metric dictionary",
              description="Every key measure() returns and what it means, including "
                          "the cost model. Reconciles itself against a live run.",
              mime_type="text/plain")
def _metrics_ref() -> str:
    return reference.metrics_reference()


@mcp.resource("lab://timeframes", name="Timeframes and their real spans",
              description="Each timeframe's bars-per-year, what 600 bars actually "
                          "covers in calendar time, and the provider ceiling.",
              mime_type="text/plain")
def _timeframes_ref() -> str:
    return reference.timeframes_reference()


if __name__ == "__main__":
    mcp.run()
