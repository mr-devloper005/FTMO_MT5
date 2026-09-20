"""Resolving a provider key to a provider instance.

Yahoo is self-contained and always available. The broker providers only exist
when a live gateway has been registered by the process that owns it — the hub
does this at startup — so this module never imports or constructs a gateway
itself.
"""

__all__ = ["get_provider", "list_providers", "register_gateway_provider"]

from typing import Any

from ftmo_mt5.marketdata.base import DataUnavailable, MarketDataProvider, ProviderCapabilities
from ftmo_mt5.marketdata.gateway import BrokerGatewayDataProvider
from ftmo_mt5.marketdata.yahoo import YahooHistoricalDataProvider

_yahoo = YahooHistoricalDataProvider()

# Broker-backed providers, registered by whoever owns the gateway objects.
_gateways: dict[str, BrokerGatewayDataProvider] = {}


def register_gateway_provider(key: str, gateway: Any, label: str = "") -> None:
    """Expose an already-connected broker gateway as a data provider."""
    _gateways[key] = BrokerGatewayDataProvider(gateway, key=key, label=label)


def get_provider(key: str) -> MarketDataProvider:
    """Provider for `key`, or DataUnavailable naming what is registered."""
    name = (key or "yahoo").strip().lower()
    if name in ("yahoo", "yf"):
        return _yahoo
    provider = _gateways.get(name)
    if provider is None:
        known = ", ".join(["yahoo", *sorted(_gateways)])
        raise DataUnavailable(f"Unknown data source '{key}'. Available: {known}.")
    return provider


def list_providers() -> list[ProviderCapabilities]:
    """Everything currently selectable, for the panel's data-source dropdown."""
    return [_yahoo.capabilities(), *(p.capabilities() for p in _gateways.values())]
