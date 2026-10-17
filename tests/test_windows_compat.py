"""Windows compatibility of the research runtime (research_agent, rag and the modules they load).

Runs on macOS/Linux: the Windows branches are exercised by forcing ``windows=True`` or by
static checks of the source. What can only be proven on a Windows PC is listed in
deploy/windows/WINDOWS_VERIFICATION.md.
"""
from __future__ import annotations

import ast
import asyncio
import os
import re
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from research_agent import oscompat  # noqa: E402

# Every project module the deployed research runtime imports (research_agent + rag + what they pull in).
RUNTIME_DIRS = ["research_agent", "rag"]
RUNTIME_FILES = ["ftmo_mt5/lab/windows.py", "ftmo_mt5/lab/metrics.py", "ftmo_mt5/lab/service.py", "ftmo_mt5/lab/verdict.py",
                 "ftmo_mt5/lab/store.py", "ftmo_mt5/backtest/engine.py", "ftmo_mt5/marketdata/yahoo.py",
                 "ftmo_mt5/marketdata/gateway.py", "ftmo_mt5/core/constants.py", "ftmo_mt5/core/models.py",
                 "systematic/data.py", "systematic/config.py", "systematic/research.py", "mcp_server/results_index.py"]


def runtime_sources():
    files = [p for d in RUNTIME_DIRS for p in (REPO / d).rglob("*.py") if "state" not in p.parts]
    return files + [REPO / f for f in RUNTIME_FILES if (REPO / f).exists()]


# ------------------------------------------------------------------ static guards
def _text_io_without_encoding(path: Path):
    """Calls that open a file in TEXT mode without encoding=: Windows would use cp1252."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    bad = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        name = f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else None
        kw = {k.arg for k in node.keywords}
        if name in ("read_text", "write_text") and "encoding" not in kw:
            bad.append(node.lineno)
        elif name == "open" and "encoding" not in kw:
            if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id in (
                    "sqlite3", "zipfile", "tarfile", "Image", "webbrowser", "gzip", "os"):
                continue
            args = list(node.args)
            mode_node = None
            if isinstance(f, ast.Name):                         # open(path, mode)
                mode_node = args[1] if len(args) > 1 else None
            else:                                               # Path.open(mode)
                mode_node = args[0] if args else None
            mode_node = next((k.value for k in node.keywords if k.arg == "mode"), mode_node)
            mode = mode_node.value if isinstance(mode_node, ast.Constant) else "r"
            if isinstance(mode, str) and "b" not in mode:
                bad.append(node.lineno)
    return bad


def test_every_text_file_operation_names_utf8():
    offenders = {str(p.relative_to(REPO)): _text_io_without_encoding(p) for p in runtime_sources()}
    offenders = {k: v for k, v in offenders.items() if v}
    assert not offenders, f"text I/O without encoding= (cp1252 on Windows): {offenders}"


POSIX_ONLY = re.compile(r"\bos\.(killpg|getpgid|setsid|fork|getuid|geteuid)\b|\bos\.kill\(|add_signal_handler|"
                        r"preexec_fn|^\s*(import|from)\s+(fcntl|resource|pwd|grp|termios|pty)\b|%-[dmHIMSjy]|"
                        r"['\"]/(tmp|usr|var)/|venv/bin/")


def test_no_posix_only_calls_outside_the_compat_layer():
    hits = []
    for p in runtime_sources():
        if p.name == "oscompat.py":
            continue
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            code, _, note = line.partition("#")
            if POSIX_ONLY.search(code) and "cross-platform" not in note:     # a reviewed per-OS list
                hits.append(f"{p.relative_to(REPO)}:{i}: {line.strip()}")
    assert not hits, "POSIX-only calls (use research_agent.oscompat): " + "; ".join(hits)


# ------------------------------------------------------------------ the process-control layer
def test_pid_alive_never_signals(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("os.kill used for liveness: on Windows it terminates the process")
    monkeypatch.setattr(os, "kill", boom)
    assert oscompat.pid_alive(os.getpid())
    assert oscompat.pid_alive(os.getpid(), must_contain="python") or oscompat.pid_alive(os.getpid())
    assert not oscompat.pid_alive(None) and not oscompat.pid_alive(0)


def test_pid_alive_false_for_exited_and_unrelated_processes():
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    assert not oscompat.pid_alive(p.pid)
    assert not oscompat.pid_alive(os.getpid(), must_contain="definitely-not-in-this-cmdline-123")


def test_worker_liveness_uses_the_compat_layer(tmp_path, monkeypatch):
    from research_agent import control as C
    from research_agent.config import Settings
    from research_agent.store import now
    ctl = C.Control(Settings(state_dir=tmp_path / "state", workers=1))
    monkeypatch.setattr(os, "kill", lambda *a: (_ for _ in ()).throw(AssertionError("os.kill called")))
    ctl.update(pid=os.getpid(), heartbeat_at=now())
    monkeypatch.setattr(C.oscompat, "pid_alive", lambda pid, must_contain=None: must_contain == "research_agent")
    assert ctl.worker_alive()


def test_terminate_tree_stops_children_too():
    code = textwrap.dedent("""
        import subprocess, sys, time
        c = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        print(c.pid, flush=True)
        time.sleep(60)
    """)
    parent = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    child = int(parent.stdout.readline())
    assert oscompat.pid_alive(child)
    oscompat.terminate_tree(parent.pid, timeout=5)
    parent.wait(timeout=10)
    deadline = time.time() + 5
    while oscompat.pid_alive(child) and time.time() < deadline:
        time.sleep(0.1)
    assert not oscompat.pid_alive(child)


def test_spawn_kwargs_per_platform():
    win = oscompat.spawn_kwargs(windows=True)
    assert set(win) == {"creationflags"} and win["creationflags"] & 0x00000200   # CREATE_NEW_PROCESS_GROUP
    assert oscompat.spawn_kwargs(windows=False) == {"start_new_session": True}


def test_child_env_keeps_windows_essentials_case_insensitively():
    environ = {"Path": r"C:\Windows", "SystemRoot": r"C:\Windows", "TEMP": r"C:\T", "ANTHROPIC_API_KEY": "k",
               "MT5_PASSWORD": "secret", "TT_SECRET": "s", "WEBULL_APP_SECRET": "w", "UserProfile": r"C:\Users\x"}
    env = oscompat.child_env(["PATH", "ANTHROPIC_API_KEY"], environ, windows=True)
    assert env == {"Path": r"C:\Windows", "SystemRoot": r"C:\Windows", "TEMP": r"C:\T", "ANTHROPIC_API_KEY": "k",
                   "UserProfile": r"C:\Users\x"}
    assert oscompat.child_env(["PATH"], {"PATH": "/bin", "SYSTEMROOT": "x"}, windows=False) == {"PATH": "/bin"}


def test_worker_environment_never_carries_broker_credentials():
    from research_agent.control import WORKER_ENV
    broker = {"MT5_LOGIN", "MT5_PASSWORD", "MT5_SERVER", "TT_SECRET", "TT_REFRESH", "TT_ACCOUNT",
              "WEBULL_APP_KEY", "WEBULL_APP_SECRET", "WEBULL_ACCOUNT_ID"}
    assert not broker & set(WORKER_ENV)
    assert {"SYSTEMROOT", "TEMP"} <= set(oscompat.WINDOWS_ESSENTIAL_ENV)


def test_spawn_worker_passes_windows_flags_and_closes_its_log_handle(tmp_path, monkeypatch):
    from research_agent import control as C
    from research_agent.config import Settings
    seen = {}

    class FakeProc:
        pid = 4242

        def wait(self):
            return 0

    def fake_popen(cmd, **kw):
        seen.update(kw, cmd=cmd)
        return FakeProc()
    monkeypatch.setattr(C.subprocess, "Popen", fake_popen)
    real = oscompat.spawn_kwargs
    monkeypatch.setattr(C.oscompat, "spawn_kwargs", lambda: real(windows=True))
    monkeypatch.setenv("SYSTEMROOT", r"C:\Windows")
    monkeypatch.setenv("MT5_PASSWORD", "never-passed")
    ctl = C.Control(Settings(state_dir=tmp_path / "state", workers=1))
    assert ctl.spawn_worker() == 4242
    assert "creationflags" in seen and "start_new_session" not in seen
    assert seen["env"]["PYTHONUTF8"] == "1" and "MT5_PASSWORD" not in seen["env"]
    assert seen["stdout"].closed                                  # parent closed its copy of worker.log


def test_log_rotation(tmp_path):
    from research_agent.control import rotate_log
    log = tmp_path / "worker.log"
    log.write_bytes(b"x" * 100)
    assert not rotate_log(log, max_bytes=1000)
    assert rotate_log(log, max_bytes=50, keep=2) and (tmp_path / "worker.log.1").exists() and not log.exists()
    log.write_bytes(b"y" * 100)
    assert rotate_log(log, max_bytes=50, keep=2)
    assert (tmp_path / "worker.log.2").read_bytes() == b"x" * 100


def test_stop_handlers_fall_back_when_the_loop_cannot_install_them(monkeypatch):
    calls = []

    class LoopWithoutSignals:                                    # what Windows' event loop does
        def add_signal_handler(self, *a):
            raise NotImplementedError

        def call_soon_threadsafe(self, cb):
            calls.append(cb)
    installed_handlers = {}
    monkeypatch.setattr(signal, "signal", lambda sig, h: installed_handlers.setdefault(sig, h))
    names = oscompat.install_stop_handlers(LoopWithoutSignals(), lambda: None, windows=False)
    assert "SIGINT" in names and signal.SIGINT in installed_handlers
    installed_handlers[signal.SIGINT](signal.SIGINT, None)
    assert len(calls) == 1
    names_win = oscompat.install_stop_handlers(LoopWithoutSignals(), lambda: None, windows=True)
    assert "SIGINT" in names_win


def test_stop_handlers_on_a_real_loop():
    async def main():
        hit = asyncio.Event()
        names = oscompat.install_stop_handlers(asyncio.get_running_loop(), hit.set)
        os.kill(os.getpid(), signal.SIGINT) if os.name != "nt" else None
        await asyncio.wait_for(hit.wait(), 2)
        return names
    if os.name == "nt":
        pytest.skip("covered by the fallback test; real delivery needs a console on Windows")
    assert "SIGINT" in asyncio.run(main())


def test_utf8_stdio_is_safe_to_call():
    oscompat.utf8_stdio()
    print("research text: → — ≥ ✓")


# ------------------------------------------------------------------ time zones without a system database
def test_zoneinfo_works_from_the_tzdata_package_alone():
    """Windows has no IANA time-zone files: zoneinfo must resolve from the tzdata wheel."""
    code = "from zoneinfo import ZoneInfo; print(ZoneInfo('America/New_York').key, ZoneInfo('Europe/Prague').key)"
    r = subprocess.run([sys.executable, "-c", code], env={**os.environ, "PYTHONTZPATH": ""}, capture_output=True,
                       text=True)
    assert r.returncode == 0, r.stderr
    assert r.stdout.split() == ["America/New_York", "Europe/Prague"]


# ------------------------------------------------------------------ file names Windows rejects
ILLEGAL = re.compile(r'[<>:"|?*\x00-\x1f]')
RESERVED = {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1, 10)], *[f"LPT{i}" for i in range(1, 10)]}


def _windows_safe(name: str) -> bool:
    stem = name.split(".")[0].upper()
    return not ILLEGAL.search(name) and stem not in RESERVED and not name.endswith((" ", "."))


def test_generated_file_names_are_windows_safe():
    from rag.fmp.client import FmpClient
    from research_agent import export, pipeline
    from research_agent.tools import ToolContext  # noqa: F401  (import check)
    names = [pipeline.new_run_id("agent"), f"leaderboard-{export._stamp()}.csv", f"research-{pipeline.new_run_id('agent')}.xlsx"]
    import hashlib
    import json
    params = {"symbol": "TSLA", "from": "2026-01-01", "to": "2026-10-08"}
    names.append(f"{'sec-filings-search/symbol'.replace('/', '_')}__{hashlib.sha1(json.dumps(params, sort_keys=True).encode()).hexdigest()[:10]}.json")
    names.append("10-Q_2026-07-23_000162828026049270")                         # rag.sec folder pattern
    for sym in ("AAPL", "BRK.B", "CON", "PRN", "NUL"):                          # lab price-cache keys
        names.append(f"{sym.strip('/')}_1d_3650.json")
    bad = [n for n in names if not _windows_safe(n)]
    assert not bad, bad
    assert FmpClient  # imported fine
