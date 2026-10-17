"""PaperBroker: an in-process, simulated stock account. No network, ever.

State lives in one JSON ledger (``state/paper/ledger.json``), rewritten
atomically after every change. Money is ``Decimal`` throughout (hard rule 1);
it is stored as strings so no float ever touches it on the way to disk.

Costs mirror the Lab so a paper result is comparable to the validation that
promoted it: commission per unit per side, and slippage in ticks that always
works against the trade.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import ROUND_DOWN, Decimal
from pathlib import Path
from typing import Dict, List, Optional

from ftmo_mt5.core.models import AccountState, PositionInfo
from ftmo_mt5.instruments.metadata import resolve_instrument

CENT = Decimal("0.01")
ZERO = Decimal("0")


def D(x) -> Decimal:
    return x if isinstance(x, Decimal) else Decimal(str(x))


def money(x: Decimal) -> str:
    return str(D(x).quantize(CENT))


def tick_size(symbol: str) -> Decimal:
    try:
        return D(resolve_instrument(symbol).tick_size or "0.01")
    except Exception:                                   # noqa: BLE001 — unknown stock trades in cents
        return Decimal("0.01")


@dataclass
class Fill:
    ticket: int
    symbol: str
    side: str            # LONG | SHORT (the position's side)
    action: str          # OPEN | CLOSE
    quantity: Decimal
    reference_price: Decimal
    fill_price: Decimal
    commission: Decimal
    realized_pnl: Decimal = ZERO


class HaltedError(RuntimeError):
    """The account is halted. Only a person may clear it (hard rule 4)."""


class PaperBroker:
    # A replay drives the broker through past days, so the ledger must record the
    # bar's date, not the wall clock. Live cycles leave this as None and get now().
    clock = None

    def __init__(self, path: Path, initial_capital: Decimal, commission_per_unit: Decimal,
                 slippage_ticks: Decimal, margin_rate: Decimal = Decimal("1")):
        self.path = Path(path)
        self.commission_per_unit = D(commission_per_unit)
        self.slippage_ticks = D(slippage_ticks)
        self.margin_rate = D(margin_rate)
        if self.path.exists():
            self.state = json.loads(self.path.read_text(encoding="utf-8"))
        else:
            self.state = {
                "initial_capital": str(D(initial_capital)),
                "created_at": _now(),
                "realized_pnl": "0", "commissions": "0",
                "next_ticket": 1,
                "positions": {}, "trades": [], "equity_history": [],
                "risk_state": "NORMAL", "halted": None,
            }

    # ---------------- persistence ----------------
    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    # ---------------- state ----------------
    @property
    def initial_capital(self) -> Decimal:
        return D(self.state["initial_capital"])

    @property
    def halted(self) -> Optional[Dict]:
        return self.state.get("halted")

    def positions(self) -> Dict[str, Dict]:
        return self.state["positions"]

    def mark(self, prices: Dict[str, Decimal]) -> None:
        for sym, pos in self.positions().items():
            if sym in prices:
                pos["last_price"] = str(D(prices[sym]))
                pos["marked_at"] = _now()

    @staticmethod
    def _unrealized(pos: Dict) -> Decimal:
        sign = Decimal(1) if pos["side"] == "LONG" else Decimal(-1)
        return (D(pos["last_price"]) - D(pos["avg_price"])) * D(pos["quantity"]) * sign

    def balance(self) -> Decimal:
        return self.initial_capital + D(self.state["realized_pnl"]) - D(self.state["commissions"])

    def gross_exposure(self) -> Decimal:
        return sum((D(p["last_price"]) * D(p["quantity"]) for p in self.positions().values()), ZERO)

    def equity(self) -> Decimal:
        return self.balance() + sum((self._unrealized(p) for p in self.positions().values()), ZERO)

    def account_state(self) -> AccountState:
        equity = self.equity()
        margin = self.gross_exposure() * self.margin_rate
        return AccountState(
            balance=max(ZERO, self.balance()), equity=max(ZERO, equity), margin=margin,
            free_margin=max(ZERO, equity - margin), initial_capital=self.initial_capital,
            timestamp=datetime.now(timezone.utc))

    def positions_info(self) -> List[PositionInfo]:
        return [PositionInfo(ticket=p["ticket"], symbol=sym, type=p["side"],
                             volume=D(p["quantity"]), price_open=D(p["avg_price"]),
                             profit=self._unrealized(p), comment=p.get("strategy", "")[:27])
                for sym, p in self.positions().items()]

    # ---------------- orders ----------------
    def _slip(self, symbol: str, price: Decimal, buying: bool) -> Decimal:
        offset = self.slippage_ticks * tick_size(symbol)
        return price + offset if buying else price - offset

    def open(self, symbol: str, side: str, quantity: Decimal, reference_price: Decimal,
             strategy: str, note: str = "") -> Fill:
        if self.halted:
            raise HaltedError(self.halted["reason"])
        if side not in ("LONG", "SHORT"):
            raise ValueError(f"side must be LONG or SHORT, got {side!r}")
        qty = D(quantity).to_integral_value(rounding=ROUND_DOWN)
        if qty <= 0:
            raise ValueError("quantity must be at least one share")
        if symbol in self.positions():
            raise ValueError(f"already holding {symbol}; close it before opening again")
        ref = D(reference_price)
        fill = self._slip(symbol, ref, buying=(side == "LONG"))
        commission = self.commission_per_unit * qty
        ticket = self.state["next_ticket"]
        self.state["next_ticket"] = ticket + 1
        self.positions()[symbol] = {
            "ticket": ticket, "side": side, "quantity": str(qty),
            "avg_price": str(fill), "last_price": str(ref),
            "opened_at": self._stamp(), "strategy": strategy, "note": note,
            "entry_commission": str(commission),
        }
        self.state["commissions"] = str(D(self.state["commissions"]) + commission)
        return Fill(ticket, symbol, side, "OPEN", qty, ref, fill, commission)

    def close(self, symbol: str, reference_price: Decimal, reason: str) -> Fill:
        # Closing is allowed while halted: flattening is how a halt protects the account.
        pos = self.positions().get(symbol)
        if pos is None:
            raise ValueError(f"no open position in {symbol}")
        qty, entry, ref = D(pos["quantity"]), D(pos["avg_price"]), D(reference_price)
        fill = self._slip(symbol, ref, buying=(pos["side"] == "SHORT"))
        sign = Decimal(1) if pos["side"] == "LONG" else Decimal(-1)
        gross = (fill - entry) * qty * sign
        commission = self.commission_per_unit * qty
        self.state["realized_pnl"] = str(D(self.state["realized_pnl"]) + gross)
        self.state["commissions"] = str(D(self.state["commissions"]) + commission)
        net = gross - commission - D(pos.get("entry_commission", "0"))
        self.state["trades"].append({
            "ticket": pos["ticket"], "symbol": symbol, "side": pos["side"], "quantity": str(qty),
            "entry_price": str(entry), "exit_price": str(fill), "opened_at": pos["opened_at"],
            "closed_at": self._stamp(), "strategy": pos.get("strategy", ""), "reason": reason,
            "gross_pnl": money(gross), "net_pnl": money(net),
        })
        del self.positions()[symbol]
        return Fill(pos["ticket"], symbol, pos["side"], "CLOSE", qty, ref, fill, commission, gross)

    # ---------------- halt ----------------
    def halt(self, reason: str) -> None:
        self.state["halted"] = {"reason": reason, "at": _now()}

    def clear_halt(self, who: str) -> None:
        if not self.halted:
            return
        self.state.setdefault("halt_history", []).append({**self.halted, "cleared_at": _now(), "cleared_by": who})
        self.state["halted"] = None

    def _stamp(self) -> str:
        """Simulated time when a replay set a clock, otherwise the wall clock."""
        if self.clock is not None:
            return self.clock().isoformat(timespec="seconds")
        return _now()

    def snapshot(self) -> Dict:
        row = {"at": _now(), "balance": money(self.balance()), "equity": money(self.equity()),
               "positions": len(self.positions())}
        self.state["equity_history"].append(row)
        return row


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
