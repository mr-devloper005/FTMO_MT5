# FTMO_MT5 Lab — MCP Server

Implements Phase 1 of [`../IMPLEMENTATION_PLAN.md`](../IMPLEMENTATION_PLAN.md), per the ten decisions settled in this project's MCP design session. Four tools, all read-only research over the existing `ftmo_mt5/lab/` — nothing here can place a trade, touch a broker, or read a credential (see the import-boundary note in `server.py`).

## Tools

| Tool | What it does | Trust level |
|---|---|---|
| `lab_list_strategies` | The real strategy catalog | Reference data |
| `lab_run_backtest` | One symbol, one timeframe, one strategy | In-sample screen only |
| `lab_run_matrix` | Symbols × timeframes × strategies, one call (≤300 cells) | In-sample screen only |
| `lab_validate_strategy` | The full OOS / walk-forward / cost-ladder robustness suite | The only tool whose output counts as evidence |

**A "beats Buy & Hold" result from `lab_run_backtest` or `lab_run_matrix` is a lead, not a finding.** Always confirm with `lab_validate_strategy` before treating anything as real — that discipline is the entire reason this session's research held up under scrutiny.

## Install

```bash
cd /Users/yashnihalani/Downloads/FTMO_MT5
venv/bin/python3 -m pip install -r mcp_server/requirements.txt
```

(Already done in this session's venv — this is here for a fresh checkout.)

## Run standalone (smoke test)

```bash
cd /Users/yashnihalani/Downloads/FTMO_MT5
PYTHONPATH=. venv/bin/python3 -m pytest mcp_server/test_server.py -v
```

## Connect it to Claude Code

Already wired: [`../.mcp.json`](../.mcp.json) at the FTMO_MT5 project root. Launch Claude Code from `/Users/yashnihalani/Downloads/FTMO_MT5` and the four tools are available.

```json
{
  "mcpServers": {
    "ftmo-lab": {
      "command": "/Users/yashnihalani/Downloads/FTMO_MT5/venv/bin/python3",
      "args": ["/Users/yashnihalani/Downloads/FTMO_MT5/mcp_server/server.py"],
      "env": { "PYTHONPATH": "/Users/yashnihalani/Downloads/FTMO_MT5" }
    }
  }
}
```

**`env.PYTHONPATH` is not optional and absolute paths are not cosmetic.** Launching `server.py` by path puts `mcp_server/` on `sys.path`, not the repo root, so `from ftmo_mt5.lab import ...` fails with `ModuleNotFoundError: No module named 'ftmo_mt5'`. Verified by actually running it both ways — a `cwd`-plus-relative-path config does *not* work here.

## Connect it to Claude Desktop

Same block, in `~/Library/Application Support/Claude/claude_desktop_config.json`. Restart the client after editing. Nothing about the server differs between the two clients — that's the point of stdio transport.

## Verified end-to-end

Launched over the real MCP protocol with exactly the config above (not just in-process function calls):

```
server: ftmo-lab 0.1.0
tools : ['lab_list_strategies', 'lab_run_backtest', 'lab_run_matrix', 'lab_validate_strategy']
validate LITE/1h/MEAN_REVERSION -> verdict=FRAGILE trades=10 vsBH=+52.28pp
backtest AMAT/1h/MEAN_REVERSION -> trades=11 beatsBH=True vsBH=+8.18pp
matrix 2x1x2                    -> cells=4 beats=1 thin=1 err=0
cache guard                     -> is_error=True, readable message, no traceback
timeframe guard                 -> is_error=True, readable message, no traceback
```

## SDK note (mcp 2.2.0)

Two things that bite when following v1-era docs:

- `FastMCP` is now `MCPServer` (`from mcp.server.mcpserver import MCPServer`).
- A tool that raises anything other than `ToolError` is reported as `UnexpectedToolError` **with a full crash traceback**. Designed refusals — the cache guard, an unknown timeframe, the 300-cell cap — therefore raise `ToolError`, so they reach the agent as a readable message instead of looking like a server bug. This was caught only by testing over the protocol; in-process calls hid it entirely.
- A tool returning a plain `dict` arrives as `structured_content == {"result": {...}}`. Unwrap that envelope, or read `content[0].text`.

## Design decisions this implements (from the grilling session)

1. Client-agnostic — stdio transport, same server file for both clients above.
2. Canonical defaults baked into every tool signature (`bars=600`, `allow_short=True`, `size_to_capital=True`) — the exact values that, when a script silently used different ones mid-session, produced a real bug (LITE/1h showing 5 trades vs. 10 depending on which script ran). That bug is now structurally impossible through these tools.
3. Cache-first — `allow_network` defaults to `False`; an uncached symbol/timeframe is refused with a clear message, never silently fetched.
4. `lab_run_matrix` defaults to a condensed summary (top 10 by alpha + counts), not raw cells — `full_detail=True` opts into everything.
5. A bad matrix cell becomes an error entry inside a successful response (matches `service.matrix()`'s own behavior); single-target tools raise real errors.
6. `lab_` prefix on every tool name.

## What's deliberately not here

`predict_stance` (the Lab's non-forecasting stance tool) — deferred per the original design decision, since its output needs its FRAGILE/OOS-status caveats baked in before it's safe to expose, and that hasn't been designed yet. No live-trading tool of any kind, ever, by import-boundary construction, not by convention.
