"""Thread-safe wrapper around MT5 Python API.

Provides synchronized access to MetaTrader5 API with automatic reconnection,
health checking, and comprehensive logging. All MT5 API calls are serialized
behind a threading.Lock to ensure thread safety.
"""

__all__ = [
    "MT5Gateway",
]

import logging
import threading
import time
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

import structlog
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode
from pydantic import SecretStr

from nehanth.core.exceptions import MT5ConnectionError, MT5ExecutionError
from nehanth.core.models import AccountState, PositionInfo

logger = structlog.get_logger(__name__)
otel_tracer = trace.get_tracer(__name__)

# Attempt to import MT5; fall back to mock on non-Windows platforms
try:
    import MetaTrader5 as mt5
    HAS_REAL_MT5 = True
except ImportError:
    HAS_REAL_MT5 = False
    logger.warning("MetaTrader5 not available; using mock implementation for testing")


class MockMT5:
    """Mock MT5 implementation for testing on non-Windows platforms."""

    ACCOUNT_BALANCE = 10000.0
    ACCOUNT_EQUITY = 10000.0
    ACCOUNT_MARGIN = 0.0
    ACCOUNT_FREE_MARGIN = 10000.0

    def __init__(self):
        self.is_connected = False
        self.positions = {}

    def initialize(self, *args, **kwargs):
        self.is_connected = True
        return True

    def shutdown(self):
        self.is_connected = False

    def terminal_info(self):
        if not self.is_connected:
            return None
        return type('obj', (object,), {'connected': self.is_connected})()

    def account_info(self):
        if not self.is_connected:
            return None
        return type('obj', (object,), {
            'balance': self.ACCOUNT_BALANCE,
            'equity': self.ACCOUNT_EQUITY,
            'margin': self.ACCOUNT_MARGIN,
            'free_margin': self.ACCOUNT_FREE_MARGIN,
        })()

    def symbol_info(self, symbol):
        if not self.is_connected:
            return None
        return type('obj', (object,), {
            'name': symbol,
            'visible': True,
        })()

    def symbol_info_tick(self, symbol):
        if not self.is_connected:
            return None
        return type('obj', (object,), {
            'bid': 1.1000,
            'ask': 1.1001,
            'last': 1.1000,
            'time': int(time.time()),
        })()

    def positions_get(self, symbol=None, group=None):
        if not self.is_connected:
            return None
        return []

    def order_send(self, request):
        if not self.is_connected:
            return None
        return type('obj', (object,), {
            'retcode': 10000,  # TRADE_RETCODE_DONE (success)
            'order': 12345,
            'volume': 1.0,
            'price': 1.1000,
            'comment': 'mock order',
        })()

    def history_deals_get(self, position_id):
        if not self.is_connected:
            return None
        return []

    def history_orders_get(self, ticket):
        if not self.is_connected:
            return None
        return []


class MT5Gateway:
    """Thread-safe wrapper around MT5 Python API.

    All MT5 API calls are serialized behind a threading.Lock to ensure
    thread safety. Provides automatic reconnection with exponential backoff
    and comprehensive health checking.
    """

    def __init__(
        self,
        login: int,
        server: str,
        password: str | SecretStr,
        max_reconnect_attempts: int = 5,
        initial_backoff_s: float = 1.0,
        max_backoff_s: float = 60.0,
    ):
        """Initialize MT5Gateway.

        Args:
            login: MT5 account login number.
            server: MT5 server name.
            password: MT5 password (will be stored as SecretStr for security).
            max_reconnect_attempts: Maximum reconnection attempts.
            initial_backoff_s: Initial backoff duration in seconds.
            max_backoff_s: Maximum backoff duration in seconds.
        """
        self._login = login
        self._server = server
        # Store password as SecretStr to prevent accidental logging/display
        self._password = SecretStr(password) if not isinstance(password, SecretStr) else password
        self._max_reconnect_attempts = max_reconnect_attempts
        self._initial_backoff_s = initial_backoff_s
        self._max_backoff_s = max_backoff_s

        self._lock = threading.Lock()
        self._mt5 = mt5 if HAS_REAL_MT5 else MockMT5()
        self._is_initialized = False
        self._last_health_check = datetime.now()

    @trace.trace_method
    def initialize(self) -> bool:
        """Initialize MT5 connection with retry logic.

        Returns:
            True if successfully initialized, False otherwise.

        Raises:
            MT5ConnectionError: If initialization fails after max attempts.
        """
        with self._lock:
            if self._is_initialized:
                return True

            for attempt in range(self._max_reconnect_attempts):
                try:
                    logger.info(
                        "mt5_initialize_attempt",
                        attempt=attempt,
                        login=self._login,
                        server=self._server,
                    )

                    if not self._mt5.initialize(login=self._login, server=self._server, password=self._password.get_secret_value()):
                        raise MT5ConnectionError(
                            "MT5 initialization failed",
                            login=self._login,
                            server=self._server,
                            attempt=attempt,
                        )

                    # Verify connection
                    if not self._is_connected():
                        raise MT5ConnectionError(
                            "MT5 not connected after initialization",
                            login=self._login,
                        )

                    self._is_initialized = True
                    logger.info("mt5_initialized_success", login=self._login, server=self._server)
                    return True

                except MT5ConnectionError as e:
                    logger.warning("mt5_initialization_failed", error=str(e), attempt=attempt)
                    if attempt < self._max_reconnect_attempts - 1:
                        backoff = min(
                            self._initial_backoff_s * (2 ** attempt),
                            self._max_backoff_s,
                        )
                        time.sleep(backoff)
                    continue

            raise MT5ConnectionError(
                f"Failed to initialize MT5 after {self._max_reconnect_attempts} attempts",
                login=self._login,
                server=self._server,
            )

    @trace.trace_method
    def shutdown(self) -> None:
        """Shutdown MT5 connection."""
        with self._lock:
            if self._is_initialized:
                try:
                    self._mt5.shutdown()
                    self._is_initialized = False
                    logger.info("mt5_shutdown_success")
                except Exception as e:
                    logger.error("mt5_shutdown_error", error=str(e))

    def _is_connected(self) -> bool:
        """Check if MT5 is connected.

        Returns:
            True if connected, False otherwise.
        """
        try:
            info = self._mt5.terminal_info()
            return info is not None and getattr(info, 'connected', False)
        except Exception:
            return False

    @trace.trace_method
    def _reconnect(self) -> bool:
        """Reconnect to MT5 with exponential backoff.

        Returns:
            True if reconnection successful, False otherwise.
        """
        logger.warning("mt5_reconnect_attempt")
        for attempt in range(self._max_reconnect_attempts):
            try:
                self._mt5.shutdown()
                time.sleep(min(self._initial_backoff_s * (2 ** attempt), self._max_backoff_s))

                if self._mt5.initialize(
                    login=self._login,
                    server=self._server,
                    password=self._password.get_secret_value(),
                ):
                    if self._is_connected():
                        logger.info("mt5_reconnect_success", attempt=attempt)
                        return True

            except Exception as e:
                logger.error("mt5_reconnect_error", error=str(e), attempt=attempt)
                continue

        logger.error("mt5_reconnect_failed", max_attempts=self._max_reconnect_attempts)
        return False

    def health_check(self) -> bool:
        """Check MT5 connection health.

        Returns:
            True if healthy, False otherwise.
        """
        with self._lock:
            is_healthy = self._is_connected()
            self._last_health_check = datetime.now()

            if not is_healthy:
                logger.warning("mt5_health_check_failed")
                return False

            logger.debug("mt5_health_check_success")
            return True

    @trace.trace_method
    def get_tick(self, symbol: str) -> Optional[dict]:
        """Get current tick data for symbol.

        Args:
            symbol: Trading symbol.

        Returns:
            Tick data or None if retrieval fails.
        """
        with self._lock:
            if not self._is_connected():
                if not self._reconnect():
                    raise MT5ConnectionError("Failed to reconnect for tick data", symbol=symbol)

            try:
                tick = self._mt5.symbol_info_tick(symbol)
                if tick is None:
                    return None

                return {
                    'bid': Decimal(str(tick.bid)),
                    'ask': Decimal(str(tick.ask)),
                    'last': Decimal(str(tick.last)),
                    'time': int(tick.time),
                }
            except Exception as e:
                logger.error("mt5_get_tick_error", symbol=symbol, error=str(e))
                raise MT5ExecutionError(f"Failed to get tick for {symbol}", retcode=-1, symbol=symbol)

    @trace.trace_method
    def get_account_info(self) -> AccountState:
        """Get current account state.

        Returns:
            AccountState snapshot.

        Raises:
            MT5ConnectionError: If unable to retrieve account info.
        """
        with self._lock:
            if not self._is_connected():
                if not self._reconnect():
                    raise MT5ConnectionError("Failed to reconnect for account info")

            try:
                info = self._mt5.account_info()
                if info is None:
                    raise MT5ConnectionError("account_info returned None")

                return AccountState(
                    balance=Decimal(str(info.balance)),
                    equity=Decimal(str(info.equity)),
                    margin=Decimal(str(info.margin)),
                    free_margin=Decimal(str(info.free_margin)),
                    initial_capital=Decimal(str(info.balance)),  # Placeholder
                    timestamp=datetime.utcnow(),
                )
            except Exception as e:
                logger.error("mt5_get_account_info_error", error=str(e))
                raise MT5ConnectionError("Failed to get account info", error=str(e))

    @trace.trace_method
    def get_positions(self, magic: Optional[int] = None) -> list[PositionInfo]:
        """Get open positions, optionally filtered by magic number.

        Args:
            magic: Optional magic number filter.

        Returns:
            List of PositionInfo objects.

        Raises:
            MT5ExecutionError: If retrieval fails.
        """
        with self._lock:
            if not self._is_connected():
                if not self._reconnect():
                    raise MT5ConnectionError("Failed to reconnect for positions")

            try:
                positions = self._mt5.positions_get()
                if positions is None:
                    return []

                result = []
                for pos in positions:
                    if magic is not None and getattr(pos, 'magic', None) != magic:
                        continue

                    result.append(
                        PositionInfo(
                            ticket=int(pos.ticket),
                            symbol=str(pos.symbol),
                            type='LONG' if pos.type == 0 else 'SHORT',
                            volume=Decimal(str(pos.volume)),
                            price_open=Decimal(str(pos.price_open)),
                            sl=Decimal(str(pos.sl)) if pos.sl else None,
                            tp=Decimal(str(pos.tp)) if pos.tp else None,
                            profit=Decimal(str(pos.profit)),
                            magic=int(pos.magic) if pos.magic else None,
                            comment=str(pos.comment) if pos.comment else "",
                        )
                    )

                logger.info("mt5_get_positions_success", count=len(result))
                return result

            except Exception as e:
                logger.error("mt5_get_positions_error", error=str(e), magic=magic)
                raise MT5ExecutionError(f"Failed to get positions", retcode=-1, magic=magic)

    @trace.trace_method
    def get_symbol_info(self, symbol: str) -> Optional[dict]:
        """Get symbol information.

        Args:
            symbol: Trading symbol.

        Returns:
            Symbol info dict or None if not found.
        """
        with self._lock:
            if not self._is_connected():
                if not self._reconnect():
                    raise MT5ConnectionError("Failed to reconnect for symbol info", symbol=symbol)

            try:
                info = self._mt5.symbol_info(symbol)
                if info is None:
                    return None

                return {
                    'name': str(info.name),
                    'visible': bool(info.visible),
                }
            except Exception as e:
                logger.error("mt5_get_symbol_info_error", symbol=symbol, error=str(e))
                return None

    @trace.trace_method
    def order_send(self, request: dict) -> dict:
        """Send order to MT5.

        Args:
            request: MT5 order request dict.

        Returns:
            MT5 order result dict.

        Raises:
            MT5ExecutionError: If order submission fails.
        """
        with self._lock:
            if not self._is_connected():
                if not self._reconnect():
                    raise MT5ConnectionError("Failed to reconnect for order submission")

            try:
                result = self._mt5.order_send(request)
                if result is None:
                    raise MT5ExecutionError("order_send returned None", retcode=-1)

                return {
                    'retcode': int(result.retcode),
                    'order': int(result.order) if hasattr(result, 'order') else 0,
                    'volume': Decimal(str(result.volume)) if hasattr(result, 'volume') else Decimal("0"),
                    'price': Decimal(str(result.price)) if hasattr(result, 'price') else Decimal("0"),
                    'comment': str(result.comment) if hasattr(result, 'comment') else "",
                }
            except MT5ExecutionError:
                raise
            except Exception as e:
                logger.error("mt5_order_send_error", error=str(e))
                raise MT5ExecutionError("Failed to send order", retcode=-1, error=str(e))

    @trace.trace_method
    def history_deals_get(self, position_id: int) -> list[dict]:
        """Get deal history for position.

        Args:
            position_id: Position ticket number.

        Returns:
            List of deal records.
        """
        with self._lock:
            if not self._is_connected():
                if not self._reconnect():
                    raise MT5ConnectionError("Failed to reconnect for deal history", position_id=position_id)

            try:
                deals = self._mt5.history_deals_get(position_id)
                if deals is None:
                    return []

                return [
                    {
                        'ticket': int(deal.ticket),
                        'order': int(deal.order),
                        'volume': Decimal(str(deal.volume)),
                        'price': Decimal(str(deal.price)),
                        'commission': Decimal(str(deal.commission)) if hasattr(deal, 'commission') else Decimal("0"),
                    }
                    for deal in deals
                ]
            except Exception as e:
                logger.error("mt5_history_deals_get_error", position_id=position_id, error=str(e))
                return []

    @trace.trace_method
    def history_orders_get(self, ticket: int) -> list[dict]:
        """Get order history.

        Args:
            ticket: Order ticket number.

        Returns:
            List of order records.
        """
        with self._lock:
            if not self._is_connected():
                if not self._reconnect():
                    raise MT5ConnectionError("Failed to reconnect for order history", ticket=ticket)

            try:
                orders = self._mt5.history_orders_get(ticket)
                if orders is None:
                    return []

                return [
                    {
                        'ticket': int(order.ticket),
                        'symbol': str(order.symbol),
                        'type': str(order.type),
                        'volume': Decimal(str(order.volume)),
                        'price': Decimal(str(order.price)),
                    }
                    for order in orders
                ]
            except Exception as e:
                logger.error("mt5_history_orders_get_error", ticket=ticket, error=str(e))
                return []
