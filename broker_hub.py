"""Broker hub — the trading terminal and one API in front of every broker.

Runs on the Mac and fans out to:
  * **MetaTrader 5** — over HTTP to the bridge inside the Windows VM
  * **tastytrade** — natively, over their REST API (equities, options, futures)
  * **Webull** — natively, over Webull's official OpenAPI

Every route is broker-scoped (`/api/{broker}/...`) and returns the same shape
whichever backend answered, so the terminal never special-cases a broker. A
broker that is down degrades to an error on its own routes and leaves the
others untouched.

    python broker_hub.py            # http://localhost:8600  (the only UI)
"""

__all__ = ["app", "main"]

import asyncio
import csv
import io
import json
import logging
import os
import shutil
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel

from ftmo_mt5.backtest import BacktestConfig, ExecutionModel, run_backtest
from ftmo_mt5.backtest.models import OptionAction, TradeMode
from ftmo_mt5.backtest import risk_defaults
from ftmo_mt5.options import ExpiryRule
from ftmo_mt5.execution.remote_gateway import RemoteMT5Gateway
from ftmo_mt5.execution.tastytrade_gateway import TastytradeGateway
from ftmo_mt5.execution.webull_gateway import WebullGateway
from ftmo_mt5.instruments import list_instruments
from ftmo_mt5.instruments.metadata import resolve_instrument
from ftmo_mt5.marketdata import DataUnavailable, TIMEFRAMES, list_providers
from ftmo_mt5.marketdata.registry import register_gateway_provider
from ftmo_mt5.marketdata.replay import REPLAY_TIMEFRAMES, load_replay_series
from ftmo_mt5.signals import SignalEngine, StrategyConfig, list_strategies

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("broker_hub")

HUB_PORT = int(os.getenv("HUB_PORT", "8600"))
MT5_VM_URL = os.getenv("MT5_VM_URL", "http://localhost:8500")
TERMINAL_FILE = Path(__file__).parent / "trading_dashboard_live.html"
# A standalone page, not part of the terminal shell: it takes over the
# whole viewport and has nothing to do with live broker state.
GAME_FILE = Path(__file__).parent / "beat_the_hold.html"
# The research lab: the same engine, but a window, a grid, a grade
# and a library around it.
LAB_FILE = Path(__file__).parent / "backtest_lab.html"

AGENT_FILE = Path(__file__).parent / "claude_agent.html"
# How often the terminal stream pushes a fresh snapshot.
STREAM_INTERVAL_S = float(os.getenv("STREAM_INTERVAL_S", "1.0"))

# Default watchlist, in MT5 symbol names. A broker whose instrument universe is
# different — Webull lists US equities, not forex — overrides this with its own
# `default_symbols`.
WATCHLIST = ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD", "US30", "BTCUSD"]


def watchlist_for(gateway: Any) -> List[str]:
    return list(getattr(gateway, "default_symbols", None) or WATCHLIST)


# ---------------------------------------------------------------------------
# Broker registry
# ---------------------------------------------------------------------------

class Broker:
    """One configured broker plus its connection state."""

    def __init__(self, key: str, gateway: Any, autoconnect: bool) -> None:
        self.key = key
        self.gateway = gateway
        self.autoconnect = autoconnect
        self.last_error = ""

    def connect(self) -> bool:
        try:
            self.gateway.initialize()
            self.last_error = ""
            return True
        except Exception as e:
            self.last_error = str(e)
            log.warning("%s connect failed: %s", self.key, e)
            return False


BROKERS: Dict[str, Broker] = {
    "mt5": Broker("mt5", RemoteMT5Gateway(vm_url=MT5_VM_URL), autoconnect=True),
    "tasty": Broker("tasty", TastytradeGateway(), autoconnect=os.getenv("TT_AUTOCONNECT", "1") == "1"),
    "webull": Broker("webull", WebullGateway(), autoconnect=os.getenv("WEBULL_AUTOCONNECT", "0") == "1"),
}


def get_broker(key: str) -> Broker:
    broker = BROKERS.get(key)
    if broker is None:
        raise HTTPException(status_code=404, detail=f"Unknown broker '{key}'")
    return broker


# The research layer reads candles through its own provider interface. Register
# the live gateways there so a backtest can run on broker data as well as on
# Yahoo history, without the backtest ever importing a gateway.
for _key, _label in (("mt5", "MetaTrader 5 (live feed)"),
                     ("tasty", "tastytrade (live feed)")):
    register_gateway_provider(_key, BROKERS[_key].gateway, _label)


def _num(value: Any) -> float:
    """Decimals are exact but not JSON — convert only at the boundary."""
    return float(value) if isinstance(value, Decimal) else value


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="Broker Hub", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class OrderRequest(BaseModel):
    symbol: str
    side: str = "buy"
    volume: float = 0.01
    sl: float = 0.0
    tp: float = 0.0


# These pages are read from disk on every request and edited constantly, so a
# cached copy is always the wrong copy: the server restarts with new markup and
# the browser keeps showing the old one.
NO_CACHE = {"Cache-Control": "no-store, must-revalidate", "Pragma": "no-cache"}

# AI Research dashboard (/research + /api/research/*). Read-only output layer
# over research_agent's store; registered before the /api/{broker_key}/...
# catch-alls. Optional: a research import problem must never stop the terminal.
try:
    from research_agent.web import autostart as research_autostart, router as research_router
    app.include_router(research_router)
    research_autostart()            # restarts the separate research worker if it was left on
except Exception as _research_err:                      # noqa: BLE001
    logging.getLogger("broker_hub").warning("AI Research dashboard disabled: %s", _research_err)


@app.get("/", response_class=HTMLResponse)
async def terminal() -> HTMLResponse:
    """The full trading terminal, now serving every broker rather than only MT5."""
    if TERMINAL_FILE.exists():
        return HTMLResponse(TERMINAL_FILE.read_text(encoding="utf-8"), headers=NO_CACHE)
    return HTMLResponse("<h1>trading_dashboard_live.html not found</h1>", status_code=404)


@app.get("/game", response_class=HTMLResponse)
async def game() -> HTMLResponse:
    """Beat the Hold — trade real Nasdaq history against buy-and-hold.

    Served from the hub so it sits behind the same address as the terminal,
    but on its own page: the game owns the full screen and reads no live data.
    """
    if GAME_FILE.exists():
        return HTMLResponse(GAME_FILE.read_text(encoding="utf-8"), headers=NO_CACHE)
    return HTMLResponse("<h1>beat_the_hold.html not found</h1>", status_code=404)


# ---------------------------------------------------------------------------
# Backtest Lab — the research loop as a tool. Everything below reuses the same
# engine the panel uses; what is added is the window, the grid, the grading and
# the library, which is the part that used to happen in conversation.
# ---------------------------------------------------------------------------

from ftmo_mt5.lab import (predict as lab_predict, service as lab_service,
                          store as lab_store, windows as lab_windows)


class LabRunRequest(BaseModel):
    """One configuration. `bars` is a count from the end, not a date range."""

    symbol: str = "/NQ"
    timeframe: str = "1d"
    strategy: str = "DAILY_SWING_SMA200"
    strategyParameters: Dict[str, Any] = {}
    bars: Optional[int] = 600

    commission: float = 2.50
    slippageTicks: float = 1.0
    initialCapital: float = 100_000.0
    positionSize: float = 1.0
    allowLong: bool = True
    allowShort: bool = False
    # Trade roughly the capital rather than one unit. One share of a $120 stock
    # is $120 of exposure against a $100,000 account, which makes every stock
    # result and its benchmark too small to mean anything.
    sizeToCapital: bool = True

    stopLossAtr: Optional[float] = None
    takeProfitAtr: Optional[float] = None
    trailingStopAtr: Optional[float] = None
    maxBarsInTrade: Optional[int] = None

    # Validation only: which parameter the sensitivity test varies. Defaults to
    # the first numeric parameter the strategy exposes.
    paramKey: Optional[str] = None
    label: str = ""
    save: bool = True


class LabMatrixRequest(LabRunRequest):
    """The same configuration, frozen, run across axes."""

    symbols: List[str] = []
    timeframes: List[str] = []
    sweepKey: Optional[str] = None
    sweepValues: List[Any] = []
    # A strategy axis. Each name on it runs on its own shipped parameters —
    # the form's parameters belong to the one strategy that was selected.
    strategies: List[str] = []


def _lab_spec(req: "LabRunRequest") -> Dict[str, Any]:
    """Request -> the engine keyword arguments the lab passes through."""
    spec: Dict[str, Any] = {
        "strategy": req.strategy,
        "strategy_parameters": req.strategyParameters or {},
        "commission": req.commission,
        "slippage_ticks": req.slippageTicks,
        "initial_capital": req.initialCapital,
        "position_size": req.positionSize,
        "allow_long": req.allowLong,
        "allow_short": req.allowShort,
    }
    for field, key in (("stopLossAtr", "stop_loss_atr"),
                       ("takeProfitAtr", "take_profit_atr"),
                       ("trailingStopAtr", "trailing_stop_atr"),
                       ("maxBarsInTrade", "max_bars_in_trade")):
        value = getattr(req, field)
        if value is not None:
            spec[key] = value
    return spec


@app.get("/lab", response_class=HTMLResponse)
async def lab_page() -> HTMLResponse:
    """The lab, on its own page: it owns the screen and reads no live state."""
    if LAB_FILE.exists():
        return HTMLResponse(LAB_FILE.read_text(encoding="utf-8"), headers=NO_CACHE)
    return HTMLResponse("<h1>backtest_lab.html not found</h1>", status_code=404)


@app.get("/api/lab/config")
async def lab_config() -> Dict[str, Any]:
    """What the lab may offer, driven off the registries and the store."""
    return {
        "timeframes": lab_windows.TIMEFRAMES,
        "ceilings": lab_windows.CEILING,
        "strategies": list_strategies(),
        "instruments": list_instruments(),
        "maxCells": lab_service.MAX_CELLS,
        # Measured trailing-stop defaults, so the lab starts from a number that
        # was actually swept for that instrument and timeframe rather than from
        # whatever was last typed. `None` is a real answer, not a gap.
        "trailingDefaults": risk_defaults.all_entries(),
        # What the backtests have already pulled, and therefore what a
        # prediction can read without going back to the provider.
        "cachedSymbols": lab_windows.cached_symbols(),
        "flatBenchmark": lab_service.verdict.FLAT_BENCHMARK,
        "library": lab_store.status(),
        "defaults": {"bars": 600, "commission": 2.50, "slippageTicks": 1.0,
                     "initialCapital": 100_000.0},
    }


@app.post("/api/lab/plan")
async def lab_plan(req: LabMatrixRequest) -> Dict[str, Any]:
    """How many cells this submission is, refused early if it is over the cap."""
    try:
        return lab_service.plan(req.symbols or [req.symbol],
                                req.timeframes or [req.timeframe],
                                req.bars, req.sweepKey, req.sweepValues,
                                req.strategies)
    except lab_service.TooManyCells as e:
        raise HTTPException(status_code=422, detail=str(e))


@app.post("/api/lab/run")
async def lab_run(req: LabRunRequest) -> Dict[str, Any]:
    """One run, with the trade list, equity curve and candles for the chart."""
    spec = _lab_spec(req)
    try:
        result = await asyncio.to_thread(
            lab_service.single, req.symbol, req.timeframe, spec, req.bars,
            req.sizeToCapital)
    except DataUnavailable as e:
        raise HTTPException(status_code=422, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    if req.save:
        result["runId"] = await asyncio.to_thread(
            lab_store.save, result, req.strategyParameters, req.label, None)
    return result


@app.post("/api/lab/validate")
async def lab_validate(req: LabRunRequest) -> Dict[str, Any]:
    """The robustness battery and the grade it adds up to."""
    spec = _lab_spec(req)
    try:
        out = await asyncio.to_thread(lab_service.validate, req.symbol,
                                      req.timeframe, spec, req.bars, req.paramKey,
                                      req.sizeToCapital)
    except (DataUnavailable, ValueError) as e:
        raise HTTPException(status_code=422, detail=str(e))
    if req.save:
        out["runId"] = await asyncio.to_thread(
            lab_store.save, out["full"], req.strategyParameters,
            req.label, out["verdict"])
    return out


@app.websocket("/ws/lab")
async def lab_matrix_stream(ws: WebSocket) -> None:
    """Matrix results, one cell at a time, over the channel the panel already has.

    Streaming rather than blocking removes the question of what to do about a
    long job: a 30-cell matrix looks instant, a 300-cell one fills visibly and
    can be read while it runs. There is no queue and no "is it done yet".
    """
    await ws.accept()
    try:
        while True:
            job = await ws.receive_json()
            try:
                req = LabMatrixRequest(**job)
            except Exception as e:                      # noqa: BLE001
                await ws.send_json({"type": "error", "detail": f"Bad request: {e}"})
                continue

            symbols = req.symbols or [req.symbol]
            timeframes = req.timeframes or [req.timeframe]
            try:
                sized = lab_service.plan(symbols, timeframes, req.bars,
                                         req.sweepKey, req.sweepValues,
                                         req.strategies)
            except lab_service.TooManyCells as e:
                await ws.send_json({"type": "refused", "detail": str(e)})
                continue

            await ws.send_json({"type": "start", **sized})
            loop = asyncio.get_running_loop()
            queue: asyncio.Queue = asyncio.Queue()

            def emit(row: Dict[str, Any]) -> None:
                loop.call_soon_threadsafe(queue.put_nowait, row)

            def work() -> None:
                try:
                    lab_service.matrix(symbols, timeframes, _lab_spec(req), req.bars,
                                       req.sweepKey, req.sweepValues, emit,
                                       req.strategies, req.sizeToCapital)
                    loop.call_soon_threadsafe(queue.put_nowait, {"_done": True})
                except Exception as e:                  # noqa: BLE001
                    loop.call_soon_threadsafe(
                        queue.put_nowait, {"_done": True, "_error": str(e)})

            task = asyncio.create_task(asyncio.to_thread(work))
            saved = 0
            while True:
                row = await queue.get()
                if row.get("_done"):
                    await ws.send_json({"type": "done", "saved": saved,
                                        "error": row.get("_error")})
                    break
                if req.save and "error" not in row:
                    params = (dict(lab_service.strategy_defaults().get(row["strategy"], {}))
                              if req.strategies else dict(req.strategyParameters or {}))
                    if row.get("paramKey") is not None and row.get("paramValue") is not None:
                        params[row["paramKey"]] = row["paramValue"]
                    if await asyncio.to_thread(lab_store.save, row, params,
                                               req.label, None) is not None:
                        saved += 1
                await ws.send_json({"type": "cell", "cell": row})
            await task
    except WebSocketDisconnect:
        return
    except Exception as e:                              # noqa: BLE001
        log.warning("lab stream ended: %s", e)


class LabPredictRequest(LabRunRequest):
    """Which instruments to read a stance for. Blank means every one listed."""

    symbols: List[str] = []
    # The timeframe is part of what gets chosen, not fixed in advance. Blank
    # means every timeframe the lab knows.
    timeframes: List[str] = []
    # Read only what the backtests already downloaded. Off, a missing series is
    # fetched, which is slow and is not what "use the backtest data" means.
    cachedOnly: bool = True


@app.websocket("/ws/lab/predict")
async def lab_predict_stream(ws: WebSocket) -> None:
    """One instrument at a time: score every strategy, then read the winner's stance.

    Streamed for the same reason the matrix is — twenty-eight instruments times
    fourteen strategies is a real wait, and a table that fills in can be read
    while it works.
    """
    await ws.accept()
    try:
        while True:
            job = await ws.receive_json()
            try:
                req = LabPredictRequest(**job)
            except Exception as e:                          # noqa: BLE001
                await ws.send_json({"type": "error", "detail": f"Bad request: {e}"})
                continue

            symbols = req.symbols or [i["symbol"] for i in list_instruments()]
            base = {k: v for k, v in _lab_spec(req).items()
                    if k not in ("strategy", "strategy_parameters")}
            timeframes = req.timeframes or list(lab_windows.TIMEFRAMES)
            await ws.send_json({"type": "start", "symbols": len(symbols),
                                "timeframes": len(timeframes), "bars": req.bars,
                                "cachedOnly": req.cachedOnly})

            loop = asyncio.get_running_loop()
            queue: asyncio.Queue = asyncio.Queue()

            def emit(row: Dict[str, Any]) -> None:
                loop.call_soon_threadsafe(queue.put_nowait, row)

            def work() -> None:
                try:
                    lab_predict.run(symbols, timeframes, req.bars, base, emit,
                                    req.cachedOnly)
                    loop.call_soon_threadsafe(queue.put_nowait, {"_done": True})
                except Exception as e:                      # noqa: BLE001
                    loop.call_soon_threadsafe(
                        queue.put_nowait, {"_done": True, "_error": str(e)})

            task = asyncio.create_task(asyncio.to_thread(work))
            while True:
                row = await queue.get()
                if row.get("_done"):
                    await ws.send_json({"type": "done", "error": row.get("_error")})
                    break
                await ws.send_json({"type": "row", "row": row})
            await task
    except WebSocketDisconnect:
        return
    except Exception as e:                                  # noqa: BLE001
        log.warning("predict stream ended: %s", e)


@app.get("/api/lab/runs")
async def lab_runs(symbol: str = "", timeframe: str = "", strategy: str = "",
                   verdict: str = "", beats: Optional[bool] = None,
                   sort: str = "created_at", desc: bool = True,
                   limit: int = 200) -> Dict[str, Any]:
    """The library. Unavailable is a state the caller renders, not an error."""
    rows = await asyncio.to_thread(lab_store.search, symbol, timeframe, strategy,
                                   verdict, beats, sort, desc, limit)
    return {"library": lab_store.status(), "count": len(rows), "runs": rows}


@app.get("/api/lab/runs.csv")
async def lab_runs_csv(symbol: str = "", timeframe: str = "", strategy: str = "",
                       verdict: str = "", beats: Optional[bool] = None,
                       sort: str = "created_at", desc: bool = True,
                       limit: int = 2000) -> PlainTextResponse:
    """The same rows as a spreadsheet, for the comparison the tool does not do."""
    rows = await asyncio.to_thread(lab_store.search, symbol, timeframe, strategy,
                                   verdict, beats, sort, desc, limit)
    columns = ["id", "createdAt", "label", "symbol", "timeframe", "strategy",
               "bars", "usableBars", "trades", "winRate", "net", "bh", "vsBH",
               "beatsBH", "pf", "maxDDPct", "sharpe", "topTradeShare", "verdict"]
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(columns)
    for row in rows:
        writer.writerow([row.get(c) for c in columns])
    return PlainTextResponse(
        buf.getvalue(), media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="backtest-runs.csv"'})


@app.get("/api/lab/runs/{run_id}")
async def lab_run_detail(run_id: int) -> Dict[str, Any]:
    row = await asyncio.to_thread(lab_store.get, run_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"No run {run_id} in the library.")
    return row


@app.delete("/api/lab/runs/{run_id}")
async def lab_run_delete(run_id: int) -> Dict[str, Any]:
    return {"deleted": await asyncio.to_thread(lab_store.delete, run_id)}


class RiskDefaultsBody(BaseModel):
    """Edits to the risk defaults: {root: {timeframe: multiple or null}}."""

    entries: Dict[str, Dict[str, Optional[float]]] = {}


@app.get("/api/backtest/defaults")
async def backtest_defaults() -> Dict[str, Any]:
    """Measured risk defaults, the user's overrides, and the two merged.

    The panel fills its trailing-stop field from `effective`, so a run starts
    from a setting that was actually measured for that symbol and timeframe
    rather than from whatever was last typed.
    """
    return risk_defaults.all_entries()


@app.put("/api/backtest/defaults")
async def save_backtest_defaults(body: RiskDefaultsBody) -> Dict[str, Any]:
    """Save edited defaults. Overrides win over the measured table."""
    try:
        return risk_defaults.save_overrides(body.entries)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/backtest/defaults/reset")
async def reset_backtest_defaults() -> Dict[str, Any]:
    """Drop every override and go back to the measured values."""
    return risk_defaults.reset_overrides()


@app.get("/api/game/candles")
async def game_candles(timeframe: str = "1d", symbol: str = "/NQ",
                       refresh: bool = False) -> Dict[str, Any]:
    """Real historical OHLCV for the replay simulator, one timeframe at a time.

    The game holds an entire series in memory and steps through it, so it asks
    for the whole thing once per timeframe rather than a request per bar. The
    fetch is cached on disk; a timeframe Yahoo does not publish (10m) is
    aggregated from real bars of a lower one, never synthesized.
    """
    try:
        series = await asyncio.to_thread(
            load_replay_series, symbol, timeframe, None, refresh
        )
    except DataUnavailable as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    payload = series.as_arrays()
    payload["label"] = REPLAY_TIMEFRAMES[timeframe]["label"]
    payload["unit"] = resolve_instrument(symbol).contract_multiplier
    payload["timeframes"] = [
        {"key": k, "label": v["label"]} for k, v in REPLAY_TIMEFRAMES.items()
    ]
    return payload


# ---------------------------------------------------------------------------
# Terminal-compatible routes
#
# The terminal was written against the MT5 bridge and calls these unprefixed
# paths. Keeping the shapes identical means the same UI drives any broker —
# only the `broker` query parameter changes.
# ---------------------------------------------------------------------------

@app.get("/api/health")
async def health(broker: str = "mt5") -> Dict:
    b = get_broker(broker)
    try:
        d = b.gateway.describe()
        return {
            "status": "connected" if d.connected else "disconnected",
            "broker": broker,
            "detail": d.detail,
        }
    except Exception as e:
        return {"status": "disconnected", "broker": broker, "detail": str(e)}


@app.get("/api/ohlcv/{symbol}")
async def terminal_ohlcv(symbol: str, broker: str = "mt5", timeframe: str = "M15", count: int = 200):
    return await ohlcv(broker, symbol, timeframe, count)


@app.get("/api/history")
async def terminal_history(broker: str = "mt5", count: int = 50):
    return await history(broker, count)


@app.get("/api/instruments")
async def terminal_instruments(broker: str = "mt5", q: str = "", limit: int = 40) -> List[Dict]:
    b = get_broker(broker)
    finder = getattr(b.gateway, "search_symbols", None)
    return finder(q, limit) if callable(finder) else []


class OptionOrder(BaseModel):
    option_symbol: str
    quantity: int = 1
    side: str = "buy"
    limit_price: float = 0.0


class OptionQuoteRequest(BaseModel):
    # Feed names, not order symbols — the chain returns both.
    symbols: List[str] = []


@app.get("/api/{broker_key}/options/{underlying}")
async def option_chain(broker_key: str, underlying: str, expiry: str = "") -> Dict:
    """Expiry list for an underlying, or one expiry's strikes when given."""
    broker = get_broker(broker_key)
    getter = getattr(broker.gateway, "get_option_chain", None)
    if not callable(getter):
        raise HTTPException(status_code=501, detail=f"{broker_key} does not support options")
    return getter(underlying, expiry or None)


class BacktestRequest(BaseModel):
    """Everything the user chose in the panel. No field carries a trading default."""

    symbol: str
    timeframe: str
    startDate: str
    endDate: str
    dataSource: str = "yahoo"
    strategy: str = "MTF_GREEN_STACK"
    strategyParameters: Dict[str, Any] = {}
    assetType: Optional[str] = None

    initialCapital: float = 100_000.0
    positionSize: float = 1.0
    commission: float = 0.0
    slippageTicks: float = 0.0

    stopLossAtr: Optional[float] = None
    takeProfitAtr: Optional[float] = None
    trailingStopAtr: Optional[float] = None
    trailingStopPct: Optional[float] = None
    breakevenAtAtr: Optional[float] = None
    trailActivateAtr: Optional[float] = None
    riskPerTradePct: Optional[float] = None
    stopLossPct: Optional[float] = None
    takeProfitPct: Optional[float] = None

    allowLong: bool = True
    allowShort: bool = True
    executionModel: str = "next_open"
    includeSignals: bool = False

    # Options mode. Premiums are modelled, never historical — the result's
    # warnings say so on every run.
    tradeMode: str = "underlying"
    optionOnSell: str = "buy_put"
    optionOnBuy: str = "buy_call"
    optionExpiryRule: str = "mwf"
    optionMinDaysToExpiry: float = 0.0
    optionImpliedVol: Optional[float] = None
    optionVolLookback: int = 20
    optionVolPremium: float = 1.0
    optionRiskFreeRate: float = 0.0
    optionTakeProfitPct: Optional[float] = None
    optionStopLossPct: Optional[float] = None
    optionHoldToExpiry: bool = False
    optionMinPremium: float = 0.05
    # Also run the same signals in the other mode, so the panel can chart the
    # underlying and the options version of one strategy side by side.
    compareOptions: bool = False


def _to_config(req: BacktestRequest) -> BacktestConfig:
    """Request -> engine configuration, with dates parsed once, here."""
    try:
        start = date.fromisoformat(req.startDate)
        end = date.fromisoformat(req.endDate)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=f"Dates must be YYYY-MM-DD: {e}")

    try:
        model = ExecutionModel(req.executionModel)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown execution model '{req.executionModel}'. "
                   f"Use one of: {', '.join(m.value for m in ExecutionModel)}.",
        )

    def _enum(cls, value: str, label: str):
        try:
            return cls(value)
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail=f"Unknown {label} '{value}'. "
                       f"Use one of: {', '.join(m.value for m in cls)}.",
            )

    return BacktestConfig(
        symbol=req.symbol, timeframe=req.timeframe, start=start, end=end,
        data_source=req.dataSource, strategy=req.strategy,
        strategy_parameters=req.strategyParameters or {},
        asset_type=req.assetType,
        initial_capital=req.initialCapital, position_size=req.positionSize,
        commission=req.commission, slippage_ticks=req.slippageTicks,
        stop_loss_atr=req.stopLossAtr, take_profit_atr=req.takeProfitAtr,
        trailing_stop_atr=req.trailingStopAtr,
        trailing_stop_pct=req.trailingStopPct,
        breakeven_at_atr=req.breakevenAtAtr,
        trail_activate_atr=req.trailActivateAtr,
        risk_per_trade_pct=req.riskPerTradePct,
        stop_loss_pct=req.stopLossPct, take_profit_pct=req.takeProfitPct,
        allow_long=req.allowLong, allow_short=req.allowShort,
        execution_model=model,
        trade_mode=_enum(TradeMode, req.tradeMode, "trade mode"),
        option_on_sell=_enum(OptionAction, req.optionOnSell, "sell-signal action"),
        option_on_buy=_enum(OptionAction, req.optionOnBuy, "buy-signal action"),
        option_expiry_rule=req.optionExpiryRule,
        option_min_days_to_expiry=req.optionMinDaysToExpiry,
        option_implied_vol=req.optionImpliedVol,
        option_vol_lookback=req.optionVolLookback,
        option_vol_premium=req.optionVolPremium,
        option_risk_free_rate=req.optionRiskFreeRate,
        option_take_profit_pct=req.optionTakeProfitPct,
        option_stop_loss_pct=req.optionStopLossPct,
        option_hold_to_expiry=req.optionHoldToExpiry,
        option_min_premium=req.optionMinPremium,
    )


@app.get("/api/backtest/config")
async def backtest_config() -> Dict:
    """What the panel may offer: providers, timeframes, strategies, instruments.

    Driven off the registries so a new provider or strategy appears in the UI
    without the UI being edited.
    """
    return {
        "providers": [
            {
                "key": c.key,
                "label": c.label,
                "timeframes": list(c.timeframes),
                "supportsHistory": c.supports_history,
                "maxIntradayDays": c.max_intraday_days,
                "note": c.note,
            }
            for c in list_providers()
        ],
        "timeframes": list(TIMEFRAMES),
        "strategies": list_strategies(),
        "instruments": list_instruments(),
        "executionModels": [
            {"key": ExecutionModel.NEXT_OPEN.value,
             "label": "Next bar open (no look-ahead)"},
            {"key": ExecutionModel.SAME_CLOSE.value,
             "label": "Signal bar close (optimistic)"},
        ],
        "tradeModes": [
            {"key": TradeMode.UNDERLYING.value, "label": "Underlying"},
            {"key": TradeMode.OPTIONS.value, "label": "Options (modelled)"},
        ],
        "optionActions": [
            {"key": OptionAction.BUY_CALL.value, "label": "Buy ATM call"},
            {"key": OptionAction.BUY_PUT.value, "label": "Buy ATM put"},
            {"key": OptionAction.SELL_CALL.value, "label": "Sell ATM call"},
            {"key": OptionAction.SELL_PUT.value, "label": "Sell ATM put"},
            {"key": OptionAction.NONE.value, "label": "Ignore this signal"},
        ],
        "expiryRules": [
            {"key": ExpiryRule.MWF.value, "label": "Mon/Wed/Fri weeklies (/NQ)"},
            {"key": ExpiryRule.DAILY.value, "label": "Every weekday"},
            {"key": ExpiryRule.FRIDAY.value, "label": "Friday weeklies"},
            {"key": ExpiryRule.MONTHLY.value, "label": "Monthly (3rd Friday)"},
        ],
        "optionsNote": (
            "Option premiums are modelled with Black-76 from the underlying's price "
            "and its realized volatility — no connected data source has historical "
            "futures-option prices. Treat the P&L as an estimate."
        ),
    }


@app.post("/api/backtest")
async def backtest(req: BacktestRequest) -> Dict:
    """Run one backtest over the user's exact configuration.

    Blocking work — data fetch plus a walk over every bar — so it runs in a
    worker thread rather than stalling the event loop the panel's websocket
    and quote polling share.
    """
    config = _to_config(req)

    def _run() -> Dict:
        from dataclasses import replace

        from ftmo_mt5.marketdata import get_provider

        # Fetch once and reuse. Both runs must see identical bars anyway —
        # comparing them across two downloads would put a data difference
        # inside a comparison meant to isolate the difference in method.
        series = get_provider(config.data_source).get_history(
            config.symbol, config.timeframe, config.start, config.end)

        payload = run_backtest(config, series).as_dict()

        # The same signals, taken in the options market instead. Returned
        # alongside rather than instead, so the panel can chart both.
        if req.compareOptions:
            other = (TradeMode.UNDERLYING if config.trade_mode is TradeMode.OPTIONS
                     else TradeMode.OPTIONS)
            try:
                payload["comparison"] = run_backtest(
                    replace(config, trade_mode=other), series).as_dict()
            except Exception as e:
                log.warning("comparison run failed: %s", e)
                payload["comparisonError"] = str(e)

        return payload

    try:
        return await asyncio.to_thread(_run)
    except DataUnavailable as e:
        # The user asked for something the provider cannot serve. That is a
        # request problem with a fixable cause, not a server failure.
        raise HTTPException(status_code=422, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        log.exception("backtest failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/signals")
async def signals(req: BacktestRequest) -> Dict:
    """Signals only — same configuration, no trade simulation."""
    config = _to_config(req)

    def _run() -> Dict:
        from ftmo_mt5.marketdata import get_provider

        provider = get_provider(config.data_source)
        series = provider.get_history(config.symbol, config.timeframe,
                                      config.start, config.end)
        engine = SignalEngine(StrategyConfig(config.strategy, config.strategy_parameters))
        found = engine.run(series)
        return {
            "symbol": series.symbol,
            "timeframe": series.timeframe,
            "provider": series.provider,
            "bars": len(series),
            "count": len(found),
            "signals": [s.as_dict() for s in found],
        }

    try:
        return await asyncio.to_thread(_run)
    except DataUnavailable as e:
        raise HTTPException(status_code=422, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@app.post("/api/{broker_key}/option-quotes")
async def option_quotes(broker_key: str, req: OptionQuoteRequest) -> Dict[str, Dict]:
    """Bid/ask for the strikes the panel is showing.

    A POST because a visible chain is a couple of hundred contracts and that
    does not belong in a query string.
    """
    broker = get_broker(broker_key)
    getter = getattr(broker.gateway, "get_option_quotes", None)
    if not callable(getter) or not req.symbols:
        return {}
    return getter(req.symbols)


@app.post("/api/{broker_key}/option-order")
async def place_option_order(broker_key: str, order: OptionOrder) -> Dict:
    broker = get_broker(broker_key)
    sender = getattr(broker.gateway, "order_option", None)
    if not callable(sender):
        raise HTTPException(status_code=501, detail=f"{broker_key} does not support options")

    result = sender(order.option_symbol, order.quantity, order.side, order.limit_price)
    return {
        "broker": broker_key,
        "success": result.get("retcode") == 10000,
        "order": result.get("order"),
        "comment": result.get("comment", ""),
    }


@app.get("/api/brokers")
async def list_brokers() -> List[Dict]:
    """Status of every configured broker — the terminal's heartbeat."""
    out = []
    for key, broker in BROKERS.items():
        try:
            d = broker.gateway.describe()
            out.append({
                "key": d.key,
                "label": d.label,
                "connected": d.connected,
                "detail": d.detail or broker.last_error,
                "symbols": watchlist_for(broker.gateway),
            })
        except Exception as e:
            out.append({
                "key": key,
                "label": key.upper(),
                "connected": False,
                "detail": f"describe failed: {e}",
                "symbols": watchlist_for(broker.gateway),
            })
    return out


@app.post("/api/{broker_key}/connect")
async def connect_broker(broker_key: str) -> Dict:
    broker = get_broker(broker_key)
    ok = broker.connect()
    return {"connected": ok, "detail": broker.last_error or "connected"}


@app.get("/api/{broker_key}/account")
async def account(broker_key: str) -> Dict:
    broker = get_broker(broker_key)
    try:
        a = broker.gateway.get_account_info()
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))
    return {
        "broker": broker_key,
        "balance": _num(a.balance),
        "equity": _num(a.equity),
        "margin": _num(a.margin),
        "free_margin": _num(a.free_margin),
    }


@app.get("/api/{broker_key}/positions")
async def positions(broker_key: str) -> List[Dict]:
    broker = get_broker(broker_key)
    try:
        raw = broker.gateway.get_positions()
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))
    return [
        {
            "broker": broker_key,
            "ticket": p.ticket,
            "symbol": p.symbol,
            "type": p.type,
            "volume": _num(p.volume),
            "open_price": _num(p.price_open),
            "profit": _num(p.profit),
        }
        for p in raw
    ]


@app.get("/api/{broker_key}/quotes")
async def quotes(broker_key: str) -> Dict[str, Optional[Dict]]:
    """Watchlist quotes from one broker, over the symbols it actually lists."""
    broker = get_broker(broker_key)
    symbols = watchlist_for(broker.gateway)

    # Streaming brokers can answer the whole list on one connection; quoting
    # them one at a time turns a panel refresh into a minute of waiting.
    batch = getattr(broker.gateway, "get_quotes", None)
    if callable(batch):
        try:
            ticks = batch(symbols)
        except Exception:
            ticks = {}
    else:
        ticks = {}
        for symbol in symbols:
            try:
                ticks[symbol] = broker.gateway.get_tick(symbol)
            except Exception:
                ticks[symbol] = None

    return {
        s: ({"bid": _num(t["bid"]), "ask": _num(t["ask"])} if (t := ticks.get(s)) else None)
        for s in symbols
    }


@app.get("/api/search")
async def search(q: str = "", limit: int = 20) -> List[Dict]:
    """Symbol search across every connected broker at once.

    Results carry their broker, so clicking one knows where to send the order —
    the same ticker can exist on more than one venue.
    """
    results: List[Dict] = []
    for key, broker in BROKERS.items():
        if not broker.gateway.health_check():
            continue
        finder = getattr(broker.gateway, "search_symbols", None)
        if not callable(finder):
            continue
        try:
            for row in finder(q, limit):
                results.append({**row, "broker": row.get("broker", key)})
        except Exception as e:
            log.warning("search failed on %s: %s", key, e)
    return results[: limit * len(BROKERS)]


@app.get("/api/{broker_key}/ohlcv/{symbol}")
async def ohlcv(broker_key: str, symbol: str, timeframe: str = "M15", count: int = 120) -> List[Dict]:
    """Candles for one symbol, in the chart's own shape."""
    broker = get_broker(broker_key)
    try:
        bars = broker.gateway.get_ohlcv(symbol, timeframe=timeframe, count=count)
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))
    return [
        {
            "time": b["time"],
            "open": _num(b["open"]),
            "high": _num(b["high"]),
            "low": _num(b["low"]),
            "close": _num(b["close"]),
            "volume": b.get("volume", 0),
        }
        for b in bars
    ]


@app.get("/api/{broker_key}/history")
async def history(broker_key: str, count: int = 50) -> List[Dict]:
    """Closed deals / transactions for one broker."""
    broker = get_broker(broker_key)
    getter = getattr(broker.gateway, "get_history", None)
    if not callable(getter):
        return []
    try:
        return getter(count)
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))


@app.post("/api/{broker_key}/order")
async def place_order(broker_key: str, order: OrderRequest) -> Dict:
    broker = get_broker(broker_key)
    # A leading slash marks a futures contract on tastytrade ("/NQZ6"). Those
    # are their own instrument class — routing one through the equity path just
    # fails to resolve the symbol.
    sender = getattr(broker.gateway, "order_future", None)
    if order.symbol.startswith("/") and callable(sender):
        result = sender(order.symbol, int(order.volume), order.side)
    else:
        result = broker.gateway.order_send({
            "symbol": order.symbol,
            "type": order.side,
            "action": order.side,
            "volume": order.volume,
            "sl": order.sl,
            "tp": order.tp,
        })
    retcode = result.get("retcode")
    return {
        "broker": broker_key,
        "success": retcode == 10000,
        "retcode": retcode,
        "order": result.get("order"),
        "price": _num(result.get("price", 0)),
        "comment": result.get("comment", ""),
    }


@app.post("/api/{broker_key}/close/{ticket}")
async def close(broker_key: str, ticket: int) -> Dict:
    broker = get_broker(broker_key)
    return broker.gateway.close_position(ticket)


@app.get("/api/portfolio")
async def portfolio() -> Dict:
    """Everything, everywhere — combined equity and positions across brokers.

    A broker that is unreachable contributes an `error` entry instead of
    breaking the whole view.
    """
    accounts, all_positions = [], []
    total_equity = 0.0

    for key, broker in BROKERS.items():
        if not broker.gateway.health_check():
            accounts.append({"broker": key, "error": broker.last_error or "not connected"})
            continue
        try:
            a = broker.gateway.get_account_info()
            accounts.append({
                "broker": key,
                "balance": _num(a.balance),
                "equity": _num(a.equity),
                "free_margin": _num(a.free_margin),
            })
            total_equity += float(a.equity)
            for p in broker.gateway.get_positions():
                all_positions.append({
                    "broker": key,
                    "ticket": p.ticket,
                    "symbol": p.symbol,
                    "type": p.type,
                    "volume": _num(p.volume),
                    "open_price": _num(p.price_open),
                    "profit": _num(p.profit),
                })
        except Exception as e:
            accounts.append({"broker": key, "error": str(e)})

    return {
        "total_equity": round(total_equity, 2),
        "open_positions": len(all_positions),
        "accounts": accounts,
        "positions": all_positions,
    }


# ---------------------------------------------------------------------------
# Live stream
# ---------------------------------------------------------------------------

@app.websocket("/ws/prices")
async def ws_prices(websocket: WebSocket) -> None:
    """Stream ticks, account and positions for one broker; accept its orders.

    Same message shape the MT5 bridge used, so the terminal did not have to be
    rewritten — it just names the broker it wants in the query string.
    """
    await websocket.accept()
    broker_key = websocket.query_params.get("broker", "mt5")

    try:
        broker = BROKERS[broker_key]
    except KeyError:
        await websocket.send_json({"type": "error", "message": f"unknown broker '{broker_key}'"})
        await websocket.close()
        return

    # Mutable per connection: the terminal can ask for more symbols than the
    # default watchlist. Without that, selecting anything outside the default
    # six left the order ticket showing the previous symbol's prices.
    symbols = list(watchlist_for(broker.gateway))
    log.info("terminal stream opened: %s %s", broker_key, symbols)

    async def snapshot() -> Dict:
        gw = broker.gateway
        # Gateway calls are blocking HTTP/socket work; off-loading them keeps
        # this coroutine free to read the client's orders while they run.
        quotes = await asyncio.to_thread(
            gw.get_quotes, symbols
        ) if hasattr(gw, "get_quotes") else {}
        account = await asyncio.to_thread(gw.get_account_info)
        positions = await asyncio.to_thread(gw.get_positions)

        ticks = {
            s: {
                "symbol": s,
                "bid": _num(q["bid"]),
                "ask": _num(q["ask"]),
                "last": _num(q.get("last", q["bid"])),
                "time": q.get("time", 0),
            }
            for s, q in (quotes or {}).items() if q
        }
        return {
            "type": "tick",
            "broker": broker_key,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "ticks": ticks,
            "account": {
                "login": getattr(gw, "account_number", broker_key),
                "balance": _num(account.balance),
                "equity": _num(account.equity),
                "margin": _num(account.margin),
                "free_margin": _num(account.free_margin),
                "profit": _num(account.equity - account.balance),
                "currency": "USD",
                "initial_capital": _num(account.initial_capital),
            },
            "positions": [
                {
                    "ticket": p.ticket,
                    "symbol": p.symbol,
                    "type": "buy" if p.type == "LONG" else "sell",
                    "volume": _num(p.volume),
                    "open_price": _num(p.price_open),
                    "current_price": _num(p.price_open),
                    "sl": _num(p.sl) if p.sl else 0,
                    "tp": _num(p.tp) if p.tp else 0,
                    "pnl": _num(p.profit),
                    "swap": 0,
                    "magic": p.magic,
                    "comment": p.comment,
                }
                for p in positions
            ],
        }

    async def pump() -> None:
        """Push a snapshot on a fixed cadence."""
        while True:
            try:
                await websocket.send_json(await snapshot())
            except Exception as e:
                log.warning("stream snapshot failed (%s): %s", broker_key, e)
                await websocket.send_json({"type": "error", "message": str(e)})
            await asyncio.sleep(STREAM_INTERVAL_S)

    async def listen() -> None:
        """Handle commands coming back from the terminal."""
        while True:
            cmd = json.loads(await websocket.receive_text())
            action = cmd.get("action")

            if action == "subscribe":
                for sym in cmd.get("symbols", []):
                    if sym and sym not in symbols:
                        symbols.append(sym)
                        log.info("stream +%s (%s)", sym, broker_key)
                continue

            if action == "order":
                symbol = str(cmd.get("symbol", ""))
                fut = getattr(broker.gateway, "order_future", None)
                if symbol.startswith("/") and callable(fut):
                    result = await asyncio.to_thread(
                        fut, symbol, int(cmd.get("volume", 0) or 0),
                        cmd.get("side", cmd.get("type", "buy")),
                    )
                    await websocket.send_json({
                        "type": "order_result",
                        "success": result.get("retcode") == 10000,
                        "ticket": result.get("order"),
                        "symbol": symbol,
                        "volume": _num(result.get("volume", cmd.get("volume", 0))),
                        "price": _num(result.get("price", 0)),
                        "error": None if result.get("retcode") == 10000 else result.get("comment"),
                        "retcode": result.get("retcode"),
                    })
                    continue
                result = await asyncio.to_thread(broker.gateway.order_send, {
                    "symbol": cmd.get("symbol", ""),
                    "type": cmd.get("side", cmd.get("type", "buy")),
                    "action": cmd.get("side", "buy"),
                    "volume": cmd.get("volume", 0),
                    "sl": cmd.get("sl", 0),
                    "tp": cmd.get("tp", 0),
                    "price": cmd.get("price", 0),
                })
                await websocket.send_json({
                    "type": "order_result",
                    "success": result.get("retcode") == 10000,
                    "ticket": result.get("order"),
                    "symbol": cmd.get("symbol", ""),
                    "volume": _num(result.get("volume", cmd.get("volume", 0))),
                    "price": _num(result.get("price", 0)),
                    "error": None if result.get("retcode") == 10000 else result.get("comment"),
                    "retcode": result.get("retcode"),
                })

            elif action == "close":
                result = await asyncio.to_thread(
                    broker.gateway.close_position, int(cmd.get("ticket", 0))
                )
                await websocket.send_json({
                    "type": "close_result",
                    "success": result.get("success", False),
                    "ticket": cmd.get("ticket"),
                    "error": result.get("error"),
                })

            elif action == "ohlcv":
                bars = await asyncio.to_thread(
                    broker.gateway.get_ohlcv,
                    cmd.get("symbol", ""), cmd.get("timeframe", "M15"), 200,
                )
                await websocket.send_json({
                    "type": "ohlcv",
                    "symbol": cmd.get("symbol"),
                    "data": [
                        {
                            "time": b["time"], "open": _num(b["open"]), "high": _num(b["high"]),
                            "low": _num(b["low"]), "close": _num(b["close"]), "volume": b.get("volume", 0),
                        }
                        for b in bars
                    ],
                })

    try:
        await asyncio.gather(pump(), listen())
    except WebSocketDisconnect:
        log.info("terminal stream closed: %s", broker_key)
    except Exception as e:
        log.warning("terminal stream error (%s): %s", broker_key, e)


# ---------------------------------------------------------------------------
# Claude agent — free-form Claude Code tasks, run from the hub and streamed
# back as they happen.
#
# Two things this page is not: it is not a chat box (every run is a fresh
# Claude Code session with real tools, not a conversation), and it is not
# reachable from anywhere but this machine — main() binds 127.0.0.1, and the
# tools below can edit files and run shell commands under whoever started the
# hub. Keep that binding.
#
# `--chrome` rides the same code path rather than shelling out separately:
# ClaudeAgentOptions.extra_args passes bare CLI flags through, so browser
# tasks differ from ordinary ones by one dict entry.
# ---------------------------------------------------------------------------

# Imported lazily and defensively. The SDK is an optional dependency, and a
# hub that refuses to boot because one page's package is missing would take
# the terminal, the game and the lab down with it.
try:
    from claude_agent_sdk import (                     # noqa: F401
        AssistantMessage, ClaudeAgentOptions, ClaudeSDKError, CLIConnectionError,
        CLINotFoundError, ProcessError, ResultMessage, TextBlock, ThinkingBlock,
        ToolUseBlock, query as claude_query,
    )
    AGENT_SDK_ERROR: Optional[str] = None
except ImportError as e:                               # noqa: BLE001
    AGENT_SDK_ERROR = f"{e}. Install with: venv/bin/pip install claude-agent-sdk"
    claude_query = None                                # type: ignore[assignment]


class AgentRunRequest(BaseModel):
    """One task. Everything has a default so the page can post just a prompt."""

    prompt: str
    # Where Claude runs. The repo root, unless a task needs somewhere else.
    cwd: str = str(Path(__file__).parent)
    model: Optional[str] = None
    # acceptEdits keeps file writes flowing without a prompt nobody can answer;
    # bypassPermissions is the fully-open setting and stays a deliberate choice
    # in the UI rather than the default here.
    permissionMode: str = "acceptEdits"
    chrome: bool = False
    maxTurns: Optional[int] = None
    # A hard ceiling in dollars. A free-form prompt box with real tools is
    # exactly where a runaway loop costs money, so the cap is always set.
    maxBudgetUsd: float = 2.0


def _agent_options(req: "AgentRunRequest") -> "ClaudeAgentOptions":
    extra: Dict[str, Optional[str]] = {}
    if req.chrome:
        # Flag-only argument: the value is None, not "" — an empty string
        # would render as `--chrome=` and the CLI would reject it.
        extra["chrome"] = None
    return ClaudeAgentOptions(
        cwd=req.cwd,
        model=req.model or None,
        permission_mode=req.permissionMode,            # type: ignore[arg-type]
        max_turns=req.maxTurns,
        max_budget_usd=req.maxBudgetUsd,
        # Behave like a normal session in that directory: CLAUDE.md, project
        # settings and the .mcp.json ftmo-lab server all load, which is what
        # makes "run a backtest on /NQ" work without restating the setup.
        setting_sources=["user", "project", "local"],
        extra_args=extra,
    )


@app.get("/agents", response_class=HTMLResponse)
async def agents_page() -> HTMLResponse:
    """The agent runner, on its own page like the lab and the game."""
    if AGENT_FILE.exists():
        return HTMLResponse(AGENT_FILE.read_text(encoding="utf-8"), headers=NO_CACHE)
    return HTMLResponse("<h1>claude_agent.html not found</h1>", status_code=404)


@app.get("/api/agent/status")
async def agent_status() -> Dict[str, Any]:
    """Is this page usable at all? Answered before the first run, not during.

    The three ways it fails — SDK missing, CLI missing, not logged in — all
    look identical from a stalled progress bar, so they are reported here as
    themselves.
    """
    cli = shutil.which("claude")
    return {
        "sdkInstalled": AGENT_SDK_ERROR is None,
        "sdkError": AGENT_SDK_ERROR,
        "cliPath": cli,
        "ready": AGENT_SDK_ERROR is None and cli is not None,
        "defaultCwd": str(Path(__file__).parent),
        # Chrome needs an interactive `/login` and a running browser; neither
        # is knowable from here, so the page says so rather than guessing.
        "chromeNote": "Needs the Claude in Chrome extension, `claude` logged in "
                      "via /login on a paid plan, and Chrome already running.",
    }


async def _stream_agent(ws: WebSocket, req: "AgentRunRequest",
                        state: Dict[str, bool]) -> None:
    """Run one task, forwarding each message as its own frame.

    Mirrors /ws/lab: the client renders progress instead of waiting on a
    single response, so a task that takes four minutes is readable while it
    runs rather than silent until it finishes.

    `state["done"]` records whether a terminal frame already went out. A
    failing run reports itself twice — once as an error ResultMessage, then
    again as the ProcessError the SDK raises on the way out — and without
    this the page prints the same failure twice under two different labels.
    """
    await ws.send_json({"type": "start", "cwd": req.cwd, "chrome": req.chrome,
                        "permissionMode": req.permissionMode,
                        "budgetUsd": req.maxBudgetUsd})
    async for message in claude_query(prompt=req.prompt, options=_agent_options(req)):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    await ws.send_json({"type": "text", "text": block.text})
                elif isinstance(block, ThinkingBlock):
                    await ws.send_json({"type": "thinking", "text": block.thinking})
                elif isinstance(block, ToolUseBlock):
                    await ws.send_json({"type": "tool", "name": block.name,
                                        "input": _tool_preview(block.input)})
        elif isinstance(message, ResultMessage):
            state["done"] = True
            await ws.send_json({
                "type": "done",
                "isError": message.is_error,
                "result": message.result,
                "costUsd": message.total_cost_usd,
                "turns": message.num_turns,
                "durationMs": message.duration_ms,
                "session": message.session_id,
                "stopReason": message.stop_reason,
            })


async def _agent_fail(ws: WebSocket, state: Dict[str, bool], detail: str) -> None:
    """Report a failure only if the run hasn't already reported one.

    The SDK raises after having already sent an error result for the same
    failure; a second frame would say the same thing twice on the page.
    """
    if state.get("done"):
        return
    await ws.send_json({"type": "error", "detail": detail})


def _tool_preview(payload: Any, limit: int = 300) -> str:
    """Tool inputs go to a log line, not a data pane — long ones are cut."""
    try:
        text = json.dumps(payload, default=str)
    except Exception:                                  # noqa: BLE001
        text = str(payload)
    return text if len(text) <= limit else text[:limit] + "…"


@app.websocket("/ws/agent")
async def agent_stream(ws: WebSocket) -> None:
    """One socket, many tasks. Send a job object; send {"cancel":true} to stop.

    The cancel path is why the run is a task raced against the next receive
    rather than a plain `async for` here: without it the only way to stop a
    long agent run would be closing the tab.
    """
    await ws.accept()
    try:
        while True:
            job = await ws.receive_json()
            if job.get("cancel"):                      # nothing running yet
                continue
            if AGENT_SDK_ERROR is not None:
                await ws.send_json({"type": "error", "detail": AGENT_SDK_ERROR})
                continue
            try:
                req = AgentRunRequest(**job)
            except Exception as e:                     # noqa: BLE001
                await ws.send_json({"type": "error", "detail": f"Bad request: {e}"})
                continue
            if not req.prompt.strip():
                await ws.send_json({"type": "error", "detail": "Empty task."})
                continue

            state: Dict[str, bool] = {"done": False}
            run = asyncio.create_task(_stream_agent(ws, req, state))
            listen = asyncio.create_task(ws.receive_json())
            done, _ = await asyncio.wait({run, listen},
                                         return_when=asyncio.FIRST_COMPLETED)

            if listen in done:
                # A frame arrived mid-run. Only cancel is meaningful here;
                # a second job while one is running is ignored rather than
                # queued, so the page can't quietly stack up agent sessions.
                run.cancel()
                try:
                    await run
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
                await ws.send_json({"type": "cancelled"})
                continue

            listen.cancel()
            try:
                await run
            except CLINotFoundError as e:
                await _agent_fail(ws, state, f"Claude Code CLI not found: {e}")
            except ProcessError as e:                  # ResultError subclasses this
                await _agent_fail(ws, state,
                                  f"Agent process failed (exit {e.exit_code}): "
                                  f"{e.stderr or e}")
            except CLIConnectionError as e:
                await _agent_fail(ws, state, f"Connection failed: {e}")
            except ClaudeSDKError as e:
                await _agent_fail(ws, state, str(e))
    except WebSocketDisconnect:
        return
    except Exception as e:                             # noqa: BLE001
        log.warning("agent stream ended: %s", e)


@app.on_event("startup")
async def startup() -> None:
    log.info("=" * 58)
    log.info("  Broker Hub — MT5 + tastytrade")
    log.info("=" * 58)
    for key, broker in BROKERS.items():
        if not broker.autoconnect:
            log.info("  %-4s autoconnect disabled", key)
            continue
        log.info("  %-4s connecting…", key)
        if broker.connect():
            log.info("  %-4s CONNECTED — %s", key, broker.gateway.describe().detail)
        else:
            log.warning("  %-4s unavailable — %s", key, broker.last_error)
    log.info("  Terminal: http://localhost:%s", HUB_PORT)
    log.info("=" * 58)


def main() -> None:
    import uvicorn

    uvicorn.run("broker_hub:app", host="127.0.0.1", port=HUB_PORT, reload=False, log_level="info")


if __name__ == "__main__":
    main()
