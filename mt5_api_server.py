"""
MT5 API Bridge Server — connects the Trading Dashboard to MetaTrader 5.

Run on your Windows machine with MT5 terminal open:
    python mt5_api_server.py

Serves:
  - REST endpoints for account info, positions, orders, history
  - WebSocket for real-time price streaming
  - CORS enabled for local file:// and localhost origins

Requirements:
    pip install fastapi uvicorn websockets MetaTrader5 python-dotenv pydantic
"""

import asyncio
import json
import logging
import os
import sys
import threading
import time
from datetime import datetime, timezone
from decimal import Decimal, getcontext
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    import MetaTrader5 as mt5
    HAS_MT5 = True
except ImportError:
    HAS_MT5 = False
    print("[WARNING] MetaTrader5 package not installed. Install with: pip install MetaTrader5")

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
getcontext().prec = 10

MT5_LOGIN = int(os.getenv("MT5_LOGIN", "0"))
MT5_SERVER = os.getenv("MT5_SERVER", "")
MT5_PASSWORD = os.getenv("MT5_PASSWORD", "")
# Explicit terminal64.exe path — needed when several MT5 terminals are installed
# (e.g. the generic MetaQuotes build alongside the broker-branded one).
MT5_TERMINAL_PATH = os.getenv("MT5_TERMINAL_PATH", "")
API_PORT = int(os.getenv("API_PORT", "8500"))

WATCHED_SYMBOLS = [
    "EURUSD", "GBPUSD", "USDJPY", "XAUUSD", "US30",
    "AUDUSD", "USDCAD", "NZDUSD",
    "ETHUSD", "BTCUSD", "EURGBP", "USDCHF",
    "US100", "XAGUSD",
]

MAGIC_NUMBER = int(os.getenv("FTMO_MAGIC", "234000"))
# How far back the deal-history endpoint looks. A challenge account rarely
# needs more, and a short window keeps the MT5 call cheap.
HISTORY_LOOKBACK_DAYS = int(os.getenv("HISTORY_LOOKBACK_DAYS", "60"))
INITIAL_CAPITAL = float(os.getenv("FTMO_INITIAL_CAPITAL", "100000"))

# ---------------------------------------------------------------------------
# Mock data layer — runs when MetaTrader5 is not available (macOS/Linux)
# ---------------------------------------------------------------------------
import random
import math

_mock_base_prices = {
    "EURUSD": 1.08500, "GBPUSD": 1.27200, "USDJPY": 149.850,
    "XAUUSD": 2415.50, "US30": 39850.0, "AUDUSD": 0.66800,
    "USDCAD": 1.36500, "NZDUSD": 0.61200, "ETHUSD": 3250.0,
    "BTCUSD": 67500.0, "EURGBP": 0.85300, "USDCHF": 0.87600,
    "US100": 17850.0, "XAGUSD": 28.50,
}
_mock_digits = {
    "EURUSD": 5, "GBPUSD": 5, "USDJPY": 3, "XAUUSD": 2,
    "US30": 1, "AUDUSD": 5, "USDCAD": 5, "NZDUSD": 5,
    "ETHUSD": 2, "BTCUSD": 2, "EURGBP": 5, "USDCHF": 5,
    "US100": 1, "XAGUSD": 3,
}
_mock_start_time = time.time()
_mock_positions = [
    {
        "ticket": 100001, "symbol": "EURUSD", "type": "buy",
        "volume": 1.0, "open_price": 1.08350, "sl": 1.08100, "tp": 1.08800,
        "swap": -1.25, "magic": MAGIC_NUMBER, "comment": "FTMO_SIGNAL",
        "time_open": datetime.now(timezone.utc).isoformat(),
    },
    {
        "ticket": 100002, "symbol": "XAUUSD", "type": "sell",
        "volume": 0.5, "open_price": 2420.00, "sl": 2435.00, "tp": 2400.00,
        "swap": -0.80, "magic": MAGIC_NUMBER, "comment": "FTMO_SIGNAL",
        "time_open": datetime.now(timezone.utc).isoformat(),
    },
]
_mock_history = []
_mock_order_counter = 100010
_mock_equity_drift = 0.0


def _mock_price(symbol: str) -> float:
    base = _mock_base_prices.get(symbol, 1.0)
    elapsed = time.time() - _mock_start_time
    digits = _mock_digits.get(symbol, 5)
    point = 10 ** (-digits)
    noise = math.sin(elapsed * 0.05 + hash(symbol) % 100) * 30 * point
    noise += math.sin(elapsed * 0.2 + hash(symbol) % 50) * 10 * point
    noise += random.gauss(0, 5 * point)
    return round(base + noise, digits)


def _mock_spread(symbol: str) -> float:
    digits = _mock_digits.get(symbol, 5)
    point = 10 ** (-digits)
    if symbol in ("XAUUSD", "BTCUSD", "ETHUSD"):
        return round(random.uniform(15, 40) * point, digits)
    if symbol in ("US30", "US100"):
        return round(random.uniform(5, 15) * point, digits)
    return round(random.uniform(1, 5) * point, digits)


def _mock_account_info() -> Dict:
    global _mock_equity_drift
    _mock_equity_drift += random.gauss(0, 15)
    _mock_equity_drift = max(-3000, min(3000, _mock_equity_drift))
    balance = INITIAL_CAPITAL + 1250.0
    pnl = sum(_mock_position_pnl(p) for p in _mock_positions)
    equity = balance + pnl + _mock_equity_drift
    margin = sum(p["volume"] * 1000 for p in _mock_positions)
    return {
        "login": MT5_LOGIN or 88012345,
        "name": "FTMO Challenge",
        "server": MT5_SERVER or "FTMO-Demo",
        "balance": round(balance, 2),
        "equity": round(equity, 2),
        "margin": round(margin, 2),
        "free_margin": round(equity - margin, 2),
        "margin_level": round((equity / margin * 100) if margin > 0 else 0, 2),
        "profit": round(pnl, 2),
        "leverage": 100,
        "currency": "USD",
    }


def _mock_position_pnl(pos: Dict) -> float:
    price = _mock_price(pos["symbol"])
    diff = price - pos["open_price"]
    if pos["type"] == "sell":
        diff = -diff
    contract = 100000 if "USD" in pos["symbol"] or "EUR" in pos["symbol"] or "GBP" in pos["symbol"] else 1
    if pos["symbol"] in ("XAUUSD", "XAGUSD"):
        contract = 100
    elif pos["symbol"] in ("US30", "US100"):
        contract = 1
    elif pos["symbol"] in ("BTCUSD", "ETHUSD"):
        contract = 1
    return round(diff * pos["volume"] * contract, 2)


def _mock_positions_with_pnl() -> List[Dict]:
    result = []
    for p in _mock_positions:
        cp = _mock_price(p["symbol"])
        pnl = _mock_position_pnl(p)
        result.append({**p, "current_price": cp, "pnl": pnl})
    return result


def _mock_symbol_tick(symbol: str) -> Optional[Dict]:
    bid = _mock_price(symbol)
    spread = _mock_spread(symbol)
    return {
        "symbol": symbol,
        "bid": bid,
        "ask": round(bid + spread, _mock_digits.get(symbol, 5)),
        "last": bid,
        "volume": random.randint(50, 500),
        "time": int(time.time()),
    }


def _mock_symbol_info(symbol: str) -> Optional[Dict]:
    digits = _mock_digits.get(symbol, 5)
    return {
        "symbol": symbol,
        "digits": digits,
        "point": 10 ** (-digits),
        "trade_contract_size": 100000 if digits == 5 else (100 if "XA" in symbol else 1),
        "volume_min": 0.01,
        "volume_max": 100.0,
        "volume_step": 0.01,
        "spread": random.randint(5, 30),
        "trade_mode": 4,
    }


def _mock_ohlcv(symbol: str, timeframe: str = "M15", count: int = 200) -> List[Dict]:
    tf_minutes = {"M1": 1, "M5": 5, "M15": 15, "H1": 60, "H4": 240, "D1": 1440}
    interval = tf_minutes.get(timeframe, 15) * 60
    now = int(time.time())
    base = _mock_base_prices.get(symbol, 1.0)
    digits = _mock_digits.get(symbol, 5)
    point = 10 ** (-digits)
    candles = []
    price = base - count * 2 * point
    for i in range(count):
        t = now - (count - i) * interval
        change = random.gauss(0, 20 * point)
        o = round(price, digits)
        c = round(price + change, digits)
        h = round(max(o, c) + abs(random.gauss(0, 10 * point)), digits)
        l = round(min(o, c) - abs(random.gauss(0, 10 * point)), digits)
        candles.append({
            "time": t * 1000,
            "open": o, "high": h, "low": l, "close": c,
            "volume": random.randint(100, 2000),
        })
        price = c
    return candles


def _mock_place_order(symbol, side, volume, **kwargs) -> Dict:
    global _mock_order_counter
    _mock_order_counter += 1
    price = _mock_price(symbol)
    pos = {
        "ticket": _mock_order_counter,
        "symbol": symbol,
        "type": side,
        "volume": volume,
        "open_price": price,
        "sl": kwargs.get("sl", 0.0),
        "tp": kwargs.get("tp", 0.0),
        "swap": 0.0,
        "magic": MAGIC_NUMBER,
        "comment": "Yash-Dashboard",
        "time_open": datetime.now(timezone.utc).isoformat(),
    }
    _mock_positions.append(pos)
    return {"success": True, "ticket": _mock_order_counter, "deal": _mock_order_counter, "price": price, "volume": volume}


def _mock_close_position(ticket: int) -> Dict:
    for i, p in enumerate(_mock_positions):
        if p["ticket"] == ticket:
            closed = _mock_positions.pop(i)
            pnl = _mock_position_pnl(closed)
            _mock_history.append({
                "ticket": ticket, "order": ticket, "symbol": closed["symbol"],
                "type": closed["type"], "volume": closed["volume"],
                "price": _mock_price(closed["symbol"]),
                "profit": pnl, "swap": closed.get("swap", 0),
                "commission": -0.50, "magic": MAGIC_NUMBER,
                "comment": "Close", "time": datetime.now(timezone.utc).isoformat(),
            })
            return {"success": True, "ticket": ticket, "deal": ticket, "price": _mock_price(closed["symbol"]), "volume": closed["volume"]}
    return {"success": False, "error": f"Position {ticket} not found"}


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("mt5_api")

# ---------------------------------------------------------------------------
# MT5 Thread-Safe Wrapper
# ---------------------------------------------------------------------------
_mt5_lock = threading.Lock()


def mt5_call(func, *args, **kwargs):
    """Execute an MT5 API call under the global lock."""
    with _mt5_lock:
        return func(*args, **kwargs)


# ---------------------------------------------------------------------------
# Symbol name resolution
# ---------------------------------------------------------------------------
# Brokers decorate instrument names differently — FTMO lists the indices as
# "US30.cash"/"US100.cash", others use ".spot", ".sim" or a bare name. Callers
# (the bot config and the dashboard) speak the plain name; we translate here so
# neither side has to know the broker's convention.
SYMBOL_SUFFIXES = ["", ".cash", ".spot", ".sim", ".c", ".pro", ".raw", "_"]

_symbol_map: Dict[str, str] = {}


def resolve_symbol(symbol: str) -> str:
    """Map a plain symbol name to the name this broker actually lists.

    Returns the input unchanged when MT5 is unavailable or nothing matches,
    so callers can keep treating the result as a symbol name either way.
    """
    if not HAS_MT5:
        return symbol
    if symbol in _symbol_map:
        return _symbol_map[symbol]

    for suffix in SYMBOL_SUFFIXES:
        candidate = symbol + suffix
        if mt5_call(mt5.symbol_info, candidate) is not None:
            _symbol_map[symbol] = candidate
            if candidate != symbol:
                log.info(f"  Symbol resolved: {symbol} -> {candidate}")
            return candidate

    _symbol_map[symbol] = symbol
    return symbol


def canonical_symbol(broker_symbol: str) -> str:
    """Inverse of resolve_symbol: broker's name -> the plain name callers use.

    Positions and deals come back wearing the broker's decoration ("US100.cash"),
    but the bot, the config and the dashboard all speak the plain name. Leaving
    the decorated form in place also risks tripping the 10-character symbol limit
    on PositionInfo, which would blank the entire positions list over one
    instrument.
    """
    for plain, broker in _symbol_map.items():
        if broker == broker_symbol:
            return plain

    for suffix in SYMBOL_SUFFIXES:
        if suffix and broker_symbol.endswith(suffix):
            return broker_symbol[: -len(suffix)]

    return broker_symbol


def connect_mt5() -> bool:
    """Initialize MT5 connection with credentials from .env."""
    if not HAS_MT5:
        log.error("MetaTrader5 package not available. Install: pip install MetaTrader5")
        return False

    log.info(f"Connecting to MT5: login={MT5_LOGIN}, server={MT5_SERVER}")

    init_ok = mt5.initialize(MT5_TERMINAL_PATH) if MT5_TERMINAL_PATH else mt5.initialize()
    if not init_ok:
        log.error(f"MT5 initialize failed: {mt5.last_error()}")
        return False

    if MT5_LOGIN and MT5_SERVER and MT5_PASSWORD:
        authorized = mt5.login(
            login=MT5_LOGIN,
            server=MT5_SERVER,
            password=MT5_PASSWORD,
        )
        if not authorized:
            log.error(f"MT5 login failed: {mt5.last_error()}")
            mt5.shutdown()
            return False

    info = mt5.account_info()
    if info is None:
        log.error("Failed to get account info after login")
        mt5.shutdown()
        return False

    log.info(
        f"MT5 connected — Account: {info.login}, "
        f"Balance: ${info.balance:,.2f}, "
        f"Server: {info.server}, "
        f"Name: {info.name}"
    )
    return True


def get_account_info() -> Optional[Dict]:
    """Fetch current account state from MT5."""
    if not HAS_MT5:
        return _mock_account_info()
    info = mt5_call(mt5.account_info)
    if info is None:
        return None
    return {
        "login": info.login,
        "name": info.name,
        "server": info.server,
        "balance": float(info.balance),
        "equity": float(info.equity),
        "margin": float(info.margin),
        "free_margin": float(info.margin_free),
        "margin_level": float(info.margin_level) if info.margin_level else 0.0,
        "profit": float(info.profit),
        "leverage": info.leverage,
        "currency": info.currency,
    }


def get_positions() -> List[Dict]:
    """Fetch all open positions from MT5."""
    if not HAS_MT5:
        return _mock_positions_with_pnl()
    positions = mt5_call(mt5.positions_get)
    if positions is None:
        return []
    result = []
    for p in positions:
        result.append({
            "ticket": p.ticket,
            "symbol": canonical_symbol(p.symbol),
            "type": "buy" if p.type == 0 else "sell",
            "volume": float(p.volume),
            "open_price": float(p.price_open),
            "current_price": float(p.price_current),
            "sl": float(p.sl),
            "tp": float(p.tp),
            "pnl": float(p.profit),
            "swap": float(p.swap),
            "magic": p.magic,
            "comment": p.comment,
            "time_open": datetime.fromtimestamp(p.time, tz=timezone.utc).isoformat(),
        })
    return result


def get_symbol_tick(symbol: str) -> Optional[Dict]:
    """Get latest tick for a symbol."""
    if not HAS_MT5:
        return _mock_symbol_tick(symbol)
    tick = mt5_call(mt5.symbol_info_tick, resolve_symbol(symbol))
    if tick is None:
        return None
    return {
        "symbol": symbol,
        "bid": float(tick.bid),
        "ask": float(tick.ask),
        "last": float(tick.last),
        "volume": float(tick.volume),
        "time": tick.time,
    }


def get_symbol_info(symbol: str) -> Optional[Dict]:
    """Get symbol properties (digits, point size, etc.)."""
    if not HAS_MT5:
        return _mock_symbol_info(symbol)
    broker_symbol = resolve_symbol(symbol)
    info = mt5_call(mt5.symbol_info, broker_symbol)
    if info is None:
        # Try enabling the symbol first
        mt5_call(mt5.symbol_select, broker_symbol, True)
        info = mt5_call(mt5.symbol_info, broker_symbol)
        if info is None:
            return None
    return {
        "symbol": info.name,
        "digits": info.digits,
        "point": float(info.point),
        "trade_contract_size": float(info.trade_contract_size),
        "volume_min": float(info.volume_min),
        "volume_max": float(info.volume_max),
        "volume_step": float(info.volume_step),
        "spread": info.spread,
        "trade_mode": info.trade_mode,
    }


def get_ohlcv(symbol: str, timeframe: str = "M15", count: int = 200) -> List[Dict]:
    """Fetch OHLCV candle data from MT5."""
    if not HAS_MT5:
        return _mock_ohlcv(symbol, timeframe, count)

    tf_map = {
        "M1": mt5.TIMEFRAME_M1,
        "M5": mt5.TIMEFRAME_M5,
        "M15": mt5.TIMEFRAME_M15,
        "H1": mt5.TIMEFRAME_H1,
        "H4": mt5.TIMEFRAME_H4,
        "D1": mt5.TIMEFRAME_D1,
    }
    mt5_tf = tf_map.get(timeframe, mt5.TIMEFRAME_M15)

    rates = mt5_call(mt5.copy_rates_from_pos, resolve_symbol(symbol), mt5_tf, 0, count)
    if rates is None or len(rates) == 0:
        return []

    result = []
    for r in rates:
        result.append({
            "time": int(r["time"]) * 1000,  # JS expects ms
            "open": float(r["open"]),
            "high": float(r["high"]),
            "low": float(r["low"]),
            "close": float(r["close"]),
            "volume": int(r["tick_volume"]),
        })
    return result


def place_order(
    symbol: str,
    side: str,
    volume: float,
    order_type: str = "market",
    price: float = 0.0,
    sl: float = 0.0,
    tp: float = 0.0,
    comment: str = "Yash-Dashboard",
) -> Dict:
    """Place an order via MT5."""
    if not HAS_MT5:
        return _mock_place_order(symbol, side, volume, sl=sl, tp=tp)

    # Translate to the broker's own name before anything touches MT5
    symbol = resolve_symbol(symbol)

    # Get symbol info
    sym_info = mt5_call(mt5.symbol_info, symbol)
    if sym_info is None:
        mt5_call(mt5.symbol_select, symbol, True)
        sym_info = mt5_call(mt5.symbol_info, symbol)
        if sym_info is None:
            return {"success": False, "error": f"Symbol {symbol} not found"}

    tick = mt5_call(mt5.symbol_info_tick, symbol)
    if tick is None:
        return {"success": False, "error": f"No tick data for {symbol}"}

    # Log symbol properties for debugging
    log.info(f"Symbol {symbol}: filling_mode={sym_info.filling_mode}, "
             f"trade_mode={sym_info.trade_mode}, "
             f"vol_min={sym_info.volume_min}, vol_max={sym_info.volume_max}, "
             f"vol_step={sym_info.volume_step}")

    # Validate and round volume
    vol_min = sym_info.volume_min
    vol_max = sym_info.volume_max
    vol_step = sym_info.volume_step
    if volume < vol_min:
        volume = vol_min
    if volume > vol_max:
        volume = vol_max
    if vol_step > 0:
        volume = round(round(volume / vol_step) * vol_step, 8)

    # Determine order type and price
    if order_type == "market":
        if side == "buy":
            mt5_order_type = mt5.ORDER_TYPE_BUY
            fill_price = tick.ask
        else:
            mt5_order_type = mt5.ORDER_TYPE_SELL
            fill_price = tick.bid
    elif order_type == "limit":
        if side == "buy":
            mt5_order_type = mt5.ORDER_TYPE_BUY_LIMIT
        else:
            mt5_order_type = mt5.ORDER_TYPE_SELL_LIMIT
        fill_price = price
    elif order_type == "stop":
        if side == "buy":
            mt5_order_type = mt5.ORDER_TYPE_BUY_STOP
        else:
            mt5_order_type = mt5.ORDER_TYPE_SELL_STOP
        fill_price = price
    else:
        return {"success": False, "error": f"Unknown order type: {order_type}"}

    # Build request with ALL MqlTradeRequest fields explicitly set
    # (MT5 Python API can leave garbage in unset C struct fields, causing 10013)
    is_market = (order_type == "market")

    # Re-fetch fresh tick right before sending (avoid stale prices)
    tick = mt5_call(mt5.symbol_info_tick, symbol)
    if tick is None:
        return {"success": False, "error": f"No fresh tick for {symbol}"}

    if is_market:
        fill_price = tick.ask if side == "buy" else tick.bid

    # Determine which filling modes to try based on symbol's filling_mode bitmask
    filling_modes = []
    if sym_info.filling_mode & 2:  # IOC first (preferred by FTMO)
        filling_modes.append((mt5.ORDER_FILLING_IOC, "IOC"))
    if sym_info.filling_mode & 1:
        filling_modes.append((mt5.ORDER_FILLING_FOK, "FOK"))
    filling_modes.append((mt5.ORDER_FILLING_RETURN, "RETURN"))

    last_error = "Unknown error"
    for fill_type, fill_name in filling_modes:
        # Build COMPLETE request — every field explicitly set
        request = {
            "action": mt5.TRADE_ACTION_DEAL if is_market else mt5.TRADE_ACTION_PENDING,
            "magic": 0,
            "order": 0,
            "symbol": symbol,
            "volume": float(volume),
            "price": float(fill_price),
            "stoplimit": 0.0,
            "sl": float(sl) if sl > 0 else 0.0,
            "tp": float(tp) if tp > 0 else 0.0,
            "deviation": 30,
            "type": mt5_order_type,
            "type_filling": fill_type,
            "type_time": mt5.ORDER_TIME_GTC,
            "expiration": 0,
            "comment": "Dashboard",
            "position": 0,
            "position_by": 0,
        }

        log.info(f"Trying {side} {volume} {symbol} @ {fill_price} with filling={fill_name}")
        log.info(f"Full request dict: {request}")

        with _mt5_lock:
            result = mt5.order_send(request)

        if result is not None and result.retcode in (10008, 10009, 10010):
            # Success!
            log.info(f"Order SUCCESS with filling={fill_name}: ticket={result.order}, deal={result.deal}")
            return {
                "success": True,
                "ticket": result.order,
                "deal": result.deal,
                "price": float(result.price),
                "volume": float(result.volume),
                "retcode": result.retcode,
                "comment": result.comment,
            }

        if result is not None:
            last_error = f"{result.comment} (retcode={result.retcode})"
            log.info(f"Filling {fill_name} rejected: retcode={result.retcode}, comment={result.comment}")
        else:
            with _mt5_lock:
                err = mt5.last_error()
            last_error = str(err)
            log.info(f"Filling {fill_name} returned None: {last_error}")

    # All filling modes failed
    log.error(f"All filling modes failed for {symbol}: {last_error}")
    return {"success": False, "error": last_error}


def close_position(ticket: int) -> Dict:
    """Close a specific position by ticket."""
    if not HAS_MT5:
        return _mock_close_position(ticket)

    positions = mt5_call(mt5.positions_get, ticket=ticket)
    if not positions or len(positions) == 0:
        return {"success": False, "error": f"Position {ticket} not found"}

    pos = positions[0]
    symbol = pos.symbol
    volume = pos.volume

    sym_info = mt5_call(mt5.symbol_info, symbol)
    tick = mt5_call(mt5.symbol_info_tick, symbol)
    if tick is None:
        return {"success": False, "error": f"No tick data for {symbol}"}

    # Opposite direction to close
    if pos.type == 0:  # BUY -> close with SELL
        close_type = mt5.ORDER_TYPE_SELL
        close_price = tick.bid
    else:  # SELL -> close with BUY
        close_type = mt5.ORDER_TYPE_BUY
        close_price = tick.ask

    # Determine filling modes from symbol info
    filling_modes = []
    if sym_info and sym_info.filling_mode & 2:
        filling_modes.append((mt5.ORDER_FILLING_IOC, "IOC"))
    if sym_info and sym_info.filling_mode & 1:
        filling_modes.append((mt5.ORDER_FILLING_FOK, "FOK"))
    filling_modes.append((mt5.ORDER_FILLING_RETURN, "RETURN"))

    last_error = "Unknown error"
    for fill_type, fill_name in filling_modes:
        # Build COMPLETE close request — every field explicitly set
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "magic": 0,
            "order": 0,
            "symbol": symbol,
            "volume": float(volume),
            "price": float(close_price),
            "stoplimit": 0.0,
            "sl": 0.0,
            "tp": 0.0,
            "deviation": 30,
            "type": close_type,
            "type_filling": fill_type,
            "type_time": mt5.ORDER_TIME_GTC,
            "expiration": 0,
            "comment": "Close",
            "position": ticket,
            "position_by": 0,
        }

        log.info(f"Closing position {ticket}: {symbol} {volume} lots (filling={fill_name})")
        log.info(f"Full close request: {request}")

        with _mt5_lock:
            result = mt5.order_send(request)

        if result is not None and result.retcode in (10008, 10009, 10010):
            log.info(f"Position {ticket} closed with {fill_name}: deal={result.deal}")
            return {
                "success": True,
                "ticket": result.order,
                "deal": result.deal,
                "price": float(result.price),
                "volume": float(result.volume),
            }

        if result is not None:
            last_error = f"{result.comment} (retcode={result.retcode})"
            log.info(f"Close filling {fill_name} rejected: retcode={result.retcode}, comment={result.comment}")
        else:
            with _mt5_lock:
                err = mt5.last_error()
            last_error = str(err)
            log.info(f"Close filling {fill_name} returned None: {last_error}")

    log.error(f"All filling modes failed to close {ticket}: {last_error}")
    return {"success": False, "error": last_error}


def get_order_history(count: int = 50) -> List[Dict]:
    """Fetch recent closed deals / order history."""
    if not HAS_MT5:
        return _mock_history[-count:]

    from datetime import timedelta

    # history_deals_get() wants NAIVE datetimes in the terminal's own clock.
    # Handing it timezone-aware UTC values silently matches nothing, which is
    # why the endpoint used to come back empty even with deals on the account.
    # The upper bound runs a day ahead so broker/terminal clock skew can't hide
    # a deal that was just filled.
    now = datetime.now()
    from_date = now - timedelta(days=HISTORY_LOOKBACK_DAYS)
    to_date = now + timedelta(days=1)

    deals = mt5_call(mt5.history_deals_get, from_date, to_date)
    if deals is None:
        return []

    # MT5 returns every deal on the account, including the opening balance
    # credit and any deposits/withdrawals (DEAL_TYPE_BALANCE and friends).
    # Those carry no symbol and would otherwise be counted as a winning trade
    # worth the whole account, wrecking win-rate and P&L stats downstream.
    trades = [
        d for d in deals
        if d.type in (mt5.DEAL_TYPE_BUY, mt5.DEAL_TYPE_SELL) and d.symbol
    ]

    result = []
    for d in trades[-count:]:
        result.append({
            "ticket": d.ticket,
            "order": d.order,
            "symbol": canonical_symbol(d.symbol),
            "type": "buy" if d.type == mt5.DEAL_TYPE_BUY else "sell",
            "volume": float(d.volume),
            "price": float(d.price),
            "profit": float(d.profit),
            "swap": float(d.swap),
            "commission": float(d.commission),
            "magic": d.magic,
            "comment": d.comment,
            "time": datetime.fromtimestamp(d.time, tz=timezone.utc).isoformat(),
            # DEAL_ENTRY_IN opened a position; OUT/INOUT/OUT_BY closed one.
            # Callers need this to tell a still-open trade from a finished one —
            # an entry deal always carries profit 0 and would otherwise be
            # reported as a break-even completed trade.
            "entry": int(d.entry),
            "is_close": d.entry in (
                mt5.DEAL_ENTRY_OUT,
                mt5.DEAL_ENTRY_INOUT,
                mt5.DEAL_ENTRY_OUT_BY,
            ),
        })
    return result


# ---------------------------------------------------------------------------
# FastAPI App
# ---------------------------------------------------------------------------
app = FastAPI(title="Yash MT5 Bridge", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins (file://, localhost, etc.)
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# -- Root: this process is an API, not a UI ----------------------------------
# It used to serve its own copy of the trading terminal, which meant two
# different builds of the same page were reachable at once — the stale one here
# and the current one on the Mac. There is exactly one terminal now, served by
# broker_hub.py, and this stays a pure data bridge.
TERMINAL_URL = os.getenv("TERMINAL_URL", "http://localhost:8600")


@app.get("/", response_class=HTMLResponse)
async def root():
    """Point anyone who lands here at the real terminal."""
    return HTMLResponse(
        content=(
            "<body style='font-family:system-ui;background:#0b0e13;color:#e6ebf5;"
            "display:flex;align-items:center;justify-content:center;height:100vh;margin:0'>"
            "<div style='text-align:center'>"
            "<h2 style='font-weight:600'>MT5 Bridge &mdash; API only</h2>"
            "<p style='color:#7d8799'>This port serves data, not the interface.</p>"
            f"<p><a style='color:#3b82f6' href='{TERMINAL_URL}'>Open the trading terminal &rarr;</a></p>"
            "<p style='color:#7d8799;font-size:13px'>Health: <code>/api/health</code> &middot; "
            "Docs: <code>/docs</code></p>"
            "</div></body>"
        )
    )


# -- Pydantic request models ------------------------------------------------
class OrderRequest(BaseModel):
    symbol: str
    side: str  # "buy" or "sell"
    volume: float
    order_type: str = "market"  # "market", "limit", "stop"
    price: float = 0.0
    sl: float = 0.0
    tp: float = 0.0


class CloseRequest(BaseModel):
    ticket: int


# -- REST Endpoints ----------------------------------------------------------
@app.get("/api/health")
async def health():
    """Health check — verifies MT5 connection."""
    if not HAS_MT5:
        info = _mock_account_info()
        return {"status": "connected (mock)", "account": info["login"], "server": info["server"]}
    info = get_account_info()
    if info is None:
        return JSONResponse(
            status_code=503,
            content={"status": "disconnected", "detail": "MT5 not connected"},
        )
    return {"status": "connected", "account": info["login"], "server": info.get("server", "")}


@app.get("/api/account")
async def account():
    """Get account balance, equity, margin, profit."""
    info = get_account_info()
    if info is None:
        raise HTTPException(status_code=503, detail="MT5 not connected")
    # Add FTMO-specific fields
    info["initial_capital"] = INITIAL_CAPITAL
    info["daily_loss_limit"] = INITIAL_CAPITAL * 0.05
    info["max_loss_limit"] = INITIAL_CAPITAL * 0.10
    info["profit_target"] = INITIAL_CAPITAL * 0.10
    info["max_loss_floor"] = INITIAL_CAPITAL - (INITIAL_CAPITAL * 0.10)
    return info


@app.get("/api/positions")
async def positions():
    """Get all open positions."""
    return get_positions()


@app.post("/api/order")
async def order(req: OrderRequest):
    """Place a new order."""
    result = place_order(
        symbol=req.symbol,
        side=req.side,
        volume=req.volume,
        order_type=req.order_type,
        price=req.price,
        sl=req.sl,
        tp=req.tp,
    )
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "Order failed"))
    return result


@app.post("/api/close")
async def close(req: CloseRequest):
    """Close a position by ticket."""
    result = close_position(req.ticket)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "Close failed"))
    return result


@app.get("/api/history")
async def history(count: int = 50):
    """Get recent deal history."""
    return get_order_history(count)


@app.get("/api/instruments")
async def instruments(q: str = "", limit: int = 60):
    """Every instrument this broker lists, optionally filtered by `q`.

    The watchlist in /api/symbols is a fixed handful; this exposes the broker's
    full universe so a search box can offer what FTMO actually trades rather
    than a hard-coded list.
    """
    if not HAS_MT5:
        return [{"symbol": s, "description": s} for s in WATCHED_SYMBOLS]

    all_symbols = mt5_call(mt5.symbols_get)
    if all_symbols is None:
        return []

    needle = q.upper().strip()
    out = []
    for s in all_symbols:
        name = canonical_symbol(s.name)
        if needle and needle not in name.upper() and needle not in (s.description or "").upper():
            continue
        out.append({
            "symbol": name,
            "broker_symbol": s.name,
            "description": s.description or "",
            "path": s.path or "",
        })
        if len(out) >= limit:
            break
    return out


@app.get("/api/symbols")
async def symbols_info():
    """Get info for all watched symbols."""
    result = {}
    for sym in WATCHED_SYMBOLS:
        info = get_symbol_info(sym)
        tick = get_symbol_tick(sym)
        if info and tick:
            result[sym] = {**info, **tick}
        elif info:
            result[sym] = info
    return result


@app.get("/api/debug/{symbol}")
async def symbol_debug(symbol: str):
    """Dump ALL symbol properties for debugging order issues."""
    if not HAS_MT5:
        raise HTTPException(status_code=503, detail="MT5 not available")

    symbol = resolve_symbol(symbol)
    with _mt5_lock:
        mt5.symbol_select(symbol, True)
        info = mt5.symbol_info(symbol)
        tick = mt5.symbol_info_tick(symbol)

    if info is None:
        raise HTTPException(status_code=404, detail=f"Symbol {symbol} not found")

    # Dump every property from SymbolInfo
    props = {}
    for attr in dir(info):
        if not attr.startswith("_"):
            try:
                val = getattr(info, attr)
                if not callable(val):
                    props[attr] = val
            except Exception:
                pass

    tick_data = {}
    if tick:
        for attr in dir(tick):
            if not attr.startswith("_"):
                try:
                    val = getattr(tick, attr)
                    if not callable(val):
                        tick_data[attr] = val
                except Exception:
                    pass

    # Also try order_check with minimal request
    check_results = {}
    if tick:
        for fill_type, fill_name in [(0, "FOK"), (1, "IOC"), (2, "RETURN")]:
            req = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": info.volume_min,
                "type": mt5.ORDER_TYPE_BUY,
                "price": tick.ask,
                "type_filling": fill_type,
                "deviation": 30,
            }
            with _mt5_lock:
                check = mt5.order_check(req)
            if check:
                check_results[fill_name] = {
                    "retcode": check.retcode,
                    "comment": check.comment,
                    "balance": check.balance,
                    "equity": check.equity,
                    "margin": check.margin,
                    "margin_free": check.margin_free,
                }
            else:
                with _mt5_lock:
                    err = mt5.last_error()
                check_results[fill_name] = {"error": str(err)}

    return {
        "symbol": symbol,
        "properties": props,
        "tick": tick_data,
        "order_check": check_results,
    }


@app.get("/api/ohlcv/{symbol}")
async def ohlcv(symbol: str, timeframe: str = "M15", count: int = 200):
    """Get OHLCV candle data for charting."""
    data = get_ohlcv(symbol, timeframe, count)
    if not data:
        raise HTTPException(status_code=404, detail=f"No data for {symbol}/{timeframe}")
    return data


# -- WebSocket for live price streaming --------------------------------------
class ConnectionManager:
    """Manages WebSocket connections for price streaming."""

    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        log.info(f"WebSocket connected. Total: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        log.info(f"WebSocket disconnected. Total: {len(self.active_connections)}")

    async def broadcast(self, message: dict):
        disconnected = []
        for conn in self.active_connections:
            try:
                await conn.send_json(message)
            except Exception:
                disconnected.append(conn)
        for conn in disconnected:
            self.disconnect(conn)


ws_manager = ConnectionManager()


@app.websocket("/ws/prices")
async def websocket_prices(websocket: WebSocket):
    """Stream live prices for all watched symbols every 500ms."""
    await ws_manager.connect(websocket)
    try:
        while True:
            # Gather all ticks
            ticks = {}
            for sym in WATCHED_SYMBOLS:
                tick = get_symbol_tick(sym)
                if tick:
                    ticks[sym] = tick

            # Also send account snapshot
            account = get_account_info()
            positions_list = get_positions()

            message = {
                "type": "tick",
                "timestamp": datetime.now(tz=timezone.utc).isoformat(),
                "ticks": ticks,
                "account": account,
                "positions": positions_list,
            }

            await websocket.send_json(message)

            # Check for incoming messages (order commands, symbol changes)
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=0.5)
                cmd = json.loads(data)
                await handle_ws_command(websocket, cmd)
            except asyncio.TimeoutError:
                pass  # No message, continue streaming

    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
    except Exception as e:
        log.error(f"WebSocket error: {e}")
        ws_manager.disconnect(websocket)


async def handle_ws_command(websocket: WebSocket, cmd: dict):
    """Handle commands received over WebSocket."""
    action = cmd.get("action")

    if action == "subscribe":
        # Client wants to subscribe to specific symbols
        symbols = cmd.get("symbols", [])
        log.info(f"Client subscribed to: {symbols}")
        await websocket.send_json({"type": "subscribed", "symbols": symbols})

    elif action == "order":
        result = place_order(
            symbol=cmd["symbol"],
            side=cmd["side"],
            volume=cmd["volume"],
            order_type=cmd.get("order_type", "market"),
            price=cmd.get("price", 0),
            sl=cmd.get("sl", 0),
            tp=cmd.get("tp", 0),
        )
        await websocket.send_json({"type": "order_result", **result})

    elif action == "close":
        result = close_position(cmd["ticket"])
        await websocket.send_json({"type": "close_result", **result})

    elif action == "ohlcv":
        data = get_ohlcv(
            cmd.get("symbol", "EURUSD"),
            cmd.get("timeframe", "M15"),
            cmd.get("count", 200),
        )
        await websocket.send_json({"type": "ohlcv", "symbol": cmd.get("symbol"), "data": data})


# ---------------------------------------------------------------------------
# Startup / Shutdown
# ---------------------------------------------------------------------------
@app.on_event("startup")
async def startup_event():
    log.info("=" * 60)
    log.info("  Yash MT5 Bridge Server Starting")
    log.info("=" * 60)

    if HAS_MT5:
        success = connect_mt5()
        if success:
            log.info("MT5 connection established successfully")

            # Enable all watched symbols, under whatever name this broker uses
            for sym in WATCHED_SYMBOLS:
                broker_sym = resolve_symbol(sym)
                ok = mt5_call(mt5.symbol_select, broker_sym, True)
                log.info(f"  Enabled symbol: {broker_sym}" + ("" if ok else " (select FAILED)"))
        else:
            log.error("MT5 connection FAILED — running in disconnected mode")
    else:
        log.warning("MT5 package not available — install MetaTrader5 on Windows")

    log.info(f"API server running on http://localhost:{API_PORT}")
    log.info(f"Dashboard WebSocket: ws://localhost:{API_PORT}/ws/prices")
    log.info("=" * 60)


@app.on_event("shutdown")
async def shutdown_event():
    log.info("Shutting down MT5 connection...")
    if HAS_MT5:
        mt5_call(mt5.shutdown)
    log.info("Server stopped.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    print(f"""
╔══════════════════════════════════════════════════════════════╗
║            Yash MT5 Bridge Server v1.0.0                 ║
║                                                             ║
║  DASHBOARD:  http://localhost:{API_PORT}                         ║
║                                                             ║
║  API Docs:   http://localhost:{API_PORT}/docs                    ║
║  Health:     http://localhost:{API_PORT}/api/health               ║
║                                                             ║
║  Open the DASHBOARD link above in Chrome to start trading!  ║
║  Make sure MetaTrader 5 terminal is running!                ║
╚══════════════════════════════════════════════════════════════╝
""")

    uvicorn.run(
        "mt5_api_server:app",
        host="0.0.0.0",
        port=API_PORT,
        reload=False,
        log_level="info",
    )
