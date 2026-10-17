"""One paper-trading cycle: read what each promoted rule wants, let RiskAgent decide, fill on paper.

Run it once per trading day, after the US close. The cycle:

1. Refuse to run if the account is halted (hard rule 4 — only a person clears a halt).
2. Mark every open position to the latest completed close.
3. For each promoted rule, run the *same backtest engine that validated it*
   over the latest window and read the position it holds at the end. That
   keeps paper trades faithful to what was graded, including the engine's own
   exits. Holdings that fell out of the ranking are closed.
4. Every intended trade becomes a ``TradingSignal`` and goes through the real
   ``RiskAgent`` with the paper account's state and FTMO limits:
   NORMAL full size, CAUTION half size, RISK_OFF closes only, HALT flattens
   everything and stops the system.
5. Exits are processed before entries, one at a time (hard rule 3: the
   decision path stays sequential — no gather).
6. Every signal, risk decision, fill and rejection is appended to the
   paper ``EventStore`` (append-only, hard rule 5).

Fills happen at the last completed close, with the Lab's slippage and
commission. That is the realistic price for an end-of-day decision made
after the close; the backtest's next-open fill is approximated, not matched
tick for tick.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import ROUND_DOWN, Decimal
from pathlib import Path
from typing import Callable, Dict, List, Optional
from uuid import uuid4

from ftmo_mt5.agents.risk_agent import RiskAgent
from ftmo_mt5.backtest import run_backtest
from ftmo_mt5.core.exceptions import CircuitBreakerError
from ftmo_mt5.core.models import (
    CircuitBreakerTriggered, FTMOConfig, OrderFilled, OrderRejected, PositionClosed,
    RiskChecked, RiskState, RiskStateChanged, SignalDirection, SignalGenerated, TradingSignal,
)
from ftmo_mt5.lab import metrics, windows
from ftmo_mt5.marketdata.base import Candle
from ftmo_mt5.store.event_store import EventStore

from systematic.config import Settings, build_ftmo_limits
from systematic.data import load_completed
from systematic.paper_broker import D, HaltedError, PaperBroker, money

REJECTED = 10006          # MT5 TRADE_RETCODE_REJECT, reused for "refused before it reached the book"


@dataclass
class Desire:
    """What one rule wants for one symbol after the latest completed bar."""
    symbol: str
    strategy: str
    direction: str                 # LONG | SHORT | FLAT
    price: Decimal                 # last completed close
    fresh: bool                    # entered recently enough to join
    entry_age_bars: Optional[int]
    note: str


def desired_position(symbol: str, spec: Dict, candles: List, settings: Settings) -> Desire:
    """Replay the validated rule over the latest window and read its open position.

    A signal on the last completed bar would normally fill on the *next* bar,
    which does not exist yet. A provisional bar at the last close (O=H=L=C, so
    it cannot trip a stop) lets the engine take that fill, at the price a paper
    order would actually get.
    """
    tf = "1d"
    last = candles[-1]
    close = D(last.close)
    provisional = Candle(timestamp=last.timestamp + windows.SECONDS[tf], open=last.close,
                         high=last.close, low=last.close, close=last.close, volume=0)
    series = windows.series(symbol, tf, list(candles) + [provisional])
    result = run_backtest(metrics._config(symbol, tf, spec), series)
    name = spec["strategy"]

    if not result.trades or result.trades[-1].exit_reason != "end_of_data":
        return Desire(symbol, name, "FLAT", close, True, None, "rule is out of the market")

    t = result.trades[-1]
    # Completed bars since the entry fill: 0 = the signal came on the latest bar.
    age = sum(1 for c in candles if c.timestamp >= t.entry_time)
    fresh = age <= settings.max_entry_age_bars
    note = (f"signal on the latest bar" if age == 0 else
            f"entered {age} bar(s) ago" + ("" if fresh else " — too old to join; waiting for a fresh entry"))
    return Desire(symbol, name, t.direction, close, fresh, age, note)


class Cycle:
    def __init__(self, settings: Settings, ftmo_config: FTMOConfig,
                 fetch: Callable[..., List] = load_completed, now: Optional[datetime] = None):
        self.settings = settings
        self.ftmo_config = ftmo_config
        self.fetch = fetch
        self.now = now
        state = settings.state_dir
        self.broker = PaperBroker(state / "paper" / "ledger.json", ftmo_config.initial_capital,
                                  settings.commission(), settings.slippage(), D(settings.margin_rate))
        if now is not None:                       # replay: stamp the simulated date
            self.broker.clock = lambda: now
        self.store = EventStore(storage_path=state / "paper" / "events")
        self.risk = RiskAgent(ftmo_config)
        self.log: List[Dict] = []

    # ---------------- helpers ----------------
    def _note(self, kind: str, **kw) -> None:
        self.log.append({"kind": kind, **kw})

    def _signal(self, symbol: str, direction: str, strategy: str, cid) -> TradingSignal:
        return TradingSignal(symbol=symbol, direction=SignalDirection(direction), confidence=Decimal("1"),
                             timestamp=datetime.now(timezone.utc), correlation_id=cid,
                             strategy_name=strategy)

    def _halt(self, reason: str, prices: Dict[str, Decimal]) -> None:
        """Flatten everything, record it, and stop. Only a person may clear this."""
        cid = uuid4()
        n = len(self.broker.positions())
        for sym in list(self.broker.positions()):
            ref = prices.get(sym, D(self.broker.positions()[sym]["last_price"]))
            self._close(sym, ref, f"HALT: {reason}", cid)
        self.broker.halt(reason)
        self.store.append(CircuitBreakerTriggered(timestamp=datetime.now(timezone.utc), correlation_id=cid,
                                                  reason=reason, positions_to_close=n))
        self._note("HALT", reason=reason, flattened=n)

    def _close(self, symbol: str, ref: Decimal, reason: str, cid) -> None:
        f = self.broker.close(symbol, ref, reason)
        self.store.append(PositionClosed(timestamp=datetime.now(timezone.utc), correlation_id=cid,
                                         symbol=symbol, profit=f.realized_pnl - f.commission,
                                         close_price=f.fill_price))
        self._note("CLOSE", symbol=symbol, side=f.side, quantity=str(f.quantity),
                   price=money(f.fill_price), pnl=money(f.realized_pnl), reason=reason)

    async def _risk(self, signal: TradingSignal):
        """RiskAgent's verdict on one signal; a crash is a halt, never a retry."""
        acct = self.broker.account_state()
        limits = build_ftmo_limits(self.ftmo_config, acct.balance)
        decision = await self.risk.process(signal, acct, limits, self.broker.positions_info())
        self.store.append(RiskChecked(timestamp=datetime.now(timezone.utc), correlation_id=signal.correlation_id,
                                      approved=decision.approved, risk_state=decision.risk_state,
                                      reason=decision.reason))
        old = RiskState(self.broker.state.get("risk_state", "NORMAL"))
        if decision.risk_state != old:
            self.store.append(RiskStateChanged(timestamp=datetime.now(timezone.utc),
                                               correlation_id=signal.correlation_id, old_state=old,
                                               new_state=decision.risk_state, trigger=decision.reason))
            self.broker.state["risk_state"] = decision.risk_state.value
            self._note("RISK_STATE", old=old.value, new=decision.risk_state.value, reason=decision.reason)
        return decision

    def _size(self, price: Decimal, multiplier: Decimal) -> Decimal:
        """Equal-weight slots, scaled by RiskAgent, capped by gross exposure."""
        equity = self.broker.equity()
        slot = equity / Decimal(self.settings.max_positions)
        room = equity * D(self.settings.max_gross_exposure) - self.broker.gross_exposure()
        budget = max(Decimal(0), min(slot * multiplier, room))
        return (budget / price).to_integral_value(rounding=ROUND_DOWN)

    # ---------------- the cycle ----------------
    async def run(self, ranking: Dict) -> Dict:
        if self.broker.halted:
            raise HaltedError(f"account halted since {self.broker.halted['at']}: {self.broker.halted['reason']}")

        promoted = {p["symbol"]: p for p in ranking.get("promoted", [])}
        held = dict(self.broker.positions())
        prices: Dict[str, Decimal] = {}
        desires: Dict[str, Desire] = {}

        # 1. data, mark to market, and what each rule wants
        for sym in sorted(set(promoted) | set(held)):
            try:
                candles = self.fetch(sym, "1d", self.settings.bars, now=self.now)
            except Exception as e:                            # noqa: BLE001
                self._note("DATA_ERROR", symbol=sym, error=f"{type(e).__name__}: {e}")
                continue
            if not candles:
                self._note("DATA_ERROR", symbol=sym, error="no completed bars")
                continue
            prices[sym] = D(candles[-1].close)
            if sym in promoted:
                desires[sym] = desired_position(sym, promoted[sym]["spec"], candles, self.settings)
        self.broker.mark(prices)

        try:
            # 2. exits first: demoted holdings, rules now FLAT, and direction flips
            for sym, pos in held.items():
                if sym not in prices:
                    self._note("SKIP", symbol=sym, why="no price this cycle; position kept")
                    continue
                want = desires.get(sym)
                if want is not None and want.direction == pos["side"]:
                    continue
                why = ("dropped from the ranking" if sym not in promoted else
                       "rule is flat" if want.direction == "FLAT" else f"rule flipped to {want.direction}")
                await self._exit(sym, pos, prices[sym], why)
                if self.broker.halted:
                    return self._finish(ranking)

            # 3. entries, strongest-ranked first
            for sym in [p["symbol"] for p in ranking.get("promoted", [])]:
                want = desires.get(sym)
                if want is None or want.direction == "FLAT" or sym in self.broker.positions():
                    continue
                if not want.fresh:
                    self._note("WAIT", symbol=sym, direction=want.direction, why=want.note)
                    continue
                await self._enter(want)
                if self.broker.halted:
                    return self._finish(ranking)
        except CircuitBreakerError as e:
            self._halt(f"RiskAgent circuit breaker: {e}", prices)

        return self._finish(ranking)

    async def _exit(self, sym: str, pos: Dict, price: Decimal, why: str) -> None:
        cid = uuid4()
        sig = self._signal(sym, "FLAT", pos.get("strategy") or "systematic", cid)
        self.store.append(SignalGenerated(timestamp=datetime.now(timezone.utc), correlation_id=cid, signal=sig))
        decision = await self._risk(sig)
        if decision.risk_state == RiskState.HALT:
            self._halt(decision.reason, {sym: price})
            return
        # Closing is always allowed short of HALT: RISK_OFF explicitly permits closes.
        self._close(sym, price, why, cid)

    async def _enter(self, want: Desire) -> None:
        cid = uuid4()
        sig = self._signal(want.symbol, want.direction, want.strategy, cid)
        self.store.append(SignalGenerated(timestamp=datetime.now(timezone.utc), correlation_id=cid, signal=sig))
        decision = await self._risk(sig)
        if decision.risk_state == RiskState.HALT:
            self._halt(decision.reason, {want.symbol: want.price})
            return
        for p in decision.positions_to_close:          # e.g. RiskAgent's pre-midnight flatten
            sym = getattr(p, "symbol", None)
            if sym in self.broker.positions():
                self._close(sym, D(self.broker.positions()[sym]["last_price"]), f"RiskAgent: {decision.reason}", cid)
        if not decision.approved:
            self.store.append(OrderRejected(timestamp=datetime.now(timezone.utc), correlation_id=cid,
                                            retcode=REJECTED, reason=decision.reason))
            self._note("REJECTED", symbol=want.symbol, direction=want.direction, reason=decision.reason)
            return
        qty = self._size(want.price, D(decision.adjusted_volume))
        if qty < 1:
            self.store.append(OrderRejected(timestamp=datetime.now(timezone.utc), correlation_id=cid,
                                            retcode=REJECTED, reason="no room: size rounds to zero shares"))
            self._note("REJECTED", symbol=want.symbol, direction=want.direction, reason="size rounds to zero")
            return
        f = self.broker.open(want.symbol, want.direction, qty, want.price, want.strategy, want.note)
        self.store.append(OrderFilled(timestamp=datetime.now(timezone.utc), correlation_id=cid,
                                      deal_ticket=f.ticket, fill_price=f.fill_price,
                                      slippage=f.fill_price - f.reference_price, volume_filled=f.quantity))
        self._note("OPEN", symbol=want.symbol, side=want.direction, quantity=str(qty),
                   price=money(f.fill_price), riskState=decision.risk_state.value,
                   sizeMultiplier=str(decision.adjusted_volume), strategy=want.strategy, why=want.note)

    def _finish(self, ranking: Dict) -> Dict:
        snap = self.broker.snapshot()
        self.broker.save()
        return {"at": snap["at"], "equity": snap["equity"], "balance": snap["balance"],
                "riskState": self.broker.state.get("risk_state"), "halted": self.broker.halted,
                "exploratory": ranking.get("exploratory", False),
                "positions": self.broker.positions(), "actions": self.log}


def load_ranking(state_dir: Path) -> Dict:
    p = Path(state_dir) / "ranking.json"
    if not p.exists():
        raise FileNotFoundError("No ranking yet. Run `python -m systematic research` first.")
    return json.loads(p.read_text(encoding="utf-8"))
