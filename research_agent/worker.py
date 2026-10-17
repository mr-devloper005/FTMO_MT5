"""The 24/7 research worker: a separate process that runs research cycles on a schedule.

    python -m research_agent worker        (normally started by the Control Center)

One loop, ticking every 2 seconds:

    heartbeat -> read desired state -> reconcile
        stopped              cancel any run, exit
        paused               finish the current run, start nothing new (except Run now)
        running              start a cycle when next_run_at is due (or Run now)

A cycle is exactly ``agent.run_agent`` — the same Claude Agent SDK session, the
same 13 research-only tools, the same safety layers. The worker adds only
scheduling, budgets and live progress: every transcript entry becomes an
activity event and moves the stage/progress the UI shows.

Recovery: state is in SQLite. A worker that starts and finds a run marked
in-progress (the previous process died) records it as interrupted and carries
on with the schedule. Only one worker runs at a time.

Guards for unattended running: a per-run budget (hard cap in the SDK), a daily
budget (no new run if it would be exceeded), and auto-pause after
MAX_CONSECUTIVE_FAILURES failed runs (e.g. an expired login) so a broken setup
does not loop.
"""
from __future__ import annotations

import asyncio
import json
import os

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from research_agent import agent, oscompat
from research_agent.config import Settings
from research_agent.control import (MAX_CONSECUTIVE_FAILURES, STAGE_OF, STAGES, Control, _iso, _parse)
from research_agent.pipeline import new_run_id
from research_agent.store import now

TICK_S = 2.0


class Worker:
    def __init__(self, settings: Settings, hard_exit: bool = False):
        self.settings = settings
        self.hard_exit = hard_exit
        self.ctl = Control(settings)
        self.task: Optional[asyncio.Task] = None
        self.run_id: Optional[str] = None
        self.trigger: Optional[str] = None
        self.progress = 0.0
        self.stop_requested = False

    # ---- lifecycle -------------------------------------------------------
    def claim(self) -> bool:
        """Single instance: refuse to start while another live worker owns the row."""
        r = self.ctl.row()
        if r["pid"] and r["pid"] != os.getpid() and self.ctl.worker_alive(r) and r["status"] != "starting":
            print(f"another worker (pid {r['pid']}) is alive; exiting", flush=True)
            return False
        if r["current_run_id"]:                               # previous process died mid-run
            self.ctl.store.finish_run(r["current_run_id"], {"run_id": r["current_run_id"], "outcome": "interrupted",
                                                            "error": "worker stopped unexpectedly (recovered)"})
            self.ctl.event("lifecycle", f"Recovered: run {r['current_run_id']} was interrupted by a restart",
                           run_id=r["current_run_id"])
        self.ctl.update(pid=os.getpid(), status="idle", heartbeat_at=now(), worker_started_at=now(),
                        current_run_id=None, stage=None, task=None, progress=0)
        self.ctl.event("lifecycle", f"Worker online (pid {os.getpid()})")
        return True

    async def main(self) -> None:
        if not self.claim():
            return
        loop = asyncio.get_running_loop()
        oscompat.install_stop_handlers(loop, self._on_signal)   # POSIX signals / Windows Ctrl+C, Ctrl+Break
        try:
            while True:
                if await self.tick():
                    break
                await asyncio.sleep(TICK_S)
        finally:
            await self._cancel_run()
            self.ctl.update(status="stopped", pid=None, current_run_id=None, stage=None, task=None)
            self.ctl.event("lifecycle", "Worker offline")
            if self.hard_exit:
                # A cancelled run can leave a blocking backtest thread behind; state is
                # already committed, so don't let interpreter shutdown wait for it.
                os._exit(0)

    def _on_signal(self) -> None:
        self.stop_requested = True

    # ---- reconcile -------------------------------------------------------
    async def tick(self) -> bool:
        """One reconcile step. Returns True when the worker should exit."""
        self.ctl.update(heartbeat_at=now(), pid=os.getpid())
        r = self.ctl.row()

        if self.task and self.task.done():
            self._collect()
            r = self.ctl.row()

        if self.stop_requested or (r["desired"] == "stopped" and not r["run_now"]):
            return True

        busy = self.task is not None
        if busy:
            self.ctl.update(status="running")
            return False

        due = r["desired"] == "running" and (not r["next_run_at"]
                                              or _parse(r["next_run_at"]) <= datetime.now(timezone.utc))
        if r["run_now"] or due:
            self._start(trigger="manual" if r["run_now"] else "schedule")
        else:
            self.ctl.update(status="paused" if r["desired"] == "paused" else "idle")
        return False

    def _start(self, trigger: str) -> None:
        cfg = self.ctl.agent_settings()
        spent = self.ctl.spent_today()
        self.ctl.update(run_now=0)
        if spent + cfg.budget_usd > cfg.daily_budget_usd:
            # Not an error: wait for tomorrow's budget, re-check every interval.
            nxt = datetime.now(timezone.utc) + timedelta(minutes=cfg.interval_minutes)
            self.ctl.update(next_run_at=_iso(nxt), last_error=None)
            self.ctl.event("budget", f"Skipped: today's spend ${spent:.2f} + run budget ${cfg.budget_usd:.2f} "
                                     f"would pass the daily cap ${cfg.daily_budget_usd:.2f}. Next check {_iso(nxt)}")
            return
        self.run_id = new_run_id("agent")
        self.trigger = trigger
        self.progress = 1.0
        self.ctl.update(status="running", current_run_id=self.run_id, run_started_at=now(), trigger=trigger,
                        stage="starting", task="Starting Claude research session", progress=1.0, last_error=None)
        self.ctl.event("run", f"Run started ({trigger}) — shortlist {cfg.shortlist_size}, up to "
                              f"{cfg.max_new_strategies} new strategies, budget ${cfg.budget_usd:.2f}, model {cfg.model}",
                       run_id=self.run_id, stage="starting")
        self.task = asyncio.create_task(agent.run_agent(
            self.settings, shortlist_size=cfg.shortlist_size, max_new_strategies=cfg.max_new_strategies,
            model=cfg.model, max_turns=cfg.max_turns, max_budget_usd=cfg.budget_usd, focus=cfg.focus,
            echo=lambda m: None, on_event=self._on_event, run_id=self.run_id))

    def _collect(self) -> None:
        """A run finished (or failed, or was cancelled): record it and schedule the next."""
        task, run_id = self.task, self.run_id
        self.task, self.run_id = None, None
        cfg = self.ctl.agent_settings()
        r = self.ctl.row()
        try:
            summary: Dict[str, Any] = task.result()
        except asyncio.CancelledError:
            summary = {"outcome": "cancelled", "error": "cancelled by the operator (Stop)"}
        except Exception as e:                                   # noqa: BLE001
            summary = {"outcome": "failed", "error": f"{type(e).__name__}: {str(e)[:400]}"}
        failed = summary.get("outcome") in ("failed",) or bool(summary.get("error")) and summary.get("outcome") != "cancelled"
        failures = (r["consecutive_failures"] or 0) + 1 if failed else 0
        nxt = datetime.now(timezone.utc) + timedelta(minutes=cfg.interval_minutes)
        brief = {k: summary.get(k) for k in ("outcome", "cost_usd", "turns", "tool_calls", "strategies_proposed",
                                             "backtests_charged", "seconds", "error", "fix", "reports", "exports")}
        changes: Dict[str, Any] = dict(current_run_id=None, stage=None, task=None, progress=0, last_run_id=run_id,
                                       last_run_at=now(), last_result_json=json.dumps(brief, default=str),
                                       last_error=summary.get("error") if failed else None,
                                       consecutive_failures=failures, next_run_at=_iso(nxt))
        if failures >= MAX_CONSECUTIVE_FAILURES and r["desired"] == "running":
            changes.update(desired="paused", pause_reason=f"auto-paused after {failures} failed runs — "
                                                          f"{summary.get('fix') or summary.get('error')}")
        self.ctl.update(**changes)
        cost = summary.get("cost_usd")
        msg = (f"Run {summary.get('outcome', 'completed')}"
               + (f" — ${cost:.2f}" if isinstance(cost, (int, float)) else "")
               + (f", {summary.get('turns')} turns, {summary.get('tool_calls')} tool calls" if summary.get("turns") else "")
               + (f". Error: {summary.get('error')}" if summary.get("error") else ""))
        self.ctl.event("run", msg, run_id=run_id, stage="done", data=brief)
        if "desired" in changes:
            self.ctl.event("lifecycle", changes["pause_reason"], run_id=run_id)

    async def _cancel_run(self) -> None:
        if self.task and not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except (asyncio.CancelledError, Exception):          # noqa: BLE001
                pass
        if self.task:
            self._collect()

    # ---- live progress -----------------------------------------------------
    def _on_event(self, e: Dict[str, Any]) -> None:
        """Transcript entry -> activity event + stage/progress (called from the run, possibly off-thread)."""
        kind, run_id = e.get("type"), e.get("run_id")
        tool = (e.get("tool") or "").replace("mcp__research__", "")
        stage = STAGE_OF.get(tool, (None, None, None))
        if kind == "tool_use":
            key, label, pct = stage
            if key:
                self.progress = max(self.progress, float(pct))
                self.ctl.update(stage=key, task=f"{label}: {tool}", progress=self.progress)
            self.ctl.event("tool", f"→ {tool} {json.dumps(e.get('input') or {}, default=str)[:300]}",
                           run_id=run_id, stage=key)
        elif kind == "tool_call":
            brief = e.get("brief") or {}
            bits = ", ".join(f"{k}={v}" for k, v in brief.items() if k not in ("contract",))[:400]
            ok = "✓" if e.get("ok") else "✗"
            key = stage[0]
            if key:                                          # creep forward within the stage
                nxt = {k: p for k, _, p, _ in STAGES}
                order = [k for k, _, _, _ in STAGES]
                ceiling = nxt[order[order.index(key) + 1]] - 2 if order.index(key) + 1 < len(order) else 98
                self.progress = min(ceiling, self.progress + 3)
                self.ctl.update(progress=self.progress)
            self.ctl.event("tool_result", f"{ok} {tool} ({e.get('seconds')}s){': ' + bits if bits else ''}",
                           run_id=run_id, stage=key, data={"args": e.get("args"), "brief": brief})
        elif kind == "progress":
            self.ctl.update(task=f"Screening NASDAQ: {e.get('message')}")
            self.ctl.event("progress", e.get("message", ""), run_id=run_id, stage="screening")
        elif kind == "text":
            self.ctl.event("agent", (e.get("text") or "").strip()[:1500], run_id=run_id)
        elif kind == "result":
            self.progress = 100.0
            self.ctl.update(progress=100.0, task="Finishing")


def run(settings: Optional[Settings] = None) -> None:
    asyncio.run(Worker(settings or Settings(), hard_exit=True).main())
