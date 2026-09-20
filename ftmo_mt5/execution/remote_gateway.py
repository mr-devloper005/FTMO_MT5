"""Remote MT5 Gateway — connects to mt5_api_server.py running on a Windows VM.

Drop-in replacement for MT5Gateway. Instead of calling MetaTrader5 DLLs locally,
this makes HTTP calls to the API bridge server running on a Windows machine
that has the real MT5 terminal.

Usage:
    # Instead of MT5Gateway(login, server, password):
    gateway = RemoteMT5Gateway(vm_url="http://192.168.64.2:8500")

    # Same interface as MT5Gateway
    gateway.initialize()
    account = gateway.get_account_info()
    tick = gateway.get_tick("EURUSD")
"""

__all__ = ["RemoteMT5Gateway"]

import os
import time
from datetime import datetime
from decimal import Decimal
from typing import Optional

import structlog
from opentelemetry import trace

from ftmo_mt5.core.exceptions import MT5ConnectionError, MT5ExecutionError
from ftmo_mt5.core.models import AccountState, PositionInfo

logger = structlog.get_logger(__name__)
otel_tracer = trace.get_tracer(__name__)

VM_URL = os.getenv("MT5_VM_URL", "http://localhost:8500")

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    import urllib.request
    import urllib.error
    import json as _json
    HAS_REQUESTS = False


def _http_get(url: str, timeout: int = 10) -> dict:
    if HAS_REQUESTS:
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return _json.loads(resp.read().decode())


def _http_post(url: str, data: dict, timeout: int = 10) -> dict:
    if HAS_REQUESTS:
        resp = requests.post(url, json=data, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    body = _json.dumps(data).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return _json.loads(resp.read().decode())


class RemoteMT5Gateway:
    """Connects to mt5_api_server.py on a Windows VM via HTTP.

    Same interface as MT5Gateway so it's a drop-in replacement.
    """

    def __init__(self, vm_url: Optional[str] = None, **kwargs):
        self._url = (vm_url or VM_URL).rstrip("/")
        self._is_initialized = False
        self._last_health_check = datetime.now()
        logger.info("remote_mt5_gateway_created", url=self._url)

    def initialize(self) -> bool:
        for attempt in range(3):
            try:
                resp = _http_get(f"{self._url}/api/health", timeout=5)
                if resp.get("status") in ("connected", "connected (mock)"):
                    self._is_initialized = True
                    logger.info("remote_mt5_connected", server=resp.get("server"), account=resp.get("account"))
                    return True
            except Exception as e:
                logger.warning("remote_mt5_connect_failed", attempt=attempt, error=str(e))
                time.sleep(2 ** attempt)
        raise MT5ConnectionError("Cannot reach MT5 API server", url=self._url)

    def shutdown(self) -> None:
        self._is_initialized = False
        logger.info("remote_mt5_shutdown")

    def health_check(self) -> bool:
        try:
            resp = _http_get(f"{self._url}/api/health", timeout=5)
            ok = resp.get("status") in ("connected", "connected (mock)")
            self._last_health_check = datetime.now()
            return ok
        except Exception:
            return False

    def describe(self):
        """Identity plus current status, for the management panel."""
        from ftmo_mt5.execution.broker_base import BrokerDescriptor

        try:
            resp = _http_get(f"{self._url}/api/health", timeout=5)
            connected = str(resp.get("status", "")).startswith("connected")
            detail = (
                f"account {resp.get('account')} on {resp.get('server')}"
                if connected else f"bridge says: {resp.get('status')}"
            )
        except Exception as e:
            connected, detail = False, f"bridge unreachable at {self._url} ({e})"

        return BrokerDescriptor(
            key="mt5",
            label="MetaTrader 5 (FTMO)",
            connected=connected,
            detail=detail,
        )

    def get_tick(self, symbol: str) -> Optional[dict]:
        try:
            resp = _http_get(f"{self._url}/api/symbols")
            sym_data = resp.get(symbol)
            if not sym_data:
                return None
            return {
                "bid": Decimal(str(sym_data["bid"])),
                "ask": Decimal(str(sym_data["ask"])),
                "last": Decimal(str(sym_data.get("last", sym_data["bid"]))),
                "time": int(sym_data.get("time", time.time())),
            }
        except Exception as e:
            logger.error("remote_get_tick_error", symbol=symbol, error=str(e))
            return None

    def get_account_info(self) -> AccountState:
        try:
            info = _http_get(f"{self._url}/api/account")
            return AccountState(
                balance=Decimal(str(info["balance"])),
                equity=Decimal(str(info["equity"])),
                margin=Decimal(str(info["margin"])),
                free_margin=Decimal(str(info["free_margin"])),
                initial_capital=Decimal(str(info.get("initial_capital", info["balance"]))),
                timestamp=datetime.utcnow(),
            )
        except Exception as e:
            logger.error("remote_get_account_error", error=str(e))
            raise MT5ConnectionError("Failed to get account info from VM", error=str(e))

    def get_positions(self, magic: Optional[int] = None) -> list[PositionInfo]:
        try:
            positions = _http_get(f"{self._url}/api/positions")
            result = []
            for p in positions:
                if magic is not None and p.get("magic") != magic:
                    continue
                result.append(PositionInfo(
                    ticket=int(p["ticket"]),
                    symbol=str(p["symbol"]),
                    type="LONG" if p["type"] == "buy" else "SHORT",
                    volume=Decimal(str(p["volume"])),
                    price_open=Decimal(str(p["open_price"])),
                    sl=Decimal(str(p["sl"])) if p.get("sl") else None,
                    tp=Decimal(str(p["tp"])) if p.get("tp") else None,
                    profit=Decimal(str(p.get("pnl", 0))),
                    magic=int(p.get("magic", 0)),
                    comment=str(p.get("comment", "")),
                ))
            return result
        except Exception as e:
            logger.error("remote_get_positions_error", error=str(e))
            return []

    def get_ohlcv(self, symbol: str, timeframe: str = "M15", count: int = 200) -> list[dict]:
        """Fetch closed candles for a symbol.

        Returns a list oldest-first with Decimal prices, or an empty list when
        the bridge has no history for the symbol.
        """
        try:
            bars = _http_get(f"{self._url}/api/ohlcv/{symbol}?timeframe={timeframe}&count={count}", timeout=20)
            return [
                {
                    "time": int(b["time"]),
                    "open": Decimal(str(b["open"])),
                    "high": Decimal(str(b["high"])),
                    "low": Decimal(str(b["low"])),
                    "close": Decimal(str(b["close"])),
                    "volume": int(b.get("volume", 0)),
                }
                for b in bars
            ]
        except Exception as e:
            logger.error("remote_get_ohlcv_error", symbol=symbol, timeframe=timeframe, error=str(e))
            return []

    def get_quotes(self, symbols: list[str]) -> dict:
        """Quote a whole watchlist in ONE call.

        The bridge already returns every watched symbol from /api/symbols;
        asking it per-symbol multiplies the round trips for no benefit, which
        matters when a live panel polls twice a second.
        """
        try:
            data = _http_get(f"{self._url}/api/symbols", timeout=10)
        except Exception as e:
            logger.error("remote_get_quotes_error", error=str(e))
            return {s: None for s in symbols}

        out = {}
        for s in symbols:
            row = data.get(s)
            if row and row.get("bid") and row.get("ask"):
                out[s] = {
                    "bid": Decimal(str(row["bid"])),
                    "ask": Decimal(str(row["ask"])),
                    "last": Decimal(str(row.get("last", row["bid"]))),
                    "time": int(row.get("time", time.time())),
                }
            else:
                out[s] = None
        return out

    def search_symbols(self, query: str, limit: int = 40) -> list[dict]:
        """Instruments this broker lists, matching `query`.

        Falls back to an empty list when the bridge predates the
        /api/instruments endpoint, so an older VM degrades to "no results"
        rather than breaking the search box.
        """
        try:
            rows = _http_get(f"{self._url}/api/instruments?q={query}&limit={limit}", timeout=15)
            return [
                {
                    "symbol": r.get("symbol", ""),
                    "description": r.get("description", ""),
                    "broker": "mt5",
                }
                for r in rows
            ]
        except Exception as e:
            logger.warning("remote_search_symbols_unavailable", error=str(e))
            return []

    def get_history(self, count: int = 50) -> list[dict]:
        """Recent closed deals."""
        try:
            deals = _http_get(f"{self._url}/api/history?count={count}", timeout=20)
            return [
                {
                    "broker": "mt5",
                    "ticket": d.get("ticket"),
                    "symbol": d.get("symbol", ""),
                    "type": d.get("type", ""),
                    "volume": float(d.get("volume", 0)),
                    "price": float(d.get("price", 0)),
                    "profit": float(d.get("profit", 0)),
                    "time": d.get("time", ""),
                    "is_close": d.get("is_close", True),
                }
                for d in deals
            ]
        except Exception as e:
            logger.error("remote_get_history_error", error=str(e))
            return []

    def get_symbol_info(self, symbol: str) -> Optional[dict]:
        try:
            resp = _http_get(f"{self._url}/api/symbols")
            sym_data = resp.get(symbol)
            if not sym_data:
                return None
            return {"name": symbol, "visible": True}
        except Exception:
            return None

    def order_send(self, request: dict) -> dict:
        try:
            action = request.get("action", "buy")
            if isinstance(action, int):
                action = "buy"
            symbol = request.get("symbol", "")
            volume = float(request.get("volume", 0.01))
            sl = float(request.get("sl", 0))
            tp = float(request.get("tp", 0))

            side = "buy"
            order_type = request.get("type")
            if order_type is not None:
                if isinstance(order_type, int) and order_type == 1:
                    side = "sell"
                elif isinstance(order_type, str) and "sell" in order_type.lower():
                    side = "sell"

            data = {
                "symbol": symbol,
                "side": side,
                "volume": volume,
                "order_type": "market",
                "sl": sl,
                "tp": tp,
            }
            resp = _http_post(f"{self._url}/api/order", data)
            return {
                "retcode": 10000 if resp.get("success") else 10006,
                "order": resp.get("ticket", 0),
                "volume": Decimal(str(resp.get("volume", volume))),
                "price": Decimal(str(resp.get("price", 0))),
                "comment": "OK" if resp.get("success") else resp.get("error", "Failed"),
            }
        except Exception as e:
            logger.error("remote_order_send_error", error=str(e))
            raise MT5ExecutionError("Failed to send order via VM", retcode=-1, error=str(e))

    def close_position(self, position) -> dict:
        try:
            ticket = position.ticket if hasattr(position, "ticket") else int(position)
            data = {"ticket": ticket}
            resp = _http_post(f"{self._url}/api/close", data)
            return resp
        except Exception as e:
            logger.error("remote_close_position_error", error=str(e))
            return {"success": False, "error": str(e)}

    def history_deals_get(self, position_id: int) -> list[dict]:
        try:
            history = _http_get(f"{self._url}/api/history?count=50")
            return [
                {
                    "ticket": int(d["ticket"]),
                    "order": int(d.get("order", d["ticket"])),
                    "volume": Decimal(str(d["volume"])),
                    "price": Decimal(str(d["price"])),
                    "commission": Decimal(str(d.get("commission", 0))),
                }
                for d in history
                if d.get("ticket") == position_id or d.get("order") == position_id
            ]
        except Exception:
            return []

    def history_orders_get(self, ticket: int) -> list[dict]:
        return self.history_deals_get(ticket)
