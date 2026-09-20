"""The interface every broker gateway must satisfy.

Two very different backends sit behind this: MetaTrader 5 (reached over HTTP
through the bridge running in the Windows VM) and Interactive Brokers (spoken
natively from the Mac). Anything above this layer — the agents, the risk
engine, the panel — works against the interface and stays broker-agnostic.

Prices and money are Decimal throughout. Never let a float reach an order.
"""

__all__ = [
    "BrokerGateway",
    "BrokerDescriptor",
]

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional, Protocol, runtime_checkable

from ftmo_mt5.core.models import AccountState, PositionInfo


@dataclass(frozen=True)
class BrokerDescriptor:
    """Identity and reachability of one broker connection.

    Attributes:
        key: Short stable id used in API routes ("mt5", "ib").
        label: Human-readable name for the panel.
        connected: Whether the gateway currently has a usable session.
        detail: Free-form status line — account number when up, the reason
            it is down otherwise.
    """

    key: str
    label: str
    connected: bool
    detail: str = ""


@runtime_checkable
class BrokerGateway(Protocol):
    """Minimum surface a broker must expose to be tradable by this system."""

    def initialize(self) -> bool:
        """Open the session. Returns True once the broker is usable."""

    def shutdown(self) -> None:
        """Close the session; safe to call when never connected."""

    def health_check(self) -> bool:
        """Cheap liveness probe. Must not raise."""

    def describe(self) -> BrokerDescriptor:
        """Identity plus current status, for the management panel."""

    def get_account_info(self) -> AccountState:
        """Current balance/equity snapshot."""

    def get_positions(self, magic: Optional[int] = None) -> list[PositionInfo]:
        """Open positions, optionally narrowed to one magic/strategy id."""

    def get_tick(self, symbol: str) -> Optional[dict]:
        """Latest bid/ask for a symbol, or None when unavailable."""

    def get_ohlcv(self, symbol: str, timeframe: str = "M15", count: int = 200) -> list[dict]:
        """Candles oldest-first with Decimal prices."""

    def order_send(self, request: dict) -> dict:
        """Submit an order. Returns a dict carrying at least `retcode`."""

    def close_position(self, position) -> dict:
        """Flatten one position, by object or ticket."""
