---
name: cross-review
description: Get Claude-built FTMO_MT5 changes independently reviewed by another model (OpenAI Codex), then act on the findings. Use when asked to review changes, run a Codex review, get a second opinion, or check work before merging.
---

# Cross-model review

Claude builds, Codex reviews cold. The value comes from the reviewer **not** knowing why the code was written — so never pass it Claude's reasoning.

## Preconditions — check, don't assume

```bash
git rev-parse --is-inside-work-tree   # FTMO_MT5 had no git repo
command -v codex                      # Codex CLI installed?
```

- **No git:** stop and tell the user. Offer: `git init && git add -A && git commit -m "baseline"`. Don't run it without a yes — check `.gitignore` covers `.env`, `venv/`, `logs/`, caches first.
- **No codex:** stop and tell the user to install the Codex CLI and sign in. Don't install it yourself.

## Steps

1. **Work on a branch.** `git switch -c <short-name>`; commit the change.
2. **Run tests first.** A failing change doesn't go to review:
   `PYTHONPATH=. venv/bin/python3 -m pytest tests/test_lab.py tests/test_backtest.py tests/test_mtf_strategy.py mcp_server/test_server.py -q`
3. **Review.** Run `codex review` against the branch diff. Give it only the diff plus `.claude/skills/cross-review/STANDARDS.md` if present — no explanation of intent.
4. **Triage each finding** into: fix, reject (with a one-line reason), or ask the user.
5. **Fix** the accepted ones, re-run tests, and re-review until no blocking findings remain.
6. **Report** a table: finding · verdict (fixed/rejected/asked) · reason.

## Merge gate

Merge only when all are true:
- tests pass
- no blocking review findings
- nothing touches order placement, credentials, or `.env` without the user explicitly approving

Never merge, push, or force-push without the user's go-ahead.

## Standards to review against

- `Decimal` for money; no floats reaching orders
- No look-ahead in strategies (bar `i` reads only `0..i`)
- Strategy param types only `int`/`float`/`bool`
- `mcp_server/` never imports `ftmo_mt5.execution`, `ftmo_mt5.agents`, or `ftmo_mt5.main`
- Designed refusals raise `ToolError`, not `ValueError`
- No credential values in code, logs, or output
