---
name: add-strategy
description: Add a new trading strategy or exit rule to the FTMO_MT5 strategy library so the Lab, backtests and MCP tools can use it. Use when asked to create, implement, or add a strategy, signal, entry rule, or exit rule.
---

# Add a strategy

## Where it goes

- New file in `ftmo_mt5/signals/` (e.g. `my_strategies.py`). Don't grow `strategies.py` (2,290 lines).
- Register at the bottom of the file: `STRATEGIES[MyStrategy.name] = MyStrategy()`
- Import the module for its side effect at the end of `ftmo_mt5/signals/__init__.py`, after `strategies`:
  `from ftmo_mt5.signals import my_strategies  # noqa: F401,E402`

Pattern to copy: `ftmo_mt5/signals/hold_strategies.py`.

## The contract

Import `STRATEGIES, Verdict, _param` from `ftmo_mt5.signals.strategies`. A strategy class needs:

- `name` — UPPER_SNAKE, unique
- `DEFAULTS` — dict of default params
- `describe(params)` → `{name, label, description, parameters: [{key, label, type, default, value}]}`
- `prepare(candles, params)` → state dict. Compute indicators once here (`ftmo_mt5.signals.indicators`: `sma`, `ema`, `atr`, `rsi`, `adx`). Put unfixable config issues in `state["problems"]`.
- `evaluate(candles, i, state)` → `Verdict("BUY" | "SELL" | "EXIT" | "FLAT", [reasons], readings)`

## Hard rules

- **Parameter `type` must be `int`, `float`, or `bool`.** No `str`. A registry test enforces it; a `str` param broke it once. Two modes → a `bool`.
- **No look-ahead.** `evaluate` at bar `i` may read only bars `0..i`. Any "prior high" etc. must exclude bar `i` itself.
- Read params with `_param(params, key, default, cast)`, and parse string booleans ("false", "0").
- `SELL` opens a short; `EXIT` only closes. Use `EXIT` for long-only exits.
- Don't implement a trailing stop inside the strategy — the engine has `trailing_stop_atr`. If a strategy defers its exit to that stop, gate re-entry (e.g. on a new high) or it re-buys the next bar and the stop does nothing.

## Done means

1. Tests pass (165 at time of writing):
   `PYTHONPATH=. venv/bin/python3 -m pytest tests/test_lab.py tests/test_backtest.py tests/test_mtf_strategy.py mcp_server/test_server.py -q`
2. Update the strategy count assertion in `mcp_server/test_server.py` (`test_list_strategies_returns_the_real_catalog`).
3. `lab_list_strategies` shows the new name.
4. Canary: `BUY_AND_HOLD_BASELINE` on the same symbol/timeframe still returns within ~2pp of its `bhReturnPct` — if not, the engine changed.
5. Test the new strategy with the `lab-research` skill and report the honest result.
