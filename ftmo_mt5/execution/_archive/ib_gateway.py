"""Interactive Brokers gateway, exposing the same surface as the MT5 gateway.

Unlike MetaTrader 5 — which only speaks to a Windows terminal and therefore
lives in the VM — IB's API is reachable straight from macOS, so this runs
locally. It talks to a running **TWS** or **IB Gateway** over the socket API;
neither this module nor anything above it can conjure that process into
existence, so `initialize()` fails cleanly when nothing is listening.

Threading note: ib_async is asyncio-native, while every other gateway here is
synchronous and gets called from worker threads. Rather than infect callers
with async, this class owns a private event loop on a background thread and
marshals each call into it. All public methods are safe to call from any
thread, including from inside `asyncio.to_thread`.

Ports, by convention:
    7497  TWS paper        7496  TWS live
    4002  Gateway paper    4001  Gateway live
The default is a **paper** port on purpose. Live trading must be an explicit,
deliberate change — never something that happens because a default was wrong.
"""

__all__ = [
    "IBGateway",
]

import asyncio
import os
import threading
from concurrent.futures import TimeoutError as FutureTimeoutError
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

import structlog

from ftmo_mt5.core.exceptions import MT5ConnectionError
from ftmo_mt5.core.models import AccountState, PositionInfo
from ftmo_mt5.execution.broker_base import BrokerDescriptor

logger = structlog.get_logger(__name__)

IB_HOST = os.getenv("IB_HOST", "127.0.0.1")
IB_PORT = int(os.getenv("IB_PORT", "7497"))
IB_CLIENT_ID = int(os.getenv("IB_CLIENT_ID", "17"))
IB_ACCOUNT = os.getenv("IB_ACCOUNT", "")
IB_CALL_TIMEOUT_S = float(os.getenv("IB_CALL_TIMEOUT_S", "20"))

# MT5-style timeframe -> (IB barSize, IB durationString multiplier in seconds)
_TIMEFRAMES = {
    "M1": ("1 min", 60),
    "M5": ("5 mins", 300),
    "M15": ("15 mins", 900),
    "H1": ("1 hour", 3600),
    "H4": ("4 hours", 14400),
    "D1": ("1 day", 86400),
}

# Instruments IB does not express as a plain FX pair. Anything not listed and
# shaped like a 6-letter pair is treated as Forex; everything else as a stock.
_METALS = {"XAUUSD", "XAGUSD"}
_CRYPTO = {"BTCUSD": "BTC", "ETHUSD": "ETH"}
_INDEX_CFD = {"US30": "IBUS30", "US100": "IBUS100", "US500": "IBUS500"}


class IBGateway:
    """Synchronous IB gateway backed by a private asyncio loop."""

    def __init__(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        client_id: Optional[int] = None,
        account: Optional[str] = None,
    ) -> None:
        self.host = host or IB_HOST
        self.port = int(port or IB_PORT)
        self.client_id = int(client_id if client_id is not None else IB_CLIENT_ID)
        self.account = account if account is not None else IB_ACCOUNT

        self._ib: Any = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._connected = False
        self._last_error = "not connected"
        self._contract_cache: dict[str, Any] = {}

        logger.info(
            "ib_gateway_created",
            host=self.host,
            port=self.port,
            client_id=self.client_id,
            mode="paper" if self.port in (7497, 4002) else "LIVE",
        )

    # ------------------------------------------------------------------
    # Event-loop plumbing
    # ------------------------------------------------------------------

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        """Start the private loop thread once, and return the loop."""
        if self._loop and self._loop.is_running():
            return self._loop

        ready = threading.Event()

        def _run() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            ready.set()
            loop.run_forever()

        self._thread = threading.Thread(target=_run, name="ib-gateway-loop", daemon=True)
        self._thread.start()
        ready.wait(timeout=5)

        if not self._loop:
            raise MT5ConnectionError("Could not start IB event loop")
        return self._loop

    def _call(self, coro, timeout: Optional[float] = None):
        """Run a coroutine on the private loop and wait for its result."""
        loop = self._ensure_loop()
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        try:
            return future.result(timeout=timeout or IB_CALL_TIMEOUT_S)
        except FutureTimeoutError:
            future.cancel()
            raise MT5ConnectionError(f"IB call timed out after {timeout or IB_CALL_TIMEOUT_S}s")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def initialize(self) -> bool:
        """Connect to TWS/IB Gateway. Raises when nothing is listening."""
        try:
            from ib_async import IB
        except ImportError as e:
            self._last_error = "ib_async not installed (pip install ib_async)"
            raise MT5ConnectionError(self._last_error, error=str(e))

        self._ib = IB()

        async def _connect() -> None:
            await self._ib.connectAsync(
                self.host, self.port, clientId=self.client_id, timeout=10
            )

        try:
            self._call(_connect(), timeout=25)
        except Exception as e:
            self._connected = False
            self._last_error = (
                f"cannot reach TWS/IB Gateway at {self.host}:{self.port} — "
                f"is it running with the API enabled? ({e})"
            )
            logger.warning("ib_connect_failed", host=self.host, port=self.port, error=str(e))
            raise MT5ConnectionError(self._last_error, host=self.host, port=self.port)

        self._connected = True
        accounts = list(self._ib.managedAccounts() or [])
        if not self.account and accounts:
            self.account = accounts[0]
        self._last_error = ""

        logger.info(
            "ib_connected",
            host=self.host,
            port=self.port,
            account=self.account,
            accounts=accounts,
        )
        return True

    def shutdown(self) -> None:
        """Disconnect and stop the private loop."""
        try:
            if self._ib and self._connected:
                self._call(self._disconnect_async(), timeout=5)
        except Exception as e:
            logger.warning("ib_shutdown_error", error=str(e))
        finally:
            self._connected = False
            if self._loop and self._loop.is_running():
                self._loop.call_soon_threadsafe(self._loop.stop)
            logger.info("ib_shutdown")

    async def _disconnect_async(self) -> None:
        self._ib.disconnect()

    def health_check(self) -> bool:
        try:
            return bool(self._ib and self._ib.isConnected())
        except Exception:
            return False

    def describe(self) -> BrokerDescriptor:
        live = self.health_check()
        mode = "paper" if self.port in (7497, 4002) else "live"
        return BrokerDescriptor(
            key="ib",
            label="Interactive Brokers",
            connected=live,
            detail=(
                f"account {self.account} ({mode}) via {self.host}:{self.port}"
                if live else (self._last_error or "not connected")
            ),
        )

    # ------------------------------------------------------------------
    # Contracts
    # ------------------------------------------------------------------

    def _contract(self, symbol: str):
        """Map an MT5-style symbol onto an IB contract.

        IB has no single "symbol" namespace — a currency pair, a metal and an
        index CFD are three different contract classes — so the mapping is
        explicit rather than guessed.
        """
        if symbol in self._contract_cache:
            return self._contract_cache[symbol]

        from ib_async import CFD, Crypto, Forex, Stock

        upper = symbol.upper()
        if upper in _INDEX_CFD:
            contract = CFD(_INDEX_CFD[upper], exchange="SMART", currency="USD")
        elif upper in _CRYPTO:
            contract = Crypto(_CRYPTO[upper], exchange="PAXOS", currency="USD")
        elif upper in _METALS:
            contract = CFD(upper, exchange="SMART", currency="USD")
        elif len(upper) == 6 and upper.isalpha():
            contract = Forex(upper)
        else:
            contract = Stock(upper, exchange="SMART", currency="USD")

        self._contract_cache[symbol] = contract
        return contract

    # ------------------------------------------------------------------
    # Market data
    # ------------------------------------------------------------------

    def get_tick(self, symbol: str) -> Optional[dict]:
        if not self.health_check():
            return None

        async def _tick():
            contract = self._contract(symbol)
            details = await self._ib.qualifyContractsAsync(contract)
            if not details:
                return None
            ticker = self._ib.reqMktData(contract, "", False, False)
            # IB streams asynchronously; give the first snapshot a moment.
            for _ in range(20):
                await asyncio.sleep(0.1)
                if ticker.bid and ticker.ask:
                    break
            self._ib.cancelMktData(contract)
            if not ticker.bid or not ticker.ask:
                return None
            return {
                "bid": Decimal(str(ticker.bid)),
                "ask": Decimal(str(ticker.ask)),
                "last": Decimal(str(ticker.last or ticker.bid)),
                "time": int(datetime.now(timezone.utc).timestamp()),
            }

        try:
            return self._call(_tick())
        except Exception as e:
            logger.error("ib_get_tick_error", symbol=symbol, error=str(e))
            return None

    def get_ohlcv(self, symbol: str, timeframe: str = "M15", count: int = 200) -> list[dict]:
        if not self.health_check():
            return []

        bar_size, seconds = _TIMEFRAMES.get(timeframe, _TIMEFRAMES["M15"])
        duration_seconds = max(seconds * count, 60)
        # IB rejects raw second-durations beyond a day; step up the unit.
        if duration_seconds <= 86400:
            duration = f"{duration_seconds} S"
        else:
            duration = f"{min(int(duration_seconds / 86400) + 1, 365)} D"

        async def _bars():
            contract = self._contract(symbol)
            if not await self._ib.qualifyContractsAsync(contract):
                return []
            bars = await self._ib.reqHistoricalDataAsync(
                contract,
                endDateTime="",
                durationStr=duration,
                barSizeSetting=bar_size,
                whatToShow="MIDPOINT",
                useRTH=False,
                formatDate=2,
            )
            return [
                {
                    "time": int(b.date.timestamp() * 1000) if hasattr(b.date, "timestamp") else 0,
                    "open": Decimal(str(b.open)),
                    "high": Decimal(str(b.high)),
                    "low": Decimal(str(b.low)),
                    "close": Decimal(str(b.close)),
                    "volume": int(b.volume) if b.volume and b.volume > 0 else 0,
                }
                for b in bars[-count:]
            ]

        try:
            return self._call(_bars(), timeout=45)
        except Exception as e:
            logger.error("ib_get_ohlcv_error", symbol=symbol, timeframe=timeframe, error=str(e))
            return []

    def get_symbol_info(self, symbol: str) -> Optional[dict]:
        if not self.health_check():
            return None
        try:
            return self._call(self._symbol_info_async(symbol))
        except Exception:
            return None

    async def _symbol_info_async(self, symbol: str) -> Optional[dict]:
        contract = self._contract(symbol)
        qualified = await self._ib.qualifyContractsAsync(contract)
        if not qualified:
            return None
        c = qualified[0]
        return {
            "name": symbol,
            "visible": True,
            "sec_type": c.secType,
            "exchange": c.exchange,
            "currency": c.currency,
            "con_id": c.conId,
        }

    # ------------------------------------------------------------------
    # Account and positions
    # ------------------------------------------------------------------

    def get_account_info(self) -> AccountState:
        if not self.health_check():
            raise MT5ConnectionError("IB not connected", detail=self._last_error)

        async def _account():
            values = await self._ib.accountSummaryAsync(self.account or "All")
            return {v.tag: v.value for v in values if not self.account or v.account == self.account}

        try:
            summary = self._call(_account())
        except Exception as e:
            logger.error("ib_get_account_error", error=str(e))
            raise MT5ConnectionError("Failed to read IB account", error=str(e))

        def pick(*tags: str) -> Decimal:
            for tag in tags:
                raw = summary.get(tag)
                if raw not in (None, ""):
                    try:
                        return Decimal(str(raw))
                    except Exception:
                        continue
            return Decimal("0")

        equity = pick("NetLiquidation", "EquityWithLoanValue")
        balance = pick("TotalCashValue", "NetLiquidation")
        margin = pick("MaintMarginReq", "InitMarginReq")
        free_margin = pick("AvailableFunds", "ExcessLiquidity")

        return AccountState(
            balance=balance,
            equity=equity,
            margin=margin,
            free_margin=free_margin,
            initial_capital=balance,
            timestamp=datetime.now(timezone.utc),
        )

    def get_positions(self, magic: Optional[int] = None) -> list[PositionInfo]:
        """Open positions. `magic` has no IB equivalent and is ignored."""
        if not self.health_check():
            return []

        async def _positions():
            return list(self._ib.positions(self.account) if self.account else self._ib.positions())

        try:
            raw = self._call(_positions())
        except Exception as e:
            logger.error("ib_get_positions_error", error=str(e))
            return []

        out: list[PositionInfo] = []
        for p in raw:
            qty = Decimal(str(p.position))
            if qty == 0:
                continue
            symbol = (p.contract.localSymbol or p.contract.symbol or "").replace(".", "")[:10]
            out.append(
                PositionInfo(
                    ticket=int(p.contract.conId),
                    symbol=symbol or "UNKNOWN",
                    type="LONG" if qty > 0 else "SHORT",
                    volume=abs(qty),
                    price_open=Decimal(str(p.avgCost)) if p.avgCost else Decimal("0.00001"),
                    sl=None,
                    tp=None,
                    profit=Decimal("0"),
                    magic=0,
                    comment="IB",
                )
            )
        return out

    # ------------------------------------------------------------------
    # Trading
    # ------------------------------------------------------------------

    def order_send(self, request: dict) -> dict:
        """Place a market order.

        Accepts the same loosely-typed dict the MT5 gateway takes, so the
        execution agent does not need to know which broker it is talking to.
        """
        if not self.health_check():
            return {"retcode": -1, "comment": "IB not connected", "order": 0}

        symbol = request.get("symbol", "")
        volume = float(request.get("volume", 0) or 0)
        if volume <= 0:
            return {"retcode": -1, "comment": "volume must be positive", "order": 0}

        side = "BUY"
        order_type = request.get("type", request.get("action", "buy"))
        if isinstance(order_type, int):
            side = "SELL" if order_type == 1 else "BUY"
        elif isinstance(order_type, str) and "sell" in order_type.lower():
            side = "SELL"

        async def _send():
            from ib_async import MarketOrder

            contract = self._contract(symbol)
            if not await self._ib.qualifyContractsAsync(contract):
                return {"retcode": -1, "comment": f"unknown IB contract for {symbol}", "order": 0}

            trade = self._ib.placeOrder(contract, MarketOrder(side, volume))
            # Wait briefly for the broker to acknowledge; a resting order is
            # still a success, so don't require a fill.
            for _ in range(50):
                await asyncio.sleep(0.1)
                if trade.orderStatus.status in ("Filled", "Submitted", "PreSubmitted", "Cancelled"):
                    break

            status = trade.orderStatus
            ok = status.status in ("Filled", "Submitted", "PreSubmitted")
            return {
                "retcode": 10000 if ok else 10006,
                "order": int(trade.order.orderId),
                "volume": Decimal(str(status.filled or volume)),
                "price": Decimal(str(status.avgFillPrice or 0)),
                "comment": status.status,
            }

        try:
            return self._call(_send(), timeout=30)
        except Exception as e:
            logger.error("ib_order_send_error", symbol=symbol, error=str(e))
            return {"retcode": -1, "comment": str(e), "order": 0}

    def close_position(self, position) -> dict:
        """Flatten a position by submitting the opposite market order."""
        if not self.health_check():
            return {"success": False, "error": "IB not connected"}

        con_id = getattr(position, "ticket", None) or int(position)

        async def _close():
            from ib_async import MarketOrder

            for p in (self._ib.positions(self.account) if self.account else self._ib.positions()):
                if int(p.contract.conId) != int(con_id):
                    continue
                qty = float(p.position)
                if qty == 0:
                    return {"success": False, "error": "position already flat"}
                trade = self._ib.placeOrder(
                    p.contract, MarketOrder("SELL" if qty > 0 else "BUY", abs(qty))
                )
                for _ in range(50):
                    await asyncio.sleep(0.1)
                    if trade.orderStatus.status in ("Filled", "Submitted", "Cancelled"):
                        break
                return {"success": True, "status": trade.orderStatus.status, "ticket": con_id}
            return {"success": False, "error": f"no open IB position with conId {con_id}"}

        try:
            return self._call(_close(), timeout=30)
        except Exception as e:
            logger.error("ib_close_position_error", error=str(e))
            return {"success": False, "error": str(e)}
