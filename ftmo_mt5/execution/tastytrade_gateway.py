"""tastytrade gateway — equities, options and futures over their REST API.

Reaches tastytrade directly from the Mac; nothing needs to be installed and no
desktop platform has to be running, which is what separates this from brokers
whose automation lives inside a Windows terminal.

Auth is **OAuth2** — a provider secret plus a refresh token. There is no
password in this flow and none belongs here. Both values come from the
tastytrade developer portal and are read from the environment.

Two environments:
    api.cert.tastyworks.com   sandbox — free, no funding, resets every 24h,
                              and **quotes are always 15 minutes delayed**
    api.tastyworks.com        live money

Sandbox is the default. Trading real money must be a deliberate edit.

Threading note: the tastytrade SDK is asyncio-native while every gateway here
is synchronous and gets called from worker threads, so this class owns a
private event loop on a background thread and marshals each call into it —
the same shape the IB gateway used.
"""

__all__ = [
    "TastytradeGateway",
]

import asyncio
import os
import re
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

TT_SECRET = os.getenv("TT_SECRET", "")
TT_REFRESH = os.getenv("TT_REFRESH", "")
TT_ACCOUNT = os.getenv("TT_ACCOUNT", "")
TT_SANDBOX = os.getenv("TT_SANDBOX", "1") != "0"
TT_CALL_TIMEOUT_S = float(os.getenv("TT_CALL_TIMEOUT_S", "30"))
# Symbol search walks the active-equity list once; cap the walk so a broker
# with a very long list cannot stall the first search indefinitely.
EQUITY_CACHE_MAX_PAGES = int(os.getenv("TT_EQUITY_CACHE_PAGES", "12"))

# MT5-style timeframe -> DXLink candle period.
_CANDLE_PERIOD = {
    "M1": "1m", "M5": "5m", "M15": "15m", "M30": "30m",
    "H1": "1h", "H4": "4h", "D1": "1d",
}

# Equities the panel watches by default. Futures use a leading slash on this
# venue ("/ESZ5"); options are multi-leg and are placed through the SDK's own
# builders rather than this single-leg path.
DEFAULT_SYMBOLS = ["AAPL", "TSLA", "NVDA", "SPY", "QQQ", "MSFT"]

# Futures products whose option chains this venue serves.
FUTURES_ROOTS = {"NQ", "ES", "CL", "GC", "RTY", "YM", "MNQ", "MES", "MCL", "MGC", "ZB", "ZN", "SI", "NG", "6E"}

# A dated contract is the root plus a month letter and a one- or two-digit
# year: NQU6, NQZ26. The month letters are the standard CME set.
_CONTRACT_RE = re.compile(r"^([A-Z0-9]{1,3})([FGHJKMNQUVXZ])(\d{1,2})$")


def _futures_root(symbol: str) -> Optional[str]:
    """Product root for anything naming a future, else None.

    "NQ", "/NQ", "NQU6" and "/NQU6" all name the same product to a trader, but
    only the root is a symbol the option-chain endpoint accepts.
    """
    text = (symbol or "").upper().lstrip("/")
    if not text:
        return None
    if text in FUTURES_ROOTS:
        return text
    match = _CONTRACT_RE.match(text)
    if match and match.group(1) in FUTURES_ROOTS:
        return match.group(1)
    return None


def _dec(value: Any) -> Decimal:
    if value is None or value == "":
        return Decimal("0")
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal("0")


def _pick(obj: Any, *names: str) -> Decimal:
    """First non-empty attribute among `names`, as Decimal.

    Balance payloads differ between cash, margin and futures accounts, so read
    several plausible fields rather than pinning one and silently reporting 0.
    """
    for name in names:
        value = getattr(obj, name, None)
        if value not in (None, ""):
            return _dec(value)
    return Decimal("0")


class TastytradeGateway:
    """Synchronous tastytrade gateway backed by a private asyncio loop."""

    def __init__(
        self,
        provider_secret: Optional[str] = None,
        refresh_token: Optional[str] = None,
        account_number: Optional[str] = None,
        sandbox: Optional[bool] = None,
    ) -> None:
        self.provider_secret = provider_secret or TT_SECRET
        self.refresh_token = refresh_token or TT_REFRESH
        self.account_number = account_number or TT_ACCOUNT
        self.sandbox = TT_SANDBOX if sandbox is None else sandbox

        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._session: Any = None
        self._account: Any = None
        self._closing: Optional[asyncio.Event] = None
        self._connected = False
        self._last_error = "not connected"
        # Populated on first symbol search; the list is large and static.
        self._equity_cache: Optional[list[dict]] = None
        self._futures_cache: Optional[list[dict]] = None
        # Futures quote under a different name on the market-data feed.
        self._streamer_symbols: dict[str, str] = {}
        # Live quotes, kept fresh by a background DXLink subscription.
        self._quote_cache: dict[str, dict] = {}
        self._stream_task: Any = None
        self._streamed: set[str] = set()

        logger.info(
            "tastytrade_gateway_created",
            env="sandbox" if self.sandbox else "LIVE",
            has_credentials=bool(self.provider_secret and self.refresh_token),
        )

    # ------------------------------------------------------------------
    # Event-loop plumbing
    # ------------------------------------------------------------------

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop and self._loop.is_running():
            return self._loop

        ready = threading.Event()

        def _run() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            ready.set()
            loop.run_forever()

        self._thread = threading.Thread(target=_run, name="tastytrade-loop", daemon=True)
        self._thread.start()
        ready.wait(timeout=5)

        if not self._loop:
            raise MT5ConnectionError("Could not start tastytrade event loop")
        return self._loop

    def _call(self, coro, timeout: Optional[float] = None):
        loop = self._ensure_loop()
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        try:
            return future.result(timeout=timeout or TT_CALL_TIMEOUT_S)
        except FutureTimeoutError:
            future.cancel()
            raise MT5ConnectionError(f"tastytrade call timed out after {timeout or TT_CALL_TIMEOUT_S}s")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def initialize(self) -> bool:
        if not (self.provider_secret and self.refresh_token):
            self._last_error = (
                "TT_SECRET / TT_REFRESH not set — create an OAuth application at "
                "developer.tastytrade.com and put the secret and refresh token in .env"
            )
            raise MT5ConnectionError(self._last_error)

        try:
            from tastytrade import Account, Session
        except ImportError as e:
            self._last_error = "tastytrade SDK not installed (pip install tastytrade)"
            raise MT5ConnectionError(self._last_error, error=str(e))

        opened = threading.Event()
        failure: dict[str, str] = {}
        # The coroutine hands the live session back through this rather than
        # through self._session, which its own teardown is entitled to clear.
        established: dict[str, Any] = {}

        session_ref: dict[str, Any] = {}

        async def _hold_session() -> None:
            """Own the session for the gateway's whole lifetime.

            Session is an async context manager; entering it inside a
            short-lived coroutine would tear the login down the moment that
            coroutine returned. So hold it open here until shutdown asks.
            """
            self._closing = asyncio.Event()
            try:
                async with Session(
                    self.provider_secret, self.refresh_token, is_test=self.sandbox
                ) as session:
                    session_ref["s"] = session
                    self._session = session
                    accounts = await Account.get(session)
                    if not accounts:
                        failure["error"] = "authenticated, but the API returned no accounts"
                        opened.set()
                        return
                    self._account = next(
                        (a for a in accounts if a.account_number == self.account_number),
                        accounts[0],
                    )
                    self.account_number = self._account.account_number
                    established["session"] = session
                    opened.set()
                    await self._closing.wait()
            except Exception as e:                      # noqa: BLE001 — surfaced below
                # Some SDK exceptions carry an empty message, and str(e) of ""
                # made the caller treat a real failure as "no failure recorded"
                # and report a misleading close-on-open instead. Always keep
                # something identifying.
                failure["error"] = str(e) or f"{type(e).__name__} (no message)"
                opened.set()
            finally:
                # Only retract the session this coroutine opened. Clearing
                # unconditionally let a finishing session null out a newer
                # one — and, on the success path, null the session the caller
                # was about to read, which surfaced as a bogus timeout.
                if self._session is session_ref.get("s"):
                    self._session = None

        loop = self._ensure_loop()
        asyncio.run_coroutine_threadsafe(_hold_session(), loop)

        opened.wait(timeout=40)

        if failure.get("error"):
            self._connected = False
            self._last_error = failure["error"]
            logger.warning("tastytrade_connect_failed", error=self._last_error)
            raise MT5ConnectionError(self._last_error)

        if not established.get("session"):
            self._connected = False
            self._last_error = ("tastytrade did not answer within 40s"
                                if not opened.is_set()
                                else "tastytrade session closed as it opened")
            raise MT5ConnectionError(self._last_error)

        # Re-arm in case the teardown guard above cleared it between the
        # coroutine signalling success and this line running.
        self._session = established["session"]

        self._connected = True
        self._last_error = ""
        logger.info(
            "tastytrade_connected",
            account=self.account_number,
            env="sandbox" if self.sandbox else "live",
        )
        return True

    def shutdown(self) -> None:
        try:
            if self._stream_task:
                self._stream_task.cancel()
            if self._closing and self._loop and self._loop.is_running():
                self._loop.call_soon_threadsafe(self._closing.set)
        except Exception as e:
            logger.warning("tastytrade_shutdown_error", error=str(e))
        finally:
            self._connected = False
            logger.info("tastytrade_shutdown")

    def health_check(self) -> bool:
        return bool(self._connected and self._session and self._account)

    def describe(self) -> BrokerDescriptor:
        live = self.health_check()
        env = "sandbox" if self.sandbox else "live"
        detail = (
            f"account {self.account_number} ({env})"
            + (" — quotes 15 min delayed" if self.sandbox else "")
            if live else (self._last_error or "not connected")
        )
        return BrokerDescriptor(
            key="tasty",
            label="tastytrade",
            connected=live,
            detail=detail,
        )

    @property
    def default_symbols(self) -> list[str]:
        return DEFAULT_SYMBOLS

    # ------------------------------------------------------------------
    # Market data
    # ------------------------------------------------------------------

    def get_tick(self, symbol: str) -> Optional[dict]:
        """Latest quote. Sandbox quotes are delayed ~15 minutes by design."""
        if not self.health_check():
            return None

        async def _quote():
            from tastytrade import DXLinkStreamer
            from tastytrade.dxfeed import Quote

            async with DXLinkStreamer(self._session) as streamer:
                await streamer.subscribe(Quote, [self._streamer_symbol(symbol)])
                try:
                    event = await asyncio.wait_for(streamer.get_event(Quote), timeout=12)
                except asyncio.TimeoutError:
                    return None

            bid = _dec(getattr(event, "bid_price", None))
            ask = _dec(getattr(event, "ask_price", None))
            if bid <= 0 or ask <= 0:
                return None
            return {
                "bid": bid,
                "ask": ask,
                "last": (bid + ask) / Decimal("2"),
                "time": int(datetime.now(timezone.utc).timestamp()),
            }

        try:
            return self._call(_quote(), timeout=20)
        except Exception as e:
            logger.error("tastytrade_get_tick_error", symbol=symbol, error=str(e))
            return None

    def _streamer_symbol(self, symbol: str) -> str:
        """Name this instrument answers to on the market-data feed.

        Equities stream under their own ticker, but a future's contract symbol
        ("/NQZ6") is not what DXLink knows it as ("/NQZ26:XCME") — subscribing
        with the contract symbol returns silence, which looked like futures
        simply having no quotes.
        """
        if not symbol.startswith("/"):
            return symbol
        if symbol in self._streamer_symbols:
            return self._streamer_symbols[symbol]

        async def _resolve():
            from tastytrade.instruments import Future

            found = await Future.get(self._session, [symbol.upper()])
            return getattr(found[0], "streamer_symbol", None) if found else None

        try:
            resolved = self._call(_resolve(), timeout=20) or symbol
        except Exception as e:
            logger.warning("tastytrade_streamer_symbol_failed", symbol=symbol, error=str(e))
            resolved = symbol

        self._streamer_symbols[symbol] = resolved
        return resolved

    def get_quotes(self, symbols: list[str]) -> dict[str, Optional[dict]]:
        """Latest quotes for a watchlist, answered from a live cache.

        A panel refreshes every second; opening and tearing down a DXLink
        session per refresh took tens of seconds and never delivered a first
        tick. Instead one long-lived subscription runs on the private loop and
        keeps `_quote_cache` current, so this returns immediately.
        """
        if not self.health_check():
            return {s: None for s in symbols}

        self._ensure_quote_stream(symbols)
        return {s: self._quote_cache.get(s) for s in symbols}

    def _ensure_quote_stream(self, symbols: list[str]) -> None:
        """Start (or re-point) the background quote subscription."""
        wanted = set(symbols)
        if self._stream_task and not self._stream_task.done() and wanted <= self._streamed:
            return

        loop = self._ensure_loop()
        if self._stream_task and not self._stream_task.done():
            self._stream_task.cancel()

        self._streamed = wanted
        # feed name -> the symbol callers asked for, so cache keys stay stable
        feed_map = {self._streamer_symbol(sym): sym for sym in wanted}

        async def _run() -> None:
            from tastytrade import DXLinkStreamer
            from tastytrade.dxfeed import Quote

            while True:
                try:
                    async with DXLinkStreamer(self._session) as streamer:
                        await streamer.subscribe(Quote, list(feed_map))
                        logger.info("tastytrade_quote_stream_open", symbols=len(feed_map))
                        async for event in streamer.listen(Quote):
                            bid = _dec(getattr(event, "bid_price", None))
                            ask = _dec(getattr(event, "ask_price", None))
                            if bid <= 0 or ask <= 0:
                                continue
                            feed_name = str(event.event_symbol)
                            self._quote_cache[feed_map.get(feed_name, feed_name)] = {
                                "bid": bid,
                                "ask": ask,
                                "last": (bid + ask) / Decimal("2"),
                                "time": int(datetime.now(timezone.utc).timestamp()),
                            }
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    # The feed drops from time to time; reconnect rather than
                    # leaving the panel frozen on stale prices.
                    logger.warning("tastytrade_quote_stream_error", error=str(e))
                    await asyncio.sleep(3)

        self._stream_task = asyncio.run_coroutine_threadsafe(_run(), loop)

    def get_ohlcv(self, symbol: str, timeframe: str = "M15", count: int = 200) -> list[dict]:
        if not self.health_check():
            return []

        period = _CANDLE_PERIOD.get(timeframe, "15m")

        async def _candles():
            from datetime import timedelta

            from tastytrade import DXLinkStreamer
            from tastytrade.dxfeed import Candle

            minutes = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "1d": 1440}[period]
            start = datetime.now(timezone.utc) - timedelta(minutes=minutes * (count + 5))

            # DXLink re-emits a candle every time it updates, so the same period
            # arrives many times over. Key by timestamp and keep the latest
            # version — appending blindly yields duplicate "bars" that would
            # skew every moving average computed on top of them.
            by_time: dict[int, dict] = {}
            loop = asyncio.get_running_loop()
            hard_deadline = loop.time() + 25          # never collect longer than this
            idle_limit = 4.0                          # stop once no NEW period appears
            last_new = loop.time()

            async with DXLinkStreamer(self._session) as streamer:
                await streamer.subscribe_candle(
                    [self._streamer_symbol(symbol)], period, start_time=start
                )
                while len(by_time) < count and loop.time() < hard_deadline:
                    remaining = min(hard_deadline - loop.time(),
                                    idle_limit - (loop.time() - last_new))
                    if remaining <= 0:
                        break                          # history exhausted
                    try:
                        candle = await asyncio.wait_for(
                            streamer.get_event(Candle), timeout=remaining
                        )
                    except asyncio.TimeoutError:
                        break
                    if candle.close is None or not candle.time:
                        continue
                    # The feed occasionally emits an all-zero placeholder bar.
                    # Left in, it drags a chart's axis down to zero and poisons
                    # every moving average computed over the series.
                    if not all(
                        (v or 0) > 0
                        for v in (candle.open, candle.high, candle.low, candle.close)
                    ):
                        continue

                    key = int(candle.time)
                    if key not in by_time:
                        last_new = loop.time()
                    # Later events for the same period supersede earlier ones.
                    by_time[key] = {
                        "time": key,
                        "open": _dec(candle.open),
                        "high": _dec(candle.high),
                        "low": _dec(candle.low),
                        "close": _dec(candle.close),
                        "volume": int(_dec(candle.volume)),
                    }

            return [by_time[t] for t in sorted(by_time)][-count:]

        try:
            return self._call(_candles(), timeout=45)
        except Exception as e:
            logger.error("tastytrade_get_ohlcv_error", symbol=symbol, timeframe=timeframe, error=str(e))
            return []

    def search_symbols(self, query: str, limit: int = 40) -> list[dict]:
        """Search this broker's tradable instruments.

        The active-equity list runs to thousands of rows and never changes
        within a session, so it is fetched once and filtered locally. An exact
        symbol that misses the cache is still looked up directly, which is how
        futures and less common tickers stay reachable.
        """
        if not self.health_check():
            return []

        needle = query.upper().strip()

        async def _load_cache():
            from tastytrade.instruments import Equity

            # One page is 1000 rows out of many thousands, and the list is
            # alphabetical — stopping at page 0 silently loses everything past
            # the early A's, so "AAPL" would never be findable. Walk the pages.
            rows: list[dict] = []
            for page in range(EQUITY_CACHE_MAX_PAGES):
                batch = await Equity.get_active_equities(
                    self._session, per_page=1000, page_offset=page
                )
                if not batch:
                    break
                rows.extend(
                    {"symbol": e.symbol, "description": e.description or "", "broker": "tasty"}
                    for e in batch
                )
                if len(batch) < 1000:
                    break
            return rows

        if not self._equity_cache:
            try:
                loaded = self._call(_load_cache(), timeout=180)
                # Leave the cache unset on failure so the next search retries;
                # pinning it to [] would make one bad call permanent.
                if loaded:
                    self._equity_cache = loaded
                    logger.info("tastytrade_equity_cache_loaded", count=len(loaded))
            except Exception as e:
                logger.warning("tastytrade_equity_cache_failed", error=str(e) or type(e).__name__)

        if not self._equity_cache:
            self._equity_cache = None

        # Futures are a small, fixed list (~250) and their real symbols carry a
        # month/year code — "/NQ" matches nothing exactly, so prefix-match them
        # here or the whole asset class stays unreachable from search.
        if not self._futures_cache:
            async def _load_futures():
                from tastytrade.instruments import Future

                futures = await Future.get(self._session)
                return [
                    {
                        "symbol": f.symbol,
                        "description": f"Future · expires {getattr(f, 'expiration_date', '?')}",
                        "broker": "tasty",
                    }
                    for f in futures
                    if getattr(f, "active", False)
                ]

            try:
                loaded = self._call(_load_futures(), timeout=60)
                # Leave it unset on failure so the next search retries. Pinning
                # it to [] made one slow first call permanent, which is why the
                # hub kept returning equities only while a direct call worked.
                if loaded:
                    self._futures_cache = loaded
                    logger.info("tastytrade_futures_cache_loaded", count=len(loaded))
            except Exception as e:
                logger.warning("tastytrade_futures_cache_failed", error=str(e))

        future_hits = [
            row for row in (self._futures_cache or [])
            if needle in row["symbol"].upper()
        ][:limit]

        hits = future_hits + [
            row for row in (self._equity_cache or [])
            if needle in row["symbol"].upper() or needle in row["description"].upper()
        ]
        hits = hits[:limit]

        if not needle:
            return hits

        # The listing endpoint does not necessarily contain every tradable
        # symbol — in sandbox, plain "AAPL" is absent from it yet quotes and
        # orders work fine. So always ask for the exact ticker too, and put it
        # first: someone typing AAPL wants AAPL, not AAPL:BOSS.
        async def _exact():
            from tastytrade.instruments import Equity, Future

            for cls in (Equity, Future):
                try:
                    found = await cls.get(self._session, [needle])
                    if found:
                        item = found[0]
                        return {
                            "symbol": item.symbol,
                            "description": getattr(item, "description", "") or "",
                            "broker": "tasty",
                        }
                except Exception:
                    continue
            return None

        exact = None
        if 1 <= len(needle) <= 8 and needle.replace("/", "").isalnum():
            try:
                exact = self._call(_exact(), timeout=15)
            except Exception:
                exact = None

        if exact:
            hits = [exact] + [h for h in hits if h["symbol"] != exact["symbol"]]
        return hits[:limit]

    def get_option_chain(self, underlying: str, expiry: Optional[str] = None) -> dict:
        """Option chain for an underlying.

        Without `expiry` this returns just the expiry dates — a full chain runs
        to thousands of contracts and no caller wants them all at once. With an
        expiry it returns that date's strikes, calls and puts paired up.
        """
        if not self.health_check():
            return {"underlying": underlying, "expirations": [], "contracts": []}

        # A trader types whichever form is in front of them — "NQ", "/NQ", or
        # the contract they are holding, "/NQU6". The chain endpoint only knows
        # the product root, so reduce all of them to "/NQ".
        root = _futures_root(underlying)
        if root:
            underlying = "/" + root

        async def _futures_chain(root: str) -> dict:
            """Futures options, parsed from the raw feed rather than the SDK.

            `get_future_option_chain` builds a `FutureOption` for every record
            and the model requires `streamer-symbol`. A handful of contracts
            come back without one — 42 out of 8480 on /NQ — and because the SDK
            validates them in a plain loop, those few sink the entire chain.
            The data itself is complete, so read the JSON and skip only the
            records that are actually unusable.
            """
            data = await self._session._get(f"/futures-option-chains/{root.lstrip('/')}")
            items = data.get("items", []) if isinstance(data, dict) else []

            dates = sorted({i["expiration-date"] for i in items if i.get("expiration-date")})
            if not expiry:
                return {"underlying": root, "expirations": dates, "contracts": []}

            by_strike: dict[str, dict] = {}
            for i in items:
                if i.get("expiration-date") != expiry:
                    continue
                try:
                    strike = str(i["strike-price"])
                    side = "call" if str(i["option-type"]).upper().startswith("C") else "put"
                    symbol = i["symbol"]
                except KeyError:
                    continue
                row = by_strike.setdefault(strike, {"strike": float(strike)})
                row[side] = symbol
                # The quote feed names contracts differently from the order
                # API; carry both so the panel can price what it lists.
                row[f"{side}_streamer"] = i.get("streamer-symbol")

            return {
                "underlying": root,
                "expiry": expiry,
                "expirations": dates,
                "contracts": sorted(by_strike.values(), key=lambda r: r["strike"]),
            }

        async def _chain():
            from tastytrade.instruments import get_option_chain

            root = underlying.upper()
            # Options ON FUTURES are a separate instrument class with their own
            # endpoint — asking the equity chain for "NQ" returns nothing at all,
            # which looked like the app simply not finding the symbol.
            if root.startswith("/"):
                return await _futures_chain(root)

            chain = await get_option_chain(self._session, root)
            dates = sorted(chain.keys())

            if not expiry:
                return {
                    "underlying": underlying.upper(),
                    "expirations": [d.isoformat() for d in dates],
                    "contracts": [],
                }

            target = next((d for d in dates if d.isoformat() == expiry), None)
            if target is None:
                return {"underlying": underlying.upper(),
                        "expirations": [d.isoformat() for d in dates], "contracts": []}

            by_strike: dict[str, dict] = {}
            for o in chain[target]:
                strike = str(o.strike_price)
                row = by_strike.setdefault(strike, {"strike": float(o.strike_price)})
                side = "call" if str(o.option_type.value).upper().startswith("C") else "put"
                row[side] = o.symbol
                row[f"{side}_streamer"] = getattr(o, "streamer_symbol", None)

            return {
                "underlying": underlying.upper(),
                "expiry": target.isoformat(),
                "expirations": [d.isoformat() for d in dates],
                "contracts": sorted(by_strike.values(), key=lambda r: r["strike"]),
            }

        try:
            return self._call(_chain(), timeout=45)
        except Exception as e:
            logger.error("tastytrade_option_chain_error", underlying=underlying, error=str(e))
            return {"underlying": underlying, "expirations": [], "contracts": [], "error": str(e)}

    def get_option_quotes(self, streamer_symbols: list[str], timeout: float = 4.0) -> dict[str, dict]:
        """Bid/ask for a set of option contracts, as a one-off snapshot.

        Deliberately not routed through the watchlist subscription: that stream
        is long-lived and re-pointing it at a few hundred strikes would drop the
        prices the rest of the panel depends on.
        """
        wanted = [s for s in dict.fromkeys(streamer_symbols) if s]
        if not wanted or not self.health_check():
            return {}

        async def _snapshot() -> dict[str, dict]:
            from tastytrade import DXLinkStreamer
            from tastytrade.dxfeed import Quote

            out: dict[str, dict] = {}
            async with DXLinkStreamer(self._session) as streamer:
                await streamer.subscribe(Quote, wanted)
                try:
                    async with asyncio.timeout(timeout):
                        async for event in streamer.listen(Quote):
                            bid = _dec(getattr(event, "bid_price", None))
                            ask = _dec(getattr(event, "ask_price", None))
                            out[str(event.event_symbol)] = {
                                "bid": float(bid),
                                "ask": float(ask),
                                "mid": float((bid + ask) / 2) if bid > 0 and ask > 0 else None,
                            }
                            # Every contract answered; no reason to sit out the
                            # rest of the window.
                            if len(out) >= len(wanted):
                                break
                except (asyncio.TimeoutError, TimeoutError):
                    pass          # a closed market simply never answers
            return out

        try:
            quotes = self._call(_snapshot(), timeout=timeout + 20)
        except Exception as e:
            logger.warning("tastytrade_option_quotes_error", error=str(e), count=len(wanted))
            return {}

        logger.info("tastytrade_option_quotes", asked=len(wanted), answered=len(quotes))
        return quotes

    def order_option(self, option_symbol: str, quantity: int, side: str = "buy",
                     limit_price: float = 0.0, closing: bool = False) -> dict:
        """Buy or sell a single option contract.

        Kept separate from `order_send` because an option is a different
        instrument class with its own open/close semantics, and mixing the two
        into one code path is how the wrong leg type gets submitted.
        """
        if not self.health_check():
            return {"retcode": -1, "comment": "tastytrade not connected", "order": 0}

        qty = _dec(quantity)
        if qty <= 0:
            return {"retcode": -1, "comment": "quantity must be positive", "order": 0}

        is_sell = "sell" in str(side).lower()
        price = _dec(limit_price)

        async def _send():
            from tastytrade.instruments import FutureOption, Option
            from tastytrade.order import NewOrder, OrderAction, OrderTimeInForce, OrderType

            # An option on a future is a different instrument class with its
            # own lookup; its symbols are the ones prefixed "./" (for example
            # "./NQU6 Q3AQ6 260817C29225"). Asking Option.get for one fails.
            cls = FutureOption if option_symbol.startswith("./") else Option
            contract = await cls.get(self._session, option_symbol)
            # Opening and closing are distinct actions on this venue: selling a
            # contract you already hold is SELL_TO_CLOSE, and sending
            # SELL_TO_OPEN instead would write a brand-new short position.
            if closing:
                action = OrderAction.SELL_TO_CLOSE if is_sell else OrderAction.BUY_TO_CLOSE
            else:
                action = OrderAction.SELL_TO_OPEN if is_sell else OrderAction.BUY_TO_OPEN
            order = NewOrder(
                time_in_force=OrderTimeInForce.DAY,
                order_type=OrderType.LIMIT if price > 0 else OrderType.MARKET,
                legs=[contract.build_leg(qty, action)],
                # Debits are negative on this venue; a buy pays, a sell receives.
                price=(-price if not is_sell else price) if price > 0 else None,
            )
            return await self._account.place_order(self._session, order, dry_run=False)

        try:
            placed = self._call(_send(), timeout=45)
        except Exception as e:
            logger.error("tastytrade_order_option_error", symbol=option_symbol, error=str(e))
            return {"retcode": -1, "comment": str(e), "order": 0}

        order_obj = getattr(placed, "order", placed)
        status = str(getattr(order_obj, "status", "") or "")
        ok = status.lower() not in ("rejected", "cancelled", "expired")
        logger.info("tastytrade_option_order_placed", symbol=option_symbol,
                    side="sell" if is_sell else "buy", quantity=str(qty), status=status)
        return {
            "retcode": 10000 if ok else 10006,
            "order": getattr(order_obj, "id", 0),
            "volume": qty,
            "price": price,
            "comment": status or "SUBMITTED",
        }

    def order_future(self, symbol: str, quantity: int, side: str = "buy",
                     limit_price: float = 0.0) -> dict:
        """Buy or sell a futures contract.

        Separate from equities and options for the same reason those are
        separate: `Future` is its own instrument class, and an OCC- or
        equity-shaped request simply fails to resolve.

        Contracts are whole numbers and notionally large — one /NQ controls far
        more than one share of anything — so quantity is never rounded up from
        a fractional value the way an equity quantity is.
        """
        if not self.health_check():
            return {"retcode": -1, "comment": "tastytrade not connected", "order": 0}

        qty = int(_dec(quantity))
        if qty <= 0:
            return {"retcode": -1, "comment": "quantity must be a positive whole number", "order": 0}

        is_sell = "sell" in str(side).lower()
        price = _dec(limit_price)

        async def _send():
            from tastytrade.instruments import Future
            from tastytrade.order import NewOrder, OrderAction, OrderTimeInForce, OrderType

            found = await Future.get(self._session, [symbol.upper()])
            if not found:
                return {"retcode": -1, "comment": f"unknown futures contract {symbol}", "order": 0}

            action = OrderAction.SELL_TO_OPEN if is_sell else OrderAction.BUY_TO_OPEN
            order = NewOrder(
                time_in_force=OrderTimeInForce.DAY,
                order_type=OrderType.LIMIT if price > 0 else OrderType.MARKET,
                legs=[found[0].build_leg(Decimal(qty), action)],
                price=(-price if not is_sell else price) if price > 0 else None,
            )
            return await self._account.place_order(self._session, order, dry_run=False)

        try:
            placed = self._call(_send(), timeout=45)
        except Exception as e:
            logger.error("tastytrade_order_future_error", symbol=symbol, error=str(e))
            return {"retcode": -1, "comment": str(e), "order": 0}

        if isinstance(placed, dict):
            return placed

        order_obj = getattr(placed, "order", placed)
        status = str(getattr(order_obj, "status", "") or "")
        ok = status.lower() not in ("rejected", "cancelled", "expired")
        logger.info("tastytrade_future_order_placed", symbol=symbol,
                    side="sell" if is_sell else "buy", quantity=qty, status=status)
        return {
            "retcode": 10000 if ok else 10006,
            "order": getattr(order_obj, "id", 0),
            "volume": Decimal(qty),
            "price": price,
            "comment": status or "SUBMITTED",
        }

    def get_history(self, count: int = 50) -> list[dict]:
        """Recent transactions on this account."""
        if not self.health_check():
            return []

        async def _history():
            return await self._account.get_history(self._session, per_page=count)

        try:
            rows = self._call(_history(), timeout=30)
        except Exception as e:
            logger.error("tastytrade_get_history_error", error=str(e))
            return []

        out = []
        for t in rows[:count]:
            # The transaction feed includes money movements — the account's
            # opening credit among them. Left in, the $100k deposit reads as a
            # winning trade and wrecks every P&L total downstream.
            tx_type = str(getattr(t, "transaction_type", "") or "")
            if tx_type and "trade" not in tx_type.lower():
                continue

            action = str(getattr(t, "action", "") or "")
            out.append({
                "broker": "tasty",
                "ticket": getattr(t, "id", 0),
                "symbol": str(getattr(t, "symbol", "") or getattr(t, "underlying_symbol", "") or ""),
                "type": "sell" if "Sell" in action else "buy",
                "volume": float(_dec(getattr(t, "quantity", 0))),
                "price": float(_dec(getattr(t, "price", 0))),
                "profit": float(_dec(getattr(t, "net_value", 0))),
                "time": str(getattr(t, "executed_at", "") or getattr(t, "transaction_date", "")),
                "is_close": "Close" in action,
            })
        return out

    def get_symbol_info(self, symbol: str) -> Optional[dict]:
        if not self.health_check():
            return None

        async def _info():
            from tastytrade.instruments import Equity

            equity = await Equity.get(self._session, symbol)
            return {
                "name": equity.symbol,
                "visible": True,
                "instrument_type": equity.instrument_type,
                "is_fractional": getattr(equity, "is_fractional_quantity_eligible", None),
            }

        try:
            return self._call(_info())
        except Exception as e:
            logger.error("tastytrade_symbol_info_error", symbol=symbol, error=str(e))
            return None

    # ------------------------------------------------------------------
    # Account and positions
    # ------------------------------------------------------------------

    def get_account_info(self) -> AccountState:
        if not self.health_check():
            raise MT5ConnectionError("tastytrade not connected", detail=self._last_error)

        async def _balances():
            return await self._account.get_balances(self._session)

        try:
            b = self._call(_balances())
        except Exception as e:
            logger.error("tastytrade_get_account_error", error=str(e))
            raise MT5ConnectionError("Failed to read tastytrade balances", error=str(e))

        equity = _pick(b, "net_liquidating_value", "margin_equity")
        cash = _pick(b, "cash_balance")
        buying_power = _pick(b, "derivative_buying_power", "equity_buying_power")
        margin = _pick(b, "maintenance_requirement", "reg_t_margin_requirement")

        return AccountState(
            balance=cash or equity,
            equity=equity or cash,
            margin=margin,
            free_margin=buying_power or cash,
            initial_capital=cash or equity,
            timestamp=datetime.now(timezone.utc),
        )

    def get_positions(self, magic: Optional[int] = None) -> list[PositionInfo]:
        """Open positions. `magic` has no tastytrade equivalent and is ignored."""
        if not self.health_check():
            return []

        async def _positions():
            return await self._account.get_positions(self._session)

        try:
            raw = self._call(_positions())
        except Exception as e:
            logger.error("tastytrade_get_positions_error", error=str(e))
            return []

        out: list[PositionInfo] = []
        for idx, p in enumerate(raw):
            qty = _dec(p.quantity)
            if qty == 0:
                continue
            direction = str(getattr(p, "quantity_direction", "") or "").lower()
            avg = _dec(getattr(p, "average_open_price", None))
            mark = _dec(getattr(p, "mark_price", None) or getattr(p, "close_price", None))
            multiplier = _dec(getattr(p, "multiplier", 1)) or Decimal("1")

            is_long = direction.startswith("long") or (not direction and qty > 0)
            # tastytrade reports no per-position P&L, so derive it from the mark.
            profit = ((mark - avg) if is_long else (avg - mark)) * abs(qty) * multiplier

            # An option's OCC symbol ("AAPL  260821C00305000") is 21 characters
            # and PositionInfo caps symbol at 10, so it cannot be carried there.
            # Truncating it silently produced a position that could never be
            # closed — the stub matched no contract. Show the underlying and
            # keep the full symbol in `comment`, which close_position reads.
            full_symbol = str(p.symbol)
            is_option = "OPTION" in str(getattr(p, "instrument_type", "")).upper()
            display = str(getattr(p, "underlying_symbol", "") or full_symbol) if is_option else full_symbol

            out.append(
                PositionInfo(
                    ticket=idx + 1,          # no numeric position id on this venue
                    symbol=display[:10] or "UNKNOWN",
                    type="LONG" if is_long else "SHORT",
                    volume=abs(qty),
                    price_open=avg if avg > 0 else Decimal("0.00001"),
                    sl=None,
                    tp=None,
                    profit=profit if mark > 0 else Decimal("0"),
                    magic=0,
                    comment=full_symbol if is_option else "tastytrade",
                )
            )
        return out

    # ------------------------------------------------------------------
    # Trading
    # ------------------------------------------------------------------

    def order_send(self, request: dict) -> dict:
        """Place a single-leg equity order. Market unless a `price` is given."""
        if not self.health_check():
            return {"retcode": -1, "comment": "tastytrade not connected", "order": 0}

        symbol = str(request.get("symbol", "")).upper()
        raw_qty = _dec(request.get("volume", 0))
        if raw_qty <= 0:
            return {"retcode": -1, "comment": "quantity must be positive", "order": 0}

        # Equities here trade in whole shares — most symbols reject fractional
        # orders ("fractional_trading_invalid_symbol"). A caller handing over an
        # MT5-style 0.01 lot means one share, not a hundredth of one.
        quantity = Decimal(int(raw_qty)) if raw_qty >= 1 else Decimal(1)
        if quantity != raw_qty:
            logger.info("tastytrade_quantity_rounded",
                        symbol=symbol, requested=str(raw_qty), used=str(quantity))

        raw_side = request.get("type", request.get("action", "buy"))
        is_sell = (
            raw_side == 1 if isinstance(raw_side, int)
            else "sell" in str(raw_side).lower()
        )
        limit_price = _dec(request.get("price", 0))

        async def _send():
            from tastytrade.instruments import Equity
            from tastytrade.order import (
                NewOrder, OrderAction, OrderTimeInForce, OrderType,
            )

            equity = await Equity.get(self._session, symbol)
            action = OrderAction.SELL_TO_OPEN if is_sell else OrderAction.BUY_TO_OPEN
            leg = equity.build_leg(quantity, action)

            order = NewOrder(
                time_in_force=OrderTimeInForce.DAY,
                order_type=OrderType.LIMIT if limit_price > 0 else OrderType.MARKET,
                legs=[leg],
                # A limit order needs a price; a market order must not carry one.
                price=(-limit_price if not is_sell else limit_price) if limit_price > 0 else None,
            )
            return await self._account.place_order(self._session, order, dry_run=False)

        try:
            placed = self._call(_send(), timeout=40)
        except Exception as e:
            logger.error("tastytrade_order_send_error", symbol=symbol, error=str(e))
            return {"retcode": -1, "comment": str(e), "order": 0}

        order_obj = getattr(placed, "order", placed)
        status = str(getattr(order_obj, "status", "") or "")
        ok = status.lower() not in ("rejected", "cancelled", "expired")

        logger.info("tastytrade_order_placed", symbol=symbol,
                    side="sell" if is_sell else "buy",
                    quantity=str(quantity), status=status)
        return {
            "retcode": 10000 if ok else 10006,
            "order": getattr(order_obj, "id", 0),
            "volume": quantity,
            "price": limit_price,
            "comment": status or "SUBMITTED",
        }

    def close_position(self, position) -> dict:
        """Flatten a holding with an opposite market order."""
        if not self.health_check():
            return {"success": False, "error": "tastytrade not connected"}

        ticket = getattr(position, "ticket", None) or int(position)
        match = next((p for p in self.get_positions() if p.ticket == int(ticket)), None)
        if match is None:
            return {"success": False, "error": f"no open tastytrade position #{ticket}"}

        # Each instrument class has to go out through its own order path; an
        # equity request simply fails to resolve a futures or option symbol.
        # A leading slash marks a future, and get_positions parks an option's
        # full OCC symbol in `comment`.
        if match.symbol.startswith("/"):
            result = self.order_future(
                match.symbol,
                int(match.volume),
                "sell" if match.type == "LONG" else "buy",
            )
        elif match.comment and match.comment != "tastytrade":
            result = self.order_option(
                match.comment,
                int(match.volume),
                "sell" if match.type == "LONG" else "buy",
                closing=True,
            )
        else:
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
