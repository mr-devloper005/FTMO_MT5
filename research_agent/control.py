"""Control plane for the 24/7 research worker: state, commands, settings, activity.

Everything lives in the research SQLite store, so the UI, the API and the
worker process share one truth and the worker can recover after any restart:

    agent_worker   one row — desired state (running / paused / stopped), the
                   worker's heartbeat and pid, current run, stage, progress,
                   next/last run, failures, settings, and a "run now" flag
    agent_events   append-only activity feed (tool calls, agent messages,
                   progress lines, lifecycle events)

Commands only change *desired* state; the worker reconciles. Start/Run now
also make sure a worker process exists. The worker is a separate OS process
started with a scrubbed environment: the FTMO hub holds broker credentials in
its environment, and none of them are passed on.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from research_agent.config import DEFAULT_STATE, REPO, Settings
from research_agent.store import Store, now

SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_worker (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    desired TEXT NOT NULL DEFAULT 'stopped',
    status TEXT NOT NULL DEFAULT 'stopped',
    pid INTEGER, heartbeat_at TEXT, worker_started_at TEXT,
    current_run_id TEXT, run_started_at TEXT, trigger TEXT,
    stage TEXT, task TEXT, progress REAL DEFAULT 0,
    next_run_at TEXT, last_run_id TEXT, last_run_at TEXT, last_result_json TEXT,
    last_error TEXT, consecutive_failures INTEGER DEFAULT 0, pause_reason TEXT,
    run_now INTEGER DEFAULT 0, settings_json TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS agent_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, run_id TEXT,
    kind TEXT NOT NULL, stage TEXT, message TEXT NOT NULL, data_json TEXT
);
CREATE INDEX IF NOT EXISTS agent_events_run ON agent_events(run_id);
"""

HEARTBEAT_STALE_S = 30
MAX_CONSECUTIVE_FAILURES = 3

# The workflow, as the UI shows it. Each research tool belongs to one stage.
STAGES = [
    ("screening", "Screening NASDAQ", 5, {"refresh_universe", "screen_universe", "get_shortlist"}),
    ("strategies", "AI strategy selection & creation", 30,
     {"market_snapshot", "list_strategies", "strategy_schema", "propose_strategy"}),
    ("backtesting", "Backtesting vs Buy & Hold", 50, {"run_backtests", "validate_top", "lookup_results"}),
    ("leaderboard", "Leaderboard & report", 85, {"get_leaderboard", "export_leaderboard", "write_report"}),
]
STAGE_OF = {tool: (key, label, pct) for key, label, pct, ts in STAGES for tool in ts}


@dataclass
class AgentSettings:
    interval_minutes: int = 240            # time between scheduled runs
    shortlist_size: int = 15
    max_new_strategies: int = 4
    budget_usd: float = 5.0                # hard cap per run (Claude Agent SDK max_budget_usd)
    daily_budget_usd: float = 20.0         # no new run starts once today's spend would pass this
    max_turns: int = 60
    model: str = "sonnet"
    focus: str = ""

    LIMITS = {"interval_minutes": (30, 10080), "shortlist_size": (3, 30), "max_new_strategies": (0, 8),
              "budget_usd": (0.5, 25.0), "daily_budget_usd": (1.0, 200.0), "max_turns": (10, 150)}

    @classmethod
    def from_json(cls, raw: Optional[str]) -> "AgentSettings":
        d = json.loads(raw) if raw else {}
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})

    def updated(self, changes: Dict[str, Any]) -> "AgentSettings":
        d = asdict(self)
        for k, v in (changes or {}).items():
            if k not in d:
                raise ValueError(f"unknown setting '{k}'")
            if k in self.LIMITS:
                lo, hi = self.LIMITS[k]
                try:
                    v = type(d[k])(v)
                except (TypeError, ValueError) as e:
                    raise ValueError(f"{k} must be a number") from e
                if not lo <= v <= hi:
                    raise ValueError(f"{k} must be between {lo} and {hi}")
            elif k == "model":
                v = str(v).strip()
                if v not in ("sonnet", "opus", "haiku", "fable") and not v.startswith("claude-"):
                    raise ValueError("model must be sonnet, opus, haiku, fable or a full claude-* model id")
            elif k == "focus":
                v = str(v).strip()[:500]
            d[k] = v
        if d["daily_budget_usd"] < d["budget_usd"]:
            raise ValueError("daily budget must be at least the per-run budget")
        return AgentSettings(**d)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _parse(ts: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(ts) if ts else None


class Control:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.store = Store(settings.db_path)
        with self.store.conn() as c:
            c.executescript(SCHEMA)
            c.execute("INSERT OR IGNORE INTO agent_worker(id, settings_json, updated_at) VALUES(1, ?, ?)",
                      (json.dumps(asdict(AgentSettings())), now()))

    # ---- state -----------------------------------------------------------
    def row(self) -> Dict[str, Any]:
        with self.store.conn() as c:
            return dict(c.execute("SELECT * FROM agent_worker WHERE id=1").fetchone())

    def update(self, **fields_: Any) -> None:
        fields_["updated_at"] = now()
        cols = ", ".join(f"{k}=?" for k in fields_)
        with self.store.conn() as c:
            c.execute(f"UPDATE agent_worker SET {cols} WHERE id=1", list(fields_.values()))

    def agent_settings(self) -> AgentSettings:
        return AgentSettings.from_json(self.row()["settings_json"])

    def save_settings(self, changes: Dict[str, Any]) -> AgentSettings:
        s = self.agent_settings().updated(changes)
        self.update(settings_json=json.dumps(asdict(s)))
        r = self.row()
        if "interval_minutes" in changes and r["desired"] == "running" and r["last_run_at"]:
            self.update(next_run_at=_iso(_parse(r["last_run_at"]) + timedelta(minutes=s.interval_minutes)))
        self.event("settings", "Settings updated: " + ", ".join(f"{k}={v}" for k, v in changes.items()))
        return s

    # ---- events ----------------------------------------------------------
    def event(self, kind: str, message: str, run_id: Optional[str] = None, stage: Optional[str] = None,
              data: Optional[Dict[str, Any]] = None) -> None:
        with self.store.conn() as c:
            c.execute("INSERT INTO agent_events(at, run_id, kind, stage, message, data_json) VALUES(?,?,?,?,?,?)",
                      (now(), run_id, kind, stage, message[:2000], json.dumps(data, default=str) if data else None))

    def events(self, after: int = 0, limit: int = 200, run_id: Optional[str] = None) -> List[Dict[str, Any]]:
        q, args = "SELECT * FROM agent_events WHERE id > ?", [after]
        if run_id:
            q += " AND run_id = ?"
            args.append(run_id)
        with self.store.conn() as c:
            rows = [dict(r) for r in c.execute(q + " ORDER BY id DESC LIMIT ?", args + [min(limit, 1000)])]
        for r in rows:
            r["data"] = json.loads(r.pop("data_json")) if r.get("data_json") else None
        return list(reversed(rows))

    # ---- process ---------------------------------------------------------
    def worker_alive(self, r: Optional[Dict[str, Any]] = None) -> bool:
        r = r or self.row()
        if not r["pid"] or not r["heartbeat_at"]:
            return False
        try:
            os.kill(r["pid"], 0)
        except (ProcessLookupError, PermissionError):
            return False
        age = (datetime.now(timezone.utc) - _parse(r["heartbeat_at"])).total_seconds()
        return age < HEARTBEAT_STALE_S

    def spawn_worker(self) -> int:
        """Start `python -m research_agent worker` as its own session, with a scrubbed environment."""
        keep = ("PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "TMPDIR", "SHELL",
                "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "LAB_CACHE_DIR",
                "GOOGLE_SERVICE_ACCOUNT_FILE", "RESEARCH_SHEET_ID")
        env = {k: os.environ[k] for k in keep if k in os.environ}
        env["PYTHONPATH"] = str(REPO)
        env["RESEARCH_AGENT_STATE"] = str(self.settings.state_dir)
        env["PYTHONUNBUFFERED"] = "1"
        self.settings.ensure_dirs()
        log = (self.settings.state_dir / "worker.log").open("a")
        proc = subprocess.Popen([sys.executable, "-m", "research_agent", "worker"], cwd=str(REPO), env=env,
                                stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                start_new_session=True)
        # Reap it when it exits, so a dead worker never lingers as a zombie that still "has" a pid.
        threading.Thread(target=proc.wait, name="research-worker-reaper", daemon=True).start()
        self.update(status="starting", pid=proc.pid, heartbeat_at=now())
        self.event("lifecycle", f"Worker process started (pid {proc.pid})")
        return proc.pid

    def ensure_worker(self) -> bool:
        """Recover: if the agent should be on and no worker is alive, start one."""
        r = self.row()
        if (r["desired"] in ("running", "paused") or r["run_now"]) and not self.worker_alive(r):
            self.spawn_worker()
            return True
        return False

    # ---- commands ----------------------------------------------------------
    def start(self) -> Dict[str, Any]:
        r = self.row()
        nxt = r["next_run_at"]
        if not nxt or r["desired"] == "stopped":
            nxt = now()                                    # first start: run right away
        self.update(desired="running", next_run_at=nxt, pause_reason=None, consecutive_failures=0)
        self.event("command", "Start — 24/7 schedule on")
        self.ensure_worker()
        return self.status()

    def pause(self) -> Dict[str, Any]:
        self.update(desired="paused", pause_reason="paused by operator")
        self.event("command", "Pause — no new runs will start (a run in progress finishes)")
        return self.status()

    def stop(self) -> Dict[str, Any]:
        self.update(desired="stopped", run_now=0, pause_reason=None)
        self.event("command", "Stop — cancelling any run and shutting the worker down")
        return self.status()

    def run_now(self) -> Dict[str, Any]:
        r = self.row()
        changes: Dict[str, Any] = {"run_now": 1}
        if r["desired"] == "stopped":
            changes["desired"] = "paused"                  # run once, then idle without a schedule
            changes["pause_reason"] = "One-off run — the 24/7 schedule is off."
        self.update(**changes)
        self.event("command", "Run now requested")
        self.ensure_worker()
        return self.status()

    # ---- read model ------------------------------------------------------
    def spent_today(self) -> float:
        day = datetime.now(timezone.utc).date().isoformat()
        with self.store.conn() as c:
            rows = c.execute("SELECT summary_json FROM runs WHERE kind='agent' AND substr(started_at,1,10)=?",
                             (day,)).fetchall()
        return round(sum((json.loads(r[0] or "{}").get("cost_usd") or 0.0) for r in rows), 4)

    def history(self, limit: int = 20) -> List[Dict[str, Any]]:
        with self.store.conn() as c:
            rows = [dict(r) for r in c.execute(
                "SELECT run_id, started_at, finished_at, summary_json FROM runs WHERE kind='agent' "
                "ORDER BY started_at DESC LIMIT ?", (limit,))]
        out = []
        for r in rows:
            s = json.loads(r.pop("summary_json") or "{}")
            r.update({k: s.get(k) for k in ("outcome", "cost_usd", "turns", "tool_calls", "strategies_proposed",
                                             "backtests_charged", "seconds", "error", "fix", "reports", "exports")})
            if not r.get("outcome") and r["finished_at"]:       # runs from before outcomes were recorded
                r["outcome"] = "failed" if s.get("error") else "completed"
            if not r["finished_at"]:
                r["outcome"] = "running" if r["run_id"] == self.row()["current_run_id"] else "interrupted"
            out.append(r)
        return out

    def status(self) -> Dict[str, Any]:
        r = self.row()
        alive = self.worker_alive(r)
        s = AgentSettings.from_json(r["settings_json"])
        if r["current_run_id"] and alive and r["desired"] == "stopped":
            state = "stopping"                     # cancelling the Claude session takes a few seconds
        elif r["current_run_id"] and alive:
            state = "running"
        elif r["desired"] == "running" and alive:
            state = "scheduled"
        elif r["desired"] == "running":
            state = "starting" if r["status"] == "starting" else "offline"
        elif r["desired"] == "paused":
            state = "paused"
        else:
            state = "stopped"
        return {
            "state": state, "desired": r["desired"], "worker_alive": alive, "pid": r["pid"] if alive else None,
            "heartbeat_at": r["heartbeat_at"], "worker_started_at": r["worker_started_at"],
            "current_run_id": r["current_run_id"], "run_started_at": r["run_started_at"], "trigger": r["trigger"],
            "stage": r["stage"], "task": r["task"], "progress": r["progress"] or 0,
            "next_run_at": r["next_run_at"] if r["desired"] == "running" else None,
            "last_run_id": r["last_run_id"], "last_run_at": r["last_run_at"],
            "last_result": json.loads(r["last_result_json"]) if r["last_result_json"] else None,
            "last_error": r["last_error"], "consecutive_failures": r["consecutive_failures"],
            "pause_reason": r["pause_reason"], "run_now_pending": bool(r["run_now"]),
            "settings": asdict(s), "spent_today_usd": self.spent_today(),
            "stages": [{"key": k, "label": label} for k, label, _, _ in STAGES],
            "now": now(),
        }


def control(settings: Optional[Settings] = None) -> Control:
    return Control(settings or Settings(state_dir=Path(os.getenv("RESEARCH_AGENT_STATE", DEFAULT_STATE))))


def stop_process(pid: int) -> None:
    """Last resort for a worker that ignores Stop (used by tests and the CLI)."""
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass
