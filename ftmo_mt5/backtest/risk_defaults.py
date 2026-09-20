"""Measured per-instrument, per-timeframe risk defaults.

These are not opinions. Each entry comes from a sweep of trailing-stop sizes
run on real Yahoo history for that instrument and timeframe, scored by
return% / maxDrawdown%, then checked two ways before being written down:

* **Both halves.** The series is cut in half and the setting must make money on
  each half independently, at $2.50 commission and 2 ticks of slippage.
* **Cost sensitivity.** Net P&L is re-run at 1, 2, 4 and 8 ticks. A setting that
  only works at 1 tick is a fill assumption, not an edge, and is not recorded.

One pattern showed up on every instrument and every timeframe: the tighter the
trail, the better the backtest looks, monotonically, all the way down to 0.2x
ATR. That is what OHLC data does when it has no intrabar path — the exit is
assumed to fill exactly at the trail. The entries below deliberately sit well
above that region, and 5-minute bars carry no default at all because nothing
there survived realistic costs.

`None` means "no trailing stop", and is a real answer rather than a gap.

Measured 2026-08-22. Re-run `scripts/` sweeps and update the table if the
market regime changes; the values are data, not physics.
"""

__all__ = ["BUILT_IN", "resolve", "all_entries", "save_overrides",
           "reset_overrides", "OVERRIDES_FILE"]

import json
from pathlib import Path
from typing import Optional

from ftmo_mt5.instruments.metadata import resolve_instrument

# root -> timeframe -> trailing stop in ATR multiples (None = no trail)
BUILT_IN: dict[str, dict[str, Optional[float]]] = {
    "GC": {"5m": None, "15m": 0.5, "30m": 0.5, "1h": 0.5, "1d": 0.4},
    "NQ": {"5m": None, "15m": 0.5, "30m": 0.5, "1h": 0.5, "1d": 0.5},
}

# Why each instrument stops where it does, shown next to the value in the UI so
# a number nobody can explain never ends up in a live run.
NOTES: dict[str, str] = {
    "GC": ("Daily is the robust one: +$182k net even at 8 ticks of slippage. "
           "30m holds up too. 1h looks far better than it is — it collapses to "
           "roughly zero at 8 ticks. 5m has no default: it loses on the first "
           "half and dies at 4 ticks."),
    "NQ": ("15m through 1d all survive 8 ticks of slippage and make money on "
           "both halves. 5m has no default: it loses on the first half at 0.5x "
           "and turns negative at 4 ticks."),
}

OVERRIDES_FILE = Path(__file__).resolve().parents[2] / "backtest_defaults.json"


def _root(symbol: str) -> str:
    """Product root for a symbol, so /NQ, NQ and /NQU6 all resolve together."""
    try:
        return resolve_instrument(symbol).root.upper()
    except Exception:
        return (symbol or "").lstrip("/").upper()


def _read_overrides() -> dict[str, dict[str, Optional[float]]]:
    try:
        raw = json.loads(OVERRIDES_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out: dict[str, dict[str, Optional[float]]] = {}
    for root, by_tf in (raw or {}).items():
        if not isinstance(by_tf, dict):
            continue
        clean: dict[str, Optional[float]] = {}
        for tf, value in by_tf.items():
            if value in (None, "", "none"):
                clean[str(tf)] = None
            else:
                try:
                    clean[str(tf)] = float(value)
                except (TypeError, ValueError):
                    continue
        out[str(root).upper()] = clean
    return out


def all_entries() -> dict:
    """Everything the panel needs: the built-ins, the overrides, and the merge."""
    overrides = _read_overrides()
    merged: dict[str, dict[str, Optional[float]]] = {
        root: dict(by_tf) for root, by_tf in BUILT_IN.items()
    }
    for root, by_tf in overrides.items():
        merged.setdefault(root, {}).update(by_tf)
    return {
        "builtIn": BUILT_IN,
        "overrides": overrides,
        "effective": merged,
        "notes": NOTES,
        "file": str(OVERRIDES_FILE),
        "measured": "2026-08-22",
    }


def resolve(symbol: str, timeframe: str) -> Optional[float]:
    """The trailing stop to use for this symbol and timeframe, or None.

    A saved override wins over the measured default. An instrument with no
    entry returns None — better no trailing stop than one carried over from a
    market it was never measured on.
    """
    root = _root(symbol)
    overrides = _read_overrides()
    if root in overrides and timeframe in overrides[root]:
        return overrides[root][timeframe]
    return BUILT_IN.get(root, {}).get(timeframe)


def save_overrides(entries: dict) -> dict:
    """Persist user edits. Pass {root: {timeframe: value|None}}."""
    current = _read_overrides()
    for root, by_tf in (entries or {}).items():
        root = str(root).upper().lstrip("/")
        slot = current.setdefault(root, {})
        for tf, value in (by_tf or {}).items():
            if value in (None, "", "none"):
                slot[str(tf)] = None
            else:
                slot[str(tf)] = float(value)
    OVERRIDES_FILE.write_text(json.dumps(current, indent=2), encoding="utf-8")
    return all_entries()


def reset_overrides() -> dict:
    """Throw the user's edits away and go back to the measured table."""
    try:
        OVERRIDES_FILE.unlink()
    except FileNotFoundError:
        pass
    return all_entries()
