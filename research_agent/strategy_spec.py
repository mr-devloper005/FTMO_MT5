"""The contract every AI-proposed strategy must satisfy, and the adapter that runs it.

The agent never writes code. It writes a JSON spec — named numeric parameters
plus entry and exit conditions built from a fixed set of causal indicators —
and this module validates it, gives it a stable name, logs it, and turns it
into an object that satisfies ``ftmo_mt5.signals.strategies.Strategy``. From
there the existing engine, metrics and robustness grading run it exactly as
they run the hand-written rules: there is one backtester, not two.

Spec shape::

    {
      "name": "rsi_dip_in_uptrend",                 # slug, becomes AI_RSI_DIP_IN_UPTREND_<hash6>
      "description": "Buy RSI dips while above the 200-day average.",
      "params": {"rsi_period": 14, "trend_period": 200, "dip_level": 30, "exit_level": 55},
      "long_entry":  {"all": [{"left": {"ind": "close"}, "op": ">", "right": {"ind": "sma", "period": "$trend_period"}},
                              {"left": {"ind": "rsi", "period": "$rsi_period"}, "op": "<", "right": {"value": "$dip_level"}}]},
      "long_exit":   {"any": [{"left": {"ind": "rsi", "period": "$rsi_period"}, "op": ">", "right": {"value": "$exit_level"}}]},
      "short_entry": null,
      "short_exit":  null
    }

Rules the validator enforces (each has a reason in the error text):

- 1–8 parameters, snake_case, numbers only; every one must be used. The FIRST
  parameter is the one the robustness test varies, so put the one that matters
  first.
- A parameter used as an indicator ``period`` must be an integer named
  ``*_period`` or ``*_lookback`` (that is how the engine infers warm-up), 2–400.
- At least one entry; each entry side needs its own exit or the opposite entry
  (otherwise a position is held until the data ends).
- At most 12 comparisons, nested at most 3 deep.

Causality: every indicator at bar ``i`` uses candles ``0..i`` only;
``highest``/``lowest`` use the bars *before* ``i`` so "close above the 20-bar
high" is a real breakout. The engine fills on the next bar's open.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ftmo_mt5.signals import indicators as ind
from ftmo_mt5.signals.strategies import Verdict

PRICE = {"close", "open", "high", "low", "volume"}
WINDOWED = {"sma", "ema", "rsi", "atr", "atr_pct", "adx", "highest", "lowest", "roc", "volume_sma"}
OPS = {">", "<", ">=", "<=", "crosses_above", "crosses_below"}
BLOCKS = ("long_entry", "long_exit", "short_entry", "short_exit")
MAX_PARAMS, MAX_CONDITIONS, MAX_DEPTH = 8, 12, 3
_SLUG = re.compile(r"^[a-z][a-z0-9_]{2,40}$")
_PARAM = re.compile(r"^[a-z][a-z0-9_]{1,30}$")
_PERIOD_NAME = re.compile(r"_(period|lookback)$")


class SpecError(ValueError):
    """A spec that breaks the contract. The message lists every problem."""


# ---------------------------------------------------------------- validation
def validate(spec: Dict[str, Any]) -> Dict[str, Any]:
    """Check a spec against the contract; return a normalised copy or raise SpecError."""
    if not isinstance(spec, dict):
        raise SpecError("spec must be a JSON object")
    errs: List[str] = []
    name = str(spec.get("name", "")).strip().lower()
    if not _SLUG.match(name):
        errs.append("name must be a 3–41 char snake_case slug starting with a letter")
    desc = str(spec.get("description", "")).strip()
    if not 10 <= len(desc) <= 400:
        errs.append("description must be 10–400 characters saying what the rule does")

    params = spec.get("params")
    if not isinstance(params, dict) or not 1 <= len(params) <= MAX_PARAMS:
        errs.append(f"params must be an object with 1–{MAX_PARAMS} entries (robustness varies the first)")
        params = {}
    for k, v in params.items():
        if not _PARAM.match(str(k)):
            errs.append(f"param '{k}': names must be snake_case")
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v == 0:
            errs.append(f"param '{k}': must be a non-zero number")

    used: Dict[str, str] = {}          # param -> how it is used ("period" or "value")
    count = [0]
    present = {}
    for block in BLOCKS:
        b = spec.get(block)
        present[block] = b is not None
        if b is not None:
            _check_block(b, block, params, used, count, errs, depth=1)
    if count[0] > MAX_CONDITIONS:
        errs.append(f"{count[0]} comparisons; the limit is {MAX_CONDITIONS} (simple rules overfit less)")

    if not (present["long_entry"] or present["short_entry"]):
        errs.append("needs long_entry and/or short_entry")
    if present["long_entry"] and not (present["long_exit"] or present["short_entry"]):
        errs.append("long_entry needs long_exit (or short_entry to reverse), or a long is held to the end of data")
    if present["short_entry"] and not (present["short_exit"] or present["long_entry"]):
        errs.append("short_entry needs short_exit (or long_entry to reverse)")
    if present["long_exit"] and not present["long_entry"]:
        errs.append("long_exit without long_entry")
    if present["short_exit"] and not present["short_entry"]:
        errs.append("short_exit without short_entry")

    for k in params:
        if k not in used:
            errs.append(f"param '{k}' is never used")
    for k, how in used.items():
        if how == "period":
            v = params.get(k)
            if not isinstance(v, int) or isinstance(v, bool):
                errs.append(f"param '{k}' is used as a period, so it must be an integer")
            elif not 2 <= v <= 400:
                errs.append(f"param '{k}' period {v} must be 2–400")
            if not _PERIOD_NAME.search(k):
                errs.append(f"param '{k}' is used as a period, so its name must end in _period or _lookback")
    if errs:
        raise SpecError("; ".join(errs))

    out = {"name": name, "description": desc, "params": dict(params)}
    for block in BLOCKS:
        out[block] = spec.get(block)
    return out


def _check_block(b: Any, where: str, params: Dict, used: Dict, count: List[int],
                 errs: List[str], depth: int) -> None:
    if depth > MAX_DEPTH:
        errs.append(f"{where}: nested deeper than {MAX_DEPTH}")
        return
    if not isinstance(b, dict) or len([k for k in ("all", "any") if k in b]) != 1:
        errs.append(f"{where}: a block is {{\"all\": [...]}} or {{\"any\": [...]}}")
        return
    key = "all" if "all" in b else "any"
    items = b[key]
    if not isinstance(items, list) or not items:
        errs.append(f"{where}.{key}: must be a non-empty list")
        return
    for n, item in enumerate(items):
        at = f"{where}.{key}[{n}]"
        if isinstance(item, dict) and ("all" in item or "any" in item):
            _check_block(item, at, params, used, count, errs, depth + 1)
            continue
        if not isinstance(item, dict) or set(item) - {"left", "op", "right"} or "op" not in item:
            errs.append(f"{at}: a comparison is {{left, op, right}}")
            continue
        count[0] += 1
        if item["op"] not in OPS:
            errs.append(f"{at}: op must be one of {sorted(OPS)}")
        for side in ("left", "right"):
            _check_expr(item.get(side), f"{at}.{side}", params, used, errs)


def _ref(v: Any, at: str, params: Dict, used: Dict, errs: List[str], how: str) -> None:
    if isinstance(v, str):
        if not v.startswith("$") or v[1:] not in params:
            errs.append(f"{at}: '{v}' is not a declared param (use \"$name\")")
            return
        if used.get(v[1:]) not in (None, how):
            errs.append(f"{at}: param '{v[1:]}' cannot be both a period and a value")
        used[v[1:]] = how
    elif isinstance(v, bool) or not isinstance(v, (int, float)):
        errs.append(f"{at}: must be a number or \"$param\"")


def _check_expr(e: Any, at: str, params: Dict, used: Dict, errs: List[str]) -> None:
    if not isinstance(e, dict):
        errs.append(f"{at}: an expression is an object")
        return
    allowed = {"ind", "period", "mult", "shift", "value"}
    if set(e) - allowed:
        errs.append(f"{at}: unknown keys {sorted(set(e) - allowed)}")
    if "value" in e:
        if "ind" in e:
            errs.append(f"{at}: use either ind or value, not both")
        _ref(e["value"], at + ".value", params, used, errs, "value")
    else:
        kind = e.get("ind")
        if kind in PRICE:
            if "period" in e:
                errs.append(f"{at}: {kind} takes no period")
        elif kind in WINDOWED:
            if "period" not in e:
                errs.append(f"{at}: {kind} needs a period")
            else:
                p = e["period"]
                if isinstance(p, str):
                    _ref(p, at + ".period", params, used, errs, "period")
                elif not isinstance(p, int) or isinstance(p, bool) or not 2 <= p <= 400:
                    errs.append(f"{at}: period must be an integer 2–400 or \"$param\"")
        else:
            errs.append(f"{at}: ind must be one of {sorted(PRICE | WINDOWED)}")
    if "mult" in e:
        _ref(e["mult"], at + ".mult", params, used, errs, "value")
    if "shift" in e and (not isinstance(e["shift"], int) or isinstance(e["shift"], bool) or not 0 <= e["shift"] <= 20):
        errs.append(f"{at}: shift must be an integer 0–20 (bars back)")


# ---------------------------------------------------------------- identity
def canonical(spec: Dict[str, Any]) -> str:
    return json.dumps({k: spec.get(k) for k in ("params",) + BLOCKS}, sort_keys=True, separators=(",", ":"))


def registered_name(spec: Dict[str, Any], prefix: str = "AI") -> str:
    """Same logic -> same name, so a re-proposed rule dedupes instead of multiplying."""
    h = hashlib.sha256(canonical(spec).encode()).hexdigest()[:6].upper()
    return f"{prefix}_{spec['name'].upper()[:28]}_{h}"


# ---------------------------------------------------------------- runtime
def _period(p: Any, params: Dict) -> int:
    return int(params[p[1:]]) if isinstance(p, str) else int(p)


def _num(v: Any, params: Dict) -> float:
    return float(params[v[1:]]) if isinstance(v, str) else float(v)


def _series(kind: str, period: int, candles: Sequence, cache: Dict) -> List[Optional[float]]:
    key = (kind, period)
    if key in cache:
        return cache[key]
    closes = cache.setdefault(("close", 0), [c.close for c in candles])
    n = len(candles)
    if kind == "sma":
        s = ind.sma(closes, period)
    elif kind == "ema":
        s = ind.ema(closes, period)
    elif kind == "rsi":
        s = ind.rsi(closes, period)
    elif kind == "atr":
        s = ind.atr(candles, period)
    elif kind == "atr_pct":
        a = _series("atr", period, candles, cache)
        s = [None if v is None or not closes[i] else v / closes[i] * 100 for i, v in enumerate(a)]
    elif kind == "adx":
        s = ind.adx(candles, period)
    elif kind == "highest":                          # of the bars BEFORE i
        s = [max(c.high for c in candles[i - period:i]) if i >= period else None for i in range(n)]
    elif kind == "lowest":
        s = [min(c.low for c in candles[i - period:i]) if i >= period else None for i in range(n)]
    elif kind == "roc":
        s = [(closes[i] / closes[i - period] - 1) * 100 if i >= period and closes[i - period] else None
             for i in range(n)]
    elif kind == "volume_sma":
        s = ind.sma([float(c.volume or 0) for c in candles], period)
    else:                                            # pragma: no cover — validator stops this
        raise SpecError(f"unknown indicator {kind}")
    cache[key] = s
    return s


class SpecStrategy:
    """A validated spec, wearing the Strategy protocol the engine already speaks."""

    def __init__(self, spec: Dict[str, Any], name: Optional[str] = None, label_prefix: str = "AI"):
        self.spec = validate(spec)
        self.name = name or registered_name(self.spec)
        self.DEFAULTS = dict(self.spec["params"])
        self.label_prefix = label_prefix

    def describe(self, params: Optional[dict] = None) -> dict:
        merged = {**self.DEFAULTS, **(params or {})}
        return {
            "name": self.name,
            "label": f"{self.label_prefix}: {self.spec['name'].replace('_', ' ')}",
            "description": self.spec["description"],
            "parameters": [{"key": k, "label": k.replace("_", " "),
                            "type": "int" if isinstance(v, int) else "float",
                            "default": v, "value": merged[k]} for k, v in self.DEFAULTS.items()],
        }

    def _params(self, params: Optional[dict]) -> Dict[str, float]:
        out = dict(self.DEFAULTS)
        for k, v in (params or {}).items():
            if k in out and v not in (None, ""):
                try:
                    out[k] = int(v) if isinstance(self.DEFAULTS[k], int) else float(v)
                except (TypeError, ValueError):
                    pass
        return out

    def prepare(self, candles: Sequence, params: dict) -> dict:
        p = self._params(params)
        cache: Dict = {}
        problems = [f"{k}={v} is not a usable period" for k, v in p.items()
                    if _PERIOD_NAME.search(k) and not 2 <= int(v) <= 1000]
        state = {"params": p, "cache": cache, "candles": candles, "side": None, "problems": problems,
                 "armed": {"long": True, "short": True}, "atr": ind.atr(candles, 14)}
        if not problems:                                         # warm every series once
            for block in BLOCKS:
                if self.spec[block]:
                    self._walk(self.spec[block], lambda e: self._expr_series(e, state))
        return state

    def _walk(self, block: Dict, fn) -> None:
        for item in block.get("all") or block.get("any"):
            if "all" in item or "any" in item:
                self._walk(item, fn)
            else:
                fn(item["left"])
                fn(item["right"])

    def _expr_series(self, e: Dict, state: dict) -> Optional[List[Optional[float]]]:
        if "value" in e:
            return None
        kind = e["ind"]
        candles = state["candles"]
        if kind in PRICE:
            key = (kind, 0)
            if key not in state["cache"]:
                state["cache"][key] = [float(getattr(c, kind) or 0) for c in candles]
            return state["cache"][key]
        return _series(kind, _period(e["period"], state["params"]), candles, state["cache"])

    def _value(self, e: Dict, i: int, state: dict) -> Optional[float]:
        p = state["params"]
        if "value" in e:
            v = _num(e["value"], p)
        else:
            j = i - int(e.get("shift", 0))
            if j < 0:
                return None
            v = self._expr_series(e, state)[j]
            if v is None:
                return None
        return v * _num(e["mult"], p) if "mult" in e else v

    def _cond(self, c: Dict, i: int, state: dict) -> Optional[bool]:
        if "all" in c or "any" in c:
            vals = [self._cond(x, i, state) for x in (c.get("all") or c.get("any"))]
            if any(v is None for v in vals):
                return None
            return all(vals) if "all" in c else any(vals)
        a, b = self._value(c["left"], i, state), self._value(c["right"], i, state)
        if a is None or b is None:
            return None
        op = c["op"]
        if op in ("crosses_above", "crosses_below"):
            a0, b0 = self._value(c["left"], i - 1, state), self._value(c["right"], i - 1, state)
            if a0 is None or b0 is None:
                return None
            return (a0 <= b0 and a > b) if op == "crosses_above" else (a0 >= b0 and a < b)
        return {">": a > b, "<": a < b, ">=": a >= b, "<=": a <= b}[op]

    def evaluate(self, candles: Sequence, i: int, state: dict) -> Verdict:
        """Decide bar i. Rules, in order:

        - an opposite entry reverses the held side (BUY/SELL — the engine reverses);
        - the held side's exit closes it (EXIT never touches the other side);
        - after an exit, that side's entry must go false once before it can
          re-enter ("re-arm"), so overlapping entry/exit conditions cannot churn;
        - both entries true at once is no opinion.
        """
        if state["problems"]:
            return Verdict("FLAT", state["problems"])
        s = self.spec
        hit = {b: (self._cond(s[b], i, state) if s[b] else False) for b in BLOCKS}
        if any(v is None for v in hit.values()):
            return Verdict("FLAT", ["warming up"])
        armed = state.setdefault("armed", {"long": True, "short": True})
        for k in ("long", "short"):
            if not hit[f"{k}_entry"]:
                armed[k] = True
        both = hit["long_entry"] and hit["short_entry"]
        enter = {k: hit[f"{k}_entry"] and armed[k] and not both for k in ("long", "short")}
        side = state["side"]
        if side is not None:
            other = "short" if side == "long" else "long"
            if enter[other]:
                state["side"] = other
                return Verdict("BUY" if other == "long" else "SELL", [f"{other} entry reverses the {side}"])
            if hit[f"{side}_exit"]:
                state["side"] = None
                armed[side] = False
                return Verdict("EXIT", [f"{side} exit conditions met"])
            return Verdict("FLAT", [f"holding {side}"])
        if enter["long"]:
            state["side"] = "long"
            return Verdict("BUY", ["long entry conditions met"])
        if enter["short"]:
            state["side"] = "short"
            return Verdict("SELL", ["short entry conditions met"])
        return Verdict("FLAT", ["both entries true — no opinion"] if both else [])


def install(strategy: SpecStrategy) -> str:
    """Make a spec strategy resolvable by name in THIS process, hidden from the panel."""
    from ftmo_mt5.signals import strategies as reg
    reg.STRATEGIES[strategy.name] = strategy
    reg.HIDDEN.add(strategy.name)
    return strategy.name


def check(spec: Dict[str, Any]) -> Tuple[bool, str]:
    """Convenience for tools: (ok, message)."""
    try:
        validate(spec)
        return True, "ok"
    except SpecError as e:
        return False, str(e)
