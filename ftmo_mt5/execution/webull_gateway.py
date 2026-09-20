"""Webull gateway, exposing the same surface as the MT5 and IB gateways.

Speaks Webull's official **OpenAPI** (the `webull-openapi-python-sdk` package) —
not the reverse-engineered app endpoints older projects used. Requests are
signed with an App Key / App Secret pair issued from Webull's developer portal;
there is no password anywhere in this flow, and none belongs here.

Two environments:
    api.sandbox.webull.com   shared test accounts, no application needed
    (production host)        issued with your approved App Key

Sandbox is the default. Pointing at production must be a deliberate edit, not
something that happens because a default was wrong.

Instrument scope differs from the other brokers: MT5 gives forex and CFDs,
Webull gives US equities, ETFs, options and crypto. The panel keeps a separate
watchlist per broker for exactly this reason.
"""

__all__ = [
    "WebullGateway",
]

import logging
import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Optional

import structlog

from ftmo_mt5.core.exceptions import MT5ConnectionError
from ftmo_mt5.core.models import AccountState, PositionInfo
from ftmo_mt5.execution.broker_base import BrokerDescriptor

logger = structlog.get_logger(__name__)

WEBULL_APP_KEY = os.getenv("WEBULL_APP_KEY", "")
WEBULL_APP_SECRET = os.getenv("WEBULL_APP_SECRET", "")
WEBULL_REGION = os.getenv("WEBULL_REGION", "us")
WEBULL_ACCOUNT_ID = os.getenv("WEBULL_ACCOUNT_ID", "")
WEBULL_ENDPOINT = os.getenv("WEBULL_ENDPOINT", "api.sandbox.webull.com")

# MT5-style timeframe -> Webull Timespan name. The SDK's enum happens to use
# the same M1/M5/M15/M60/D vocabulary, so this stays a thin rename.
_TIMESPAN = {
    "M1": "M1", "M5": "M5", "M15": "M15", "M30": "M30",
    "H1": "M60", "H4": "M240", "D1": "D",
}

# Webull needs the instrument category alongside the symbol.
_CRYPTO = {"BTCUSD", "ETHUSD", "BTC", "ETH", "SOLUSD", "DOGEUSD"}

DEFAULT_SYMBOLS = ["AAPL", "TSLA", "NVDA", "MSFT", "AMZN", "SPY"]


def _dec(value: Any) -> Decimal:
    """Best-effort Decimal. Broker payloads mix strings, floats and nulls."""
    if value in (None, "", "-"):
        return Decimal("0")
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def _first(payload: dict, *keys: str) -> Any:
    """Return the first key present and non-empty.

    Webull's field names vary between account types and API versions, so read
    defensively rather than pinning one spelling and failing silently.
    """
    for key in keys:
        if key in payload and payload[key] not in (None, "", []):
            return payload[key]
    return None


class WebullGateway:
    """Synchronous Webull OpenAPI gateway."""

    def __init__(
        self,
        app_key: Optional[str] = None,
        app_secret: Optional[str] = None,
        region: Optional[str] = None,
        account_id: Optional[str] = None,
        endpoint: Optional[str] = None,
    ) -> None:
        self.app_key = app_key or WEBULL_APP_KEY
        self.app_secret = app_secret or WEBULL_APP_SECRET
        self.region = (region or WEBULL_REGION).lower()
        self.account_id = account_id or WEBULL_ACCOUNT_ID
        self.endpoint = endpoint or WEBULL_ENDPOINT

        self._api: Any = None
        self._trade: Any = None
        self._data: Any = None
        self._connected = False
        self._last_error = "not connected"

        logger.info(
            "webull_gateway_created",
            region=self.region,
            endpoint=self.endpoint,
            mode="sandbox" if "sandbox" in self.endpoint else "PRODUCTION",
            has_credentials=bool(self.app_key and self.app_secret),
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def initialize(self) -> bool:
        if not (self.app_key and self.app_secret):
            self._last_error = (
                "WEBULL_APP_KEY / WEBULL_APP_SECRET not set — create them in "
                "Webull's OpenAPI Management portal and put them in .env"
            )
            raise MT5ConnectionError(self._last_error)

        try:
            from webull.core.client import ApiClient
            from webull.data.data_client import DataClient
            from webull.trade.trade_client import TradeClient
        except ImportError as e:
            self._last_error = "webull-openapi-python-sdk not installed"
            raise MT5ConnectionError(self._last_error, error=str(e))

        api = ApiClient(self.app_key, self.app_secret, self.region)
        api.add_endpoint(self.region, self.endpoint)

        # Claim the logger slots before the clients do, or the SDK attaches its
        # own handler to stdout and floods the bot's structured log stream.
        api.set_file_logger(
            path=str(Path(__file__).resolve().parents[2] / "logs" / "webull_sdk.log"),
            log_level=logging.WARNING,
        )

        self._api = api
        self._trade = TradeClient(api)
        self._data = DataClient(api)

        try:
            res = self._trade.account_v2.get_account_list()
        except Exception as e:
            self._connected = False
            self._last_error = f"account list call failed: {e}"
            raise MT5ConnectionError(self._last_error, endpoint=self.endpoint)

        if res.status_code != 200:
            self._connected = False
            self._last_error = f"HTTP {res.status_code} from {self.endpoint}: {res.text[:180]}"
            raise MT5ConnectionError(self._last_error)

        accounts = res.json()
        if isinstance(accounts, dict):
            accounts = accounts.get("data", accounts.get("accounts", []))

        if not self.account_id and accounts:
            first = accounts[0]
            self.account_id = str(_first(first, "account_id", "accountId", "id") or "")

        if not self.account_id:
            self._connected = False
            self._last_error = "connected, but the API returned no tradable account"
            raise MT5ConnectionError(self._last_error)

        self._connected = True
        self._last_error = ""
        logger.info(
            "webull_connected",
            account_id=self.account_id,
            accounts=len(accounts) if accounts else 0,
            endpoint=self.endpoint,
        )
        return True

    def shutdown(self) -> None:
        self._connected = False
        logger.info("webull_shutdown")

    def health_check(self) -> bool:
        if not (self._connected and self._trade):
            return False
        try:
            return self._trade.account_v2.get_account_list().status_code == 200
        except Exception:
            return False

    def describe(self) -> BrokerDescriptor:
        live = self.health_check()
        mode = "sandbox" if "sandbox" in self.endpoint else "live"
        return BrokerDescriptor(
            key="webull",
            label="Webull",
            connected=live,
            detail=(
                f"account {self.account_id} ({mode}, {self.region.upper()})"
                if live else (self._last_error or "not connected")
            ),
        )

    # ------------------------------------------------------------------
    # Symbols
    # ------------------------------------------------------------------

    @property
    def default_symbols(self) -> list[str]:
        """Watchlist that actually exists on this broker (US equities)."""
        return DEFAULT_SYMBOLS

    def _category(self, symbol: str) -> str:
        upper = symbol.upper()
        if upper in _CRYPTO:
            return "US_CRYPTO"
        return "HK_STOCK" if self.region == "hk" else "US_STOCK"

    def get_symbol_info(self, symbol: str) -> Optional[dict]:
        if not self._connected:
            return None
        try:
            res = self._data.instrument.get_instrument(
                symbols=symbol, category=self._category(symbol)
            )
            if res.status_code != 200:
                return None
            data = res.json()
            rows = data.get("data", data) if isinstance(data, dict) else data
            if not rows:
                return None
            row = rows[0]
            return {
                "name": symbol,
                "visible": True,
                "instrument_id": _first(row, "instrument_id", "instrumentId"),
                "category": self._category(symbol),
            }
        except Exception as e:
            logger.error("webull_symbol_info_error", symbol=symbol, error=str(e))
            return None

    # ------------------------------------------------------------------
    # Market data
    # ------------------------------------------------------------------

    def get_tick(self, symbol: str) -> Optional[dict]:
        if not self._connected:
            return None
        try:
            res = self._data.market_data.get_quotes(symbol, self._category(symbol))
            if res.status_code != 200:
                return None
            payload = res.json()
            if isinstance(payload, dict) and "data" in payload:
                payload = payload["data"]
            if isinstance(payload, list):
                payload = payload[0] if payload else {}

            bid = _dec(_first(payload, "bid_price", "bidPrice", "bid"))
            ask = _dec(_first(payload, "ask_price", "askPrice", "ask"))
            last = _dec(_first(payload, "last_price", "lastPrice", "close", "price"))

            # Outside market hours a quote often has no book, only a last
            # price. Fall back to it so the panel shows something truthful.
            if bid <= 0 or ask <= 0:
                if last <= 0:
                    return None
                bid = ask = last

            return {
                "bid": bid,
                "ask": ask,
                "last": last or bid,
                "time": int(datetime.now(timezone.utc).timestamp()),
            }
        except Exception as e:
            logger.error("webull_get_tick_error", symbol=symbol, error=str(e))
            return None

    def get_ohlcv(self, symbol: str, timeframe: str = "M15", count: int = 200) -> list[dict]:
        if not self._connected:
            return []
        try:
            res = self._data.market_data.get_history_bar(
                symbol=symbol,
                category=self._category(symbol),
                timespan=_TIMESPAN.get(timeframe, "M15"),
                count=str(count),
            )
            if res.status_code != 200:
                logger.warning("webull_ohlcv_http", symbol=symbol, status=res.status_code)
                return []

            payload = res.json()
            if isinstance(payload, dict):
                payload = payload.get("data", payload.get("bars", []))
            if isinstance(payload, dict):
                payload = payload.get("bars", [])

            bars = []
            for b in payload or []:
                ts = _first(b, "trade_time", "tradeTime", "timestamp", "time") or 0
                try:
                    ts_ms = int(ts) if str(ts).isdigit() else int(
                        datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp() * 1000
                    )
                except Exception:
                    ts_ms = 0
                if ts_ms and ts_ms < 10_000_000_000:      # seconds, not millis
                    ts_ms *= 1000
                bars.append({
                    "time": ts_ms,
                    "open": _dec(_first(b, "open", "openPrice")),
                    "high": _dec(_first(b, "high", "highPrice")),
                    "low": _dec(_first(b, "low", "lowPrice")),
                    "close": _dec(_first(b, "close", "closePrice")),
                    "volume": int(_dec(_first(b, "volume", "vol"))),
                })

            # Webull returns newest-first; the rest of the system reads oldest-first.
            if len(bars) > 1 and bars[0]["time"] > bars[-1]["time"]:
                bars.reverse()
            return bars
        except Exception as e:
            logger.error("webull_get_ohlcv_error", symbol=symbol, error=str(e))
            return []

    # ------------------------------------------------------------------
    # Account and positions
    # ------------------------------------------------------------------

    def get_account_info(self) -> AccountState:
        if not self._connected:
            raise MT5ConnectionError("Webull not connected", detail=self._last_error)

        res = self._trade.account_v2.get_account_balance(self.account_id)
        if res.status_code != 200:
            raise MT5ConnectionError(f"balance HTTP {res.status_code}: {res.text[:180]}")

        payload = res.json()
        if isinstance(payload, dict) and "data" in payload:
            payload = payload["data"]
        if isinstance(payload, list):
            payload = payload[0] if payload else {}

        # Prefer the account-currency block when the API splits by currency.
        assets = payload.get("account_currency_assets") or payload.get("accountCurrencyAssets")
        if isinstance(assets, list) and assets:
            payload = {**payload, **assets[0]}

        equity = _dec(_first(payload, "net_liquidation_value", "netLiquidationValue",
                             "total_asset_value", "totalAssetValue", "net_account_value"))
        cash = _dec(_first(payload, "cash_balance", "cashBalance",
                           "total_cash_value", "settled_funds"))
        buying_power = _dec(_first(payload, "buying_power", "buyingPower",
                                   "day_buying_power", "available_funds"))
        margin = _dec(_first(payload, "margin_used", "marginUsed", "initial_margin"))

        if equity <= 0:
            equity = cash

        return AccountState(
            balance=cash or equity,
            equity=equity,
            margin=margin,
            free_margin=buying_power or cash,
            initial_capital=cash or equity,
            timestamp=datetime.now(timezone.utc),
        )

    def get_positions(self, magic: Optional[int] = None) -> list[PositionInfo]:
        """Open positions. `magic` has no Webull equivalent and is ignored."""
        if not self._connected:
            return []

        try:
            res = self._trade.account_v2.get_account_position(self.account_id)
            if res.status_code != 200:
                logger.warning("webull_positions_http", status=res.status_code)
                return []
            payload = res.json()
        except Exception as e:
            logger.error("webull_get_positions_error", error=str(e))
            return []

        if isinstance(payload, dict):
            payload = _first(payload, "data", "positions", "holdings") or []

        out: list[PositionInfo] = []
        for raw in payload:
            qty = _dec(_first(raw, "quantity", "position", "shares"))
            if qty == 0:
                continue
            symbol = str(_first(raw, "symbol", "ticker") or "UNKNOWN")[:10]
            cost = _dec(_first(raw, "cost_price", "costPrice", "avg_cost", "average_cost"))
            out.append(
                PositionInfo(
                    ticket=int(_dec(_first(raw, "instrument_id", "instrumentId", "position_id") or 0)),
                    symbol=symbol,
                    type="LONG" if qty > 0 else "SHORT",
                    volume=abs(qty),
                    price_open=cost if cost > 0 else Decimal("0.00001"),
                    sl=None,
                    tp=None,
                    profit=_dec(_first(raw, "unrealized_profit_loss", "unrealizedProfitLoss", "pnl")),
                    magic=0,
                    comment="Webull",
                )
            )
        return out

    # ------------------------------------------------------------------
    # Trading
    # ------------------------------------------------------------------

    def order_send(self, request: dict) -> dict:
        """Place an order. Market by default; a non-zero `price` makes it a limit."""
        if not self._connected:
            return {"retcode": -1, "comment": "Webull not connected", "order": 0}

        symbol = str(request.get("symbol", "")).upper()
        quantity = _dec(request.get("volume", 0))
        if quantity <= 0:
            return {"retcode": -1, "comment": "volume must be positive", "order": 0}

        side = "BUY"
        raw_side = request.get("type", request.get("action", "buy"))
        if isinstance(raw_side, int):
            side = "SELL" if raw_side == 1 else "BUY"
        elif isinstance(raw_side, str) and "sell" in raw_side.lower():
            side = "SELL"

        limit_price = _dec(request.get("price", 0))
        client_order_id = uuid.uuid4().hex

        order: dict[str, Any] = {
            "client_order_id": client_order_id,
            "symbol": symbol,
            "instrument_type": "CRYPTO" if self._category(symbol) == "US_CRYPTO" else "EQUITY",
            "market": self.region.upper(),
            "order_type": "LIMIT" if limit_price > 0 else "MARKET",
            "quantity": str(quantity.normalize()),
            "support_trading_session": "CORE",
            "side": side,
            "time_in_force": "DAY",
            "entrust_type": "QTY",
        }
        if limit_price > 0:
            order["limit_price"] = str(limit_price)

        try:
            res = self._trade.order_v2.place_order(self.account_id, [order])
        except Exception as e:
            logger.error("webull_order_send_error", symbol=symbol, error=str(e))
            return {"retcode": -1, "comment": str(e), "order": 0}

        if res.status_code != 200:
            logger.warning("webull_order_rejected", symbol=symbol, status=res.status_code, body=res.text[:200])
            return {"retcode": 10006, "comment": f"HTTP {res.status_code}: {res.text[:160]}", "order": 0}

        body = res.json()
        if isinstance(body, dict) and "data" in body:
            body = body["data"]
        if isinstance(body, list):
            body = body[0] if body else {}

        logger.info("webull_order_placed", symbol=symbol, side=side, quantity=str(quantity),
                    client_order_id=client_order_id)
        return {
            "retcode": 10000,
            "order": _first(body, "order_id", "orderId") or client_order_id,
            "volume": quantity,
            "price": limit_price,
            "comment": str(_first(body, "status", "order_status") or "SUBMITTED"),
        }

    def close_position(self, position) -> dict:
        """Flatten a holding by selling the whole quantity at market."""
        if not self._connected:
            return {"success": False, "error": "Webull not connected"}

        ticket = getattr(position, "ticket", None) or int(position)
        match = next((p for p in self.get_positions() if p.ticket == int(ticket)), None)
        if match is None:
            return {"success": False, "error": f"no open Webull position with id {ticket}"}

        result = self.order_send({
            "symbol": match.symbol,
            "type": "sell" if match.type == "LONG" else "buy",
            "volume": float(match.volume),
        })
        ok = result.get("retcode") == 10000
        return {
            "success": ok,
            "ticket": ticket,
            "order": result.get("order"),
            "error": None if ok else result.get("comment"),
        }
