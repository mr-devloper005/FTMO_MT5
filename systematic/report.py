"""Plain-markdown reports for the team: what was researched, what was promoted, what was traded."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

BANNER = "> **PAPER TRADING ONLY** — simulated money. Live trading is disabled; this package has no live order path."


def _pct(x) -> str:
    return "—" if x is None else f"{x:+.1f}%"


def research_report(ranking: Dict, screened: Dict[str, List[Dict]], watchlist: List[str]) -> str:
    tally = ranking["gradeTally"]
    lines = [f"# Research — {ranking['generatedAt'][:10]}", "", BANNER, ""]
    if ranking["exploratory"]:
        lines += [f"> **EXPLORATORY RUN** — promotion floor lowered to {ranking['minGrade']}. "
                  "Anything promoted here has NOT met the MODERATE bar.", ""]
    screened_runs = sum(1 for rows in screened.values() for r in rows if "error" not in r)
    passed = sum(1 for rows in screened.values() for r in rows if r.get("passesScreen"))
    lines += [
        f"- Watchlist: {len(watchlist)} symbols ({', '.join(watchlist)})",
        f"- Screen: {screened_runs} strategy runs, {passed} passed the Lab's screen",
        f"- Validated: {sum(tally.values())} candidates — "
        + ", ".join(f"{g} {n}" for g, n in reversed(list(tally.items()))),
        f"- Promotion floor: **{ranking['minGrade']}** · promoted: **{len(ranking['promoted'])}**",
        "",
    ]
    if not ranking["promoted"]:
        lines += ["**Nothing was promoted.** No candidate reached the promotion grade, so the paper "
                  "portfolio stays in cash. That is the system working: it will not trade a rule "
                  "it cannot defend.", ""]
    lines += ["## Ranking (best rule per symbol)", "",
              "| # | Symbol | Strategy | Grade | Score | Trades | vs B&H | OOS vs B&H | Folds won | Max DD | Promoted |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
    for c in ranking["ranked"]:
        lines.append(
            f"| {c['rank']} | {c['symbol']} | {c['strategy']} | {c['verdict']} | {c.get('score', 0):.0f} | "
            f"{c.get('trades', '—')} | {_pct(c.get('vsBHPct'))} | {_pct(c.get('oosVsBHPct'))} | "
            f"{c.get('foldsWon', 0)}/{c.get('foldsRun', 0)} | {_pct(-(c.get('maxDDPct') or 0))} | "
            f"{'✅' if c.get('promoted') else ''} |")
    lines += ["", "## Why each symbol's best rule got its grade", ""]
    for c in ranking["ranked"]:
        why = "; ".join(c.get("why") or []) or "cleared every gate"
        lines.append(f"- **{c['symbol']}** ({c['strategy']}, {c['verdict']}): {why}")
    errors = [(s, r) for s, rows in screened.items() for r in rows if "error" in r and "strategy" not in r]
    if errors:
        lines += ["", "## Data problems", ""] + [f"- {s}: {r['error']}" for s, r in errors]
    return "\n".join(lines) + "\n"


def cycle_report(result: Dict) -> str:
    lines = [f"# Paper cycle — {result['at']}", "", BANNER, ""]
    if result.get("exploratory"):
        lines += ["> **EXPLORATORY** — trading rules promoted below the MODERATE bar.", ""]
    if result.get("halted"):
        lines += [f"> 🛑 **HALTED** — {result['halted']['reason']}. Trading stays stopped until a person "
                  "reviews it and runs `python -m systematic reset-halt`.", ""]
    lines += [f"- Equity: **${result['equity']}** · Balance: ${result['balance']} · Risk state: **{result['riskState']}**",
              f"- Open positions: {len(result['positions'])}", "", "## Actions this cycle", ""]
    if not result["actions"]:
        lines.append("- None. Every promoted rule already had the position it wanted, or was flat.")
    for a in result["actions"]:
        detail = ", ".join(f"{k}={v}" for k, v in a.items() if k != "kind")
        lines.append(f"- **{a['kind']}** {detail}")
    if result["positions"]:
        lines += ["", "## Positions", "", "| Symbol | Side | Qty | Entry | Last | Strategy |", "|---|---|---|---|---|---|"]
        for sym, p in result["positions"].items():
            lines.append(f"| {sym} | {p['side']} | {p['quantity']} | {p['avg_price']} | {p['last_price']} | {p.get('strategy', '')} |")
    return "\n".join(lines) + "\n"


def write(state_dir: Path, kind: str, text: str) -> Path:
    d = Path(state_dir) / "reports"
    d.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    p = d / f"{stamp}-{kind}.md"
    p.write_text(text)
    return p
