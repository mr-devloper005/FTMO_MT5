"""The few operating-system differences the 24/7 research worker depends on, in one place.

macOS and Windows differ in exactly the calls the worker's lifecycle uses:

* liveness - ``os.kill(pid, 0)`` is a harmless "are you there?" on POSIX but on Windows
  ``os.kill`` with any signal other than CTRL_C/CTRL_BREAK calls TerminateProcess: the
  status poll would KILL the worker. psutil answers the question on both.
* termination - ``os.killpg``/``os.getpgid`` do not exist on Windows; psutil stops the
  whole process tree on both.
* detaching - ``start_new_session`` is POSIX-only; Windows uses process-creation flags.
* signals - ``loop.add_signal_handler`` raises NotImplementedError on Windows' event loop;
  the fallback is ``signal.signal`` for SIGINT / SIGBREAK.
* environment - a child started with a scrubbed environment on Windows still needs
  SYSTEMROOT, TEMP, USERPROFILE, ... (without SYSTEMROOT Python cannot even seed its
  random generator); Windows variable names are case-insensitive.
* console text - Windows consoles and redirected output default to a legacy code page;
  research text contains "→", "—", "≥", so stdout/stderr are switched to UTF-8.

``windows=`` parameters exist so tests on macOS can exercise the Windows branches.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
from typing import Callable, Dict, Iterable, List, Mapping, Optional

IS_WINDOWS = os.name == "nt"

# What a Windows child process needs to start and behave normally, whatever else is scrubbed.
WINDOWS_ESSENTIAL_ENV = ("SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP", "USERPROFILE", "APPDATA",
                         "LOCALAPPDATA", "PROGRAMDATA", "PROGRAMFILES", "PROGRAMFILES(X86)", "COMMONPROGRAMFILES",
                         "HOMEDRIVE", "HOMEPATH", "USERNAME", "USERDOMAIN", "COMPUTERNAME", "NUMBER_OF_PROCESSORS",
                         "PROCESSOR_ARCHITECTURE", "OS")


def _windows(windows: Optional[bool]) -> bool:
    return IS_WINDOWS if windows is None else windows


def child_env(keep: Iterable[str], environ: Optional[Mapping[str, str]] = None,
              windows: Optional[bool] = None) -> Dict[str, str]:
    """Copy only ``keep`` from ``environ`` (plus what Windows needs). Matching is case-insensitive
    on Windows, where ``Path`` and ``PATH`` are the same variable."""
    environ = os.environ if environ is None else environ
    win = _windows(windows)
    wanted = list(keep) + (list(WINDOWS_ESSENTIAL_ENV) if win else [])
    if not win:
        return {k: environ[k] for k in wanted if k in environ}
    by_upper = {k.upper(): k for k in environ}
    out: Dict[str, str] = {}
    for name in wanted:
        real = by_upper.get(name.upper())
        if real is not None:
            out[real] = environ[real]
    return out


def spawn_kwargs(windows: Optional[bool] = None) -> Dict[str, object]:
    """Popen arguments that detach a long-lived child from the parent's console / session."""
    if _windows(windows):
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200) | \
            getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        return {"creationflags": flags}
    return {"start_new_session": True}


def pid_alive(pid: Optional[int], must_contain: Optional[str] = None) -> bool:
    """Is ``pid`` a running (not zombie) process? Never sends a signal. With ``must_contain``,
    the process command line must mention it too (Windows reuses PIDs quickly)."""
    if not pid:
        return False
    import psutil
    try:
        p = psutil.Process(int(pid))
        if p.status() == psutil.STATUS_ZOMBIE:
            return False
        if must_contain:
            try:
                return any(must_contain in part for part in p.cmdline())
            except (psutil.AccessDenied, psutil.ZombieProcess):
                return True                                   # exists; cannot inspect it - trust the pid
        return True
    except (psutil.NoSuchProcess, ValueError):
        return False
    except psutil.AccessDenied:
        return True


def terminate_tree(pid: int, timeout: float = 10.0) -> List[int]:
    """Stop ``pid`` and all its descendants: polite terminate first, kill whatever is left.
    Returns the pids that were still running and got signalled."""
    import psutil
    try:
        root = psutil.Process(int(pid))
    except psutil.NoSuchProcess:
        return []
    procs = root.children(recursive=True) + [root]
    hit = []
    for p in procs:
        try:
            p.terminate()
            hit.append(p.pid)
        except psutil.NoSuchProcess:
            pass
    _, alive = psutil.wait_procs(procs, timeout=timeout)
    for p in alive:
        try:
            p.kill()
        except psutil.NoSuchProcess:
            pass
    psutil.wait_procs(alive, timeout=timeout)
    return hit


def install_stop_handlers(loop, callback: Callable[[], None], windows: Optional[bool] = None) -> List[str]:
    """Run ``callback`` on the event loop when the process is asked to stop.
    POSIX: loop signal handlers for SIGTERM/SIGINT. Windows (or any loop without
    add_signal_handler): signal.signal for SIGINT and SIGBREAK, handed to the loop thread-safely."""
    installed: List[str] = []
    if not _windows(windows):
        try:
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(sig, callback)
                installed.append(sig.name)
            return installed
        except (NotImplementedError, RuntimeError, AttributeError):
            installed.clear()

    def _handler(signum, frame) -> None:                      # runs in the main thread
        loop.call_soon_threadsafe(callback)

    for name in ("SIGINT", "SIGBREAK", "SIGTERM"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, _handler)
            installed.append(name)
        except (ValueError, OSError, RuntimeError):           # not the main thread, or not allowed
            pass
    return installed


def utf8_stdio() -> None:
    """Make print() safe for non-ASCII research text on Windows consoles and redirected logs."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            if (getattr(stream, "encoding", "") or "").lower().replace("-", "") != "utf8":
                reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass
