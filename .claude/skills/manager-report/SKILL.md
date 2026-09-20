---
name: manager-report
description: Write a factual progress report on FTMO_MT5 work for the user's manager. Use when asked to summarise what was done, write a status update, weekly report, or "what did I accomplish".
---

# Manager report

## Gather facts — don't recall them

Run from the repo root:

```bash
# deliverables and when they changed
for f in *.md *.pptx mcp_server/*.py ftmo_mt5/signals/*.py ftmo_mt5/lab/*.py .mcp.json; do
  [ -f "$f" ] && printf "%s  %s\n" "$(stat -f '%Sm' -t '%b %d %H:%M' "$f")" "$f"
done | sort

# tests
PYTHONPATH=. venv/bin/python3 -m pytest tests/test_lab.py tests/test_backtest.py tests/test_mtf_strategy.py mcp_server/test_server.py -q | tail -1

# research volume and verdicts
PYTHONPATH=. venv/bin/python3 -c "from mcp_server.results_index import stats; print(stats())"
```

Use the reporting period the user names. If none, use the last 7 days by file date.

## Structure

1. **Period and time** — dates, and hours if the user gave them (they work ~20 hrs/week part-time).
2. **Delivered** — grouped: research, tooling built, docs/planning, integrations. Each item concrete: what exists now that didn't.
3. **Key finding** — the research result stated plainly, including negative results. Current standing result: no strategy has reached MODERATE or ROBUST; buy & hold is undefeated at 2h+ timeframes.
4. **Numbers** — tests passing, lines of code, backtests indexed.
5. **Blockers / risks** — what's down or unproven.
6. **Next** — 2–4 items.

## Rules

- Every number comes from the commands above, not memory.
- Say **"designed and validated"** or **"directed"** for AI-built work, not "wrote" or "hand-coded".
- Separate **capability delivered** from **trading results**. Never imply a profitable strategy exists.
- No hype words ("revolutionary", "game-changing"). Keep it one screen.
- Offer an honest one-paragraph assessment only if the user asks for one.
