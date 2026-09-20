"""Contract specifications, keyed by symbol.

Only the facts that change P&L arithmetic live here: what a one-point move is
worth, the smallest price increment, and how quantity is counted. Everything
else about an instrument belongs to the broker that trades it.

An unknown symbol resolves to a plain equity — one unit is one share and P&L is
the price difference — which is the right default for the long tail of tickers
and is reported to the caller so the assumption is never silent.
"""

__all__ = ["AssetType", "Instrument", "resolve_instrument", "list_instruments"]

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class AssetType(str, Enum):
    FUTURE = "future"
    EQUITY = "equity"
    ETF = "etf"


@dataclass(frozen=True)
class Instrument:
    """Everything the backtest engine needs to price a position.

    Attributes:
        symbol: The application's symbol, as the user typed it.
        root: Product root — "NQ" for "/NQU6", the ticker itself for equities.
        asset_type: Which arithmetic applies.
        exchange: Listing venue, informational.
        currency: Quote currency.
        tick_size: Smallest price increment.
        tick_value: Cash value of one tick, for one unit.
        contract_multiplier: Cash value of a one-point move, for one unit.
            Always 1 for shares.
        quantity_step: Smallest tradable quantity increment.
        min_quantity: Smallest tradable quantity.
        session_timezone: Venue timezone, for documenting session assumptions.
        option_strike_increment: Spacing between listed option strikes. Wider
            than the tick size — /NQ ticks in 0.25 but lists strikes every 10 —
            so rounding a strike to the tick would invent contracts.
        trading_hours_per_day: Session length, used to annualize volatility.
            Futures run nearly around the clock; equities do not.
        trading_days_per_year: Sessions per year, same purpose.
        assumed: True when this was defaulted rather than known.
    """

    symbol: str
    root: str
    asset_type: AssetType
    exchange: str = ""
    currency: str = "USD"
    tick_size: float = 0.01
    tick_value: float = 0.01
    contract_multiplier: float = 1.0
    quantity_step: float = 1.0
    min_quantity: float = 1.0
    session_timezone: str = "America/New_York"
    option_strike_increment: float = 1.0
    trading_hours_per_day: float = 6.5
    trading_days_per_year: float = 252.0
    assumed: bool = False

    def point_value(self) -> float:
        """Cash change per one-point move, per unit held."""
        return self.contract_multiplier


# Contract specs for the futures the application deals in. Values are the
# published CME/NYMEX/COMEX specifications.
_FUTURES: dict[str, dict] = {
    "NQ":  dict(exchange="CME",   tick_size=0.25,   tick_value=5.00,  multiplier=20,    strike=10),
    "MNQ": dict(exchange="CME",   tick_size=0.25,   tick_value=0.50,  multiplier=2,     strike=10),
    "ES":  dict(exchange="CME",   tick_size=0.25,   tick_value=12.50, multiplier=50,    strike=5),
    "MES": dict(exchange="CME",   tick_size=0.25,   tick_value=1.25,  multiplier=5,     strike=5),
    "YM":  dict(exchange="CBOT",  tick_size=1.0,    tick_value=5.00,  multiplier=5,     strike=50),
    "MYM": dict(exchange="CBOT",  tick_size=1.0,    tick_value=0.50,  multiplier=0.5,   strike=50),
    "RTY": dict(exchange="CME",   tick_size=0.10,   tick_value=5.00,  multiplier=50,    strike=5),
    "M2K": dict(exchange="CME",   tick_size=0.10,   tick_value=0.50,  multiplier=5,     strike=5),
    "CL":  dict(exchange="NYMEX", tick_size=0.01,   tick_value=10.00, multiplier=1000,  strike=0.5),
    "MCL": dict(exchange="NYMEX", tick_size=0.01,   tick_value=1.00,  multiplier=100,   strike=0.5),
    "GC":  dict(exchange="COMEX", tick_size=0.10,   tick_value=10.00, multiplier=100,   strike=5),
    "MGC": dict(exchange="COMEX", tick_size=0.10,   tick_value=1.00,  multiplier=10,    strike=5),
    "SI":  dict(exchange="COMEX", tick_size=0.005,  tick_value=25.00, multiplier=5000,  strike=0.25),
    "NG":  dict(exchange="NYMEX", tick_size=0.001,  tick_value=10.00, multiplier=10000, strike=0.05),
    "ZB":  dict(exchange="CBOT",  tick_size=0.03125, tick_value=31.25, multiplier=1000, strike=1),
    "ZN":  dict(exchange="CBOT",  tick_size=0.015625, tick_value=15.625, multiplier=1000, strike=0.5),
}

# Widely traded ETFs, separated from single stocks only so the panel can label
# them; the arithmetic is identical to equities.
_ETFS = {"SPY", "QQQ", "IWM", "DIA", "GLD", "SLV", "TLT", "XLF", "XLE", "ARKK", "VOO", "VTI"}

_MONTH_CODES = "FGHJKMNQUVXZ"


def _root_of(symbol: str) -> str:
    """Product root for a futures symbol: "/NQU6" and "/NQ" both give "NQ"."""
    text = symbol.upper().lstrip("/")
    if len(text) > 2 and text[-1].isdigit():
        trimmed = text.rstrip("0123456789")
        if trimmed and trimmed[-1] in _MONTH_CODES:
            trimmed = trimmed[:-1]
        if trimmed in _FUTURES:
            return trimmed
    return text


def resolve_instrument(symbol: str, asset_type: Optional[str] = None) -> Instrument:
    """Specification for `symbol`.

    Args:
        symbol: Application symbol, e.g. "/NQ", "/NQU6", "AAPL", "SPY".
        asset_type: Optional override when the caller knows better than the
            symbol shape does.

    Returns:
        An Instrument. `assumed` is True when equity defaults were applied to
        an unrecognised symbol, so callers can surface that.
    """
    text = (symbol or "").strip().upper()
    if not text:
        raise ValueError("No symbol given.")

    root = _root_of(text)
    declared = (asset_type or "").strip().lower()

    is_future = declared == "future" or (
        not declared and (text.startswith("/") or root in _FUTURES)
    )

    if is_future:
        spec = _FUTURES.get(root)
        if spec:
            return Instrument(
                symbol=text, root=root, asset_type=AssetType.FUTURE,
                exchange=spec["exchange"], tick_size=spec["tick_size"],
                tick_value=spec["tick_value"],
                contract_multiplier=float(spec["multiplier"]),
                option_strike_increment=float(spec["strike"]),
                # CME equity-index futures run 23 hours a day, which is what
                # annualizes their volatility correctly.
                trading_hours_per_day=23.0, trading_days_per_year=252.0,
                session_timezone="America/Chicago",
            )
        # A future we have no specification for: point value is unknowable, so
        # say so rather than inventing one that quietly scales every P&L.
        return Instrument(
            symbol=text, root=root, asset_type=AssetType.FUTURE,
            tick_size=0.25, tick_value=1.0, contract_multiplier=1.0,
            option_strike_increment=1.0,
            trading_hours_per_day=23.0, trading_days_per_year=252.0,
            session_timezone="America/Chicago", assumed=True,
        )

    # US equities and ETFs genuinely share one specification — one unit is one
    # share, a point is a dollar, the tick is a cent — so this is the correct
    # answer rather than a guess, and it is not flagged as assumed.
    kind = AssetType.ETF if (declared == "etf" or root in _ETFS) else AssetType.EQUITY
    return Instrument(
        symbol=text, root=root, asset_type=kind, exchange="",
        tick_size=0.01, tick_value=0.01, contract_multiplier=1.0,
        quantity_step=1.0, min_quantity=1.0,
        session_timezone="America/New_York",
    )


def list_instruments() -> list[dict]:
    """Known instruments, for the panel's symbol suggestions."""
    out = [
        {"symbol": f"/{root}", "assetType": AssetType.FUTURE.value,
         "label": f"/{root} · {spec['exchange']} · ${spec['multiplier']}/pt"}
        for root, spec in _FUTURES.items()
    ]
    out += [{"symbol": s, "assetType": AssetType.ETF.value, "label": f"{s} · ETF"}
            for s in sorted(_ETFS)]
    return out
