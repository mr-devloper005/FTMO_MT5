"""A small local web UI for the paper-only systematic pipeline.

    PYTHONPATH=. venv/bin/python3 -m systematic ui        # http://127.0.0.1:8601

Deliberately a *separate process on its own port* from ``broker_hub.py`` (8600).
The hub holds broker gateways and order routes; this serves the paper pipeline
only, so the page you click Run on lives in a process with no order path at all.

Every action shells out to the same CLI you would type by hand
(``python -m systematic ...``, ``scripts/paper_replay.py``). The UI never
re-implements the pipeline, so it cannot drift from it or do something the CLI
would refuse.

Read-only by default apart from four buttons: research, cycle, replay, and
clearing a halt (which still demands a name and an explicit confirm).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from collections import deque
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Deque, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from systematic.config import PACKAGE, PROMOTION_FLOORS, REPO

UI_FILE = PACKAGE / "ui.html"
NO_CACHE = {"Cache-Control": "no-store"}
BANNER = "PAPER TRADING ONLY — simulated money. This process has no live order path."

app = FastAPI(title="Systematic paper trading")


# ----------------------------------------------------------------- paths
def _safe(path_str: str, must_exist: bool = False) -> Path:
    """Every path the browser sends must stay inside the repo."""
    p = (REPO / path_str).resolve() if not Path(path_str).is_absolute() else Path(path_str).resolve()
    if not p.is_relative_to(REPO.resolve()):          # a string prefix test also accepts C:\repo2
        raise HTTPException(400, "path must be inside the repo")
    if must_exist and not p.exists():
        raise HTTPException(404, f"not found: {path_str}")
    return p


def _state_dirs() -> List[str]:
    """Top-level state dirs, plus any replay account nested inside one."""
    out = []
    for d in sorted(PACKAGE.glob("state*")):
        if not d.is_dir():
            continue
        out.append(str(d.relative_to(REPO)))
        replay = d / "replay"
        if replay.is_dir():
            out.append(str(replay.relative_to(REPO)))
    return out


# ------------------------------------------------------------------ jobs
class Job:
    """One CLI run at a time. Output is kept so the page can tail it."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.cmd: List[str] = []
        self.label = ""
        self.lines: Deque[str] = deque(maxlen=800)
        self.running = False
        self.returncode: Optional[int] = None
        self.started: Optional[str] = None

    def start(self, label: str, cmd: List[str]) -> None:
        with self.lock:
            if self.running:
                raise HTTPException(409, f"'{self.label}' is still running")
            self.running = True
            self.label, self.cmd = label, cmd
            self.lines.clear()
            self.returncode = None
            self.started = datetime.now(timezone.utc).isoformat(timespec="seconds")
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        self.lines.append("$ " + " ".join(self.cmd))
        try:
            proc = subprocess.Popen(
                self.cmd, cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1, env=_child_env())
            for line in proc.stdout:                       # type: ignore[union-attr]
                self.lines.append(line.rstrip())
            proc.wait()
            self.returncode = proc.returncode
        except Exception as e:                             # noqa: BLE001
            self.lines.append(f"failed to launch: {type(e).__name__}: {e}")
            self.returncode = -1
        finally:
            self.running = False

    def snapshot(self) -> Dict:
        return {"label": self.label, "running": self.running, "returncode": self.returncode,
                "started": self.started, "lines": list(self.lines)}


JOB = Job()
PY = [sys.executable]

# Windows needs these for sockets, SSL, temp files and finding programs; without SYSTEMROOT a child
# Python cannot even open a network connection. Nothing else (no broker credentials) is passed on.
_WINDOWS_ENV = ("SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP", "USERPROFILE",
                "APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "HOMEDRIVE", "HOMEPATH", "NUMBER_OF_PROCESSORS")


def _child_env(environ: Optional[Dict[str, str]] = None, windows: Optional[bool] = None) -> Dict[str, str]:
    """A scrubbed environment for jobs started from the panel: repo on PYTHONPATH, UTF-8 output."""
    environ = os.environ if environ is None else environ
    windows = os.name == "nt" if windows is None else windows
    env = {"PYTHONPATH": str(REPO), "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    if windows:
        upper = {k.upper(): v for k, v in environ.items()}       # Windows names are case-insensitive
        env.update({k: upper[k] for k in _WINDOWS_ENV if k in upper})
        env["PATH"] = upper.get("PATH", "")
    else:
        env["PATH"] = "/usr/bin:/bin:/usr/local/bin"            # cross-platform: the POSIX branch
    return env


# --------------------------------------------------------------- reading
def _ledger(state: Path) -> Dict:
    f = state / "paper" / "ledger.json"
    if not f.exists():
        return {}
    return json.loads(f.read_text(encoding="utf-8"))


def _ranking(state: Path) -> Dict:
    f = state / "ranking.json"
    if not f.exists():
        return {}
    return json.loads(f.read_text(encoding="utf-8"))


def _D(v) -> Decimal:
    return Decimal(str(v or 0))


def _pnl(led: Dict) -> Dict:
    """Realized, unrealized and net P&L straight from the ledger.

    The headline number is ``equity - initial_capital``: it is exact by
    construction (balance = capital + realized gross - commissions, and equity
    adds open float), so it cannot disagree with the account itself.
    """
    if not led:
        return {}
    initial = _D(led.get("initial_capital"))
    commissions = _D(led.get("commissions"))

    open_rows, unrealized = [], Decimal(0)
    for sym, p in (led.get("positions") or {}).items():
        sign = Decimal(1) if p["side"] == "LONG" else Decimal(-1)
        qty, avg, last = _D(p["quantity"]), _D(p["avg_price"]), _D(p["last_price"])
        u = sign * (last - avg) * qty
        unrealized += u
        open_rows.append({
            "symbol": sym, "side": p["side"], "quantity": str(qty),
            "avgPrice": float(avg), "lastPrice": float(last),
            "pnl": float(u),
            "pnlPct": float(sign * (last - avg) / avg * 100) if avg else 0.0,
            "value": float(last * qty),
            "strategy": p.get("strategy", ""), "openedAt": p.get("opened_at"),
        })

    trades = led.get("trades") or []
    closed = [{
        "symbol": t["symbol"], "side": t["side"], "quantity": t["quantity"],
        "entry": float(_D(t["entry_price"])), "exit": float(_D(t["exit_price"])),
        "pnl": float(_D(t["net_pnl"])), "gross": float(_D(t["gross_pnl"])),
        "openedAt": t.get("opened_at"), "closedAt": t.get("closed_at"),
        "strategy": t.get("strategy", ""), "reason": t.get("reason", ""),
    } for t in trades]
    realized = sum((_D(t["net_pnl"]) for t in trades), Decimal(0))

    equity = initial + _D(led.get("realized_pnl")) - commissions + unrealized
    wins = [c for c in closed if c["pnl"] > 0]
    losses = [c for c in closed if c["pnl"] < 0]

    return {
        "initial": float(initial),
        "equity": float(equity),
        "net": float(equity - initial),
        "netPct": float((equity / initial - 1) * 100) if initial else 0.0,
        "realized": float(realized),
        "unrealized": float(unrealized),
        "commissions": float(commissions),
        "openPositions": open_rows,
        "closedTrades": list(reversed(closed)),          # newest first
        "wins": len(wins), "losses": len(losses),
        "winRate": round(100 * len(wins) / len(closed), 1) if closed else None,
        "avgWin": round(sum(c["pnl"] for c in wins) / len(wins), 2) if wins else 0.0,
        "avgLoss": round(sum(c["pnl"] for c in losses) / len(losses), 2) if losses else 0.0,
        "best": max((c["pnl"] for c in closed), default=0.0),
        "worst": min((c["pnl"] for c in closed), default=0.0),
        "equityCurve": [{"at": e.get("at"), "equity": float(_D(e.get("equity")))}
                        for e in (led.get("equity_history") or [])][-400:],
    }


@app.get("/", response_class=HTMLResponse)
async def page() -> HTMLResponse:
    if not UI_FILE.exists():
        return HTMLResponse("<h1>systematic/ui.html not found</h1>", status_code=404)
    return HTMLResponse(UI_FILE.read_text(encoding="utf-8"), headers=NO_CACHE)


@app.get("/api/config")
async def config() -> JSONResponse:
    return JSONResponse({
        "banner": BANNER,
        "states": _state_dirs(),
        "watchlists": ([str((PACKAGE / "watchlist.txt").relative_to(REPO))]
                       + [str(p.relative_to(REPO)) for p in sorted((PACKAGE / "watchlists").glob("*.txt"))]),
        "settings": ([""] + [str(p.relative_to(REPO)) for p in sorted((PACKAGE / "settings").glob("*.yaml"))]),
        "grades": PROMOTION_FLOORS,
    }, headers=NO_CACHE)


@app.get("/api/state")
async def state(state: str) -> JSONResponse:
    d = _safe(state)
    led, rank = _ledger(d), _ranking(d)
    last = (led.get("equity_history") or [{}])[-1]
    reports = sorted((d / "reports").glob("*.md"), reverse=True) if (d / "reports").exists() else []
    return JSONResponse({
        "stateDir": state,
        "account": {
            "equity": last.get("equity", led.get("initial_capital")),
            "balance": last.get("balance", led.get("initial_capital")),
            "initial": led.get("initial_capital"),
            "riskState": led.get("risk_state"),
            "halted": led.get("halted"),
            "commissions": led.get("commissions"),
            "closedTrades": len(led.get("trades", [])),
            "positions": led.get("positions", {}),
            "exists": bool(led),
        },
        "ranking": {
            "generatedAt": rank.get("generatedAt"),
            "minGrade": rank.get("minGrade"),
            "exploratory": rank.get("exploratory", False),
            "tally": rank.get("gradeTally", {}),
            "promoted": rank.get("promoted", []),
            "ranked": rank.get("ranked", []),
        },
        "pnl": _pnl(led),
        "reports": [p.name for p in reports[:40]],
    }, headers=NO_CACHE)


@app.get("/api/report")
async def report(state: str, name: str) -> JSONResponse:
    if "/" in name or ".." in name:
        raise HTTPException(400, "bad report name")
    f = _safe(state) / "reports" / name
    if not f.exists():
        raise HTTPException(404, "no such report")
    return JSONResponse({"name": name, "text": f.read_text(encoding="utf-8")}, headers=NO_CACHE)


@app.get("/api/watchlist")
async def get_watchlist(path: str) -> JSONResponse:
    return JSONResponse({"path": path, "text": _safe(path, must_exist=True).read_text(encoding="utf-8")}, headers=NO_CACHE)


class WatchlistIn(BaseModel):
    path: str
    text: str


@app.post("/api/watchlist")
async def put_watchlist(body: WatchlistIn) -> JSONResponse:
    p = _safe(body.path)
    if p.suffix != ".txt":
        raise HTTPException(400, "watchlists are .txt files")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body.text, encoding="utf-8")
    # Parse it back so a typo surfaces here, not three minutes into a run.
    from systematic.universe import load_watchlist
    try:
        symbols = load_watchlist(p)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return JSONResponse({"saved": body.path, "symbols": len(symbols)}, headers=NO_CACHE)


# --------------------------------------------------------------- symbols
# Same pattern as systematic.universe — imported so the two cannot drift.
from systematic.universe import _TICKER as _TICKER_OK  # noqa: E402


def _load_bars(symbol: str, bars: int = 2500):
    """Daily completed bars for one symbol. Network on first call, then cached."""
    from systematic.data import load_completed
    return load_completed(symbol, "1d", bars)


def _symbol_meta(symbol: str, candles=None) -> Dict:
    """Enough to show a ticker row without opening it.

    Pass ``candles`` when the caller already loaded them, so the row describes
    the series actually on screen instead of a second, differently sized fetch.
    """
    try:
        c = _load_bars(symbol, 400) if candles is None else candles
    except Exception as e:                                  # noqa: BLE001
        return {"symbol": symbol, "ok": False, "error": f"{type(e).__name__}: {e}"}
    if len(c) < 100:
        return {"symbol": symbol, "ok": False,
                "error": f"only {len(c)} daily bars — too little history to research"}
    last, prev = c[-1], c[-2]
    year = c[-252] if len(c) >= 252 else c[0]
    return {
        "symbol": symbol, "ok": True, "bars": len(c),
        "last": float(last.close),
        "changePct": float((last.close - prev.close) / prev.close * 100) if prev.close else 0.0,
        "yearPct": float((last.close - year.close) / year.close * 100) if year.close else 0.0,
        "from": datetime.fromtimestamp(c[0].timestamp, timezone.utc).date().isoformat(),
        "to": datetime.fromtimestamp(last.timestamp, timezone.utc).date().isoformat(),
    }


@app.get("/api/symbols")
def symbols(path: str, meta: bool = False) -> JSONResponse:
    """The watchlist, optionally with live price metadata (slower: it downloads)."""
    from systematic.universe import load_watchlist
    f = _safe(path, must_exist=True)
    try:
        syms = load_watchlist(f)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not meta:
        return JSONResponse({"path": path, "symbols": [{"symbol": x} for x in syms]}, headers=NO_CACHE)
    return JSONResponse({"path": path, "symbols": [_symbol_meta(x) for x in syms]}, headers=NO_CACHE)


class SymbolsIn(BaseModel):
    path: str
    symbols: List[str] = []
    symbol: Optional[str] = None


def _write_symbols(f: Path, syms: List[str], keep_header: str = "") -> None:
    f.write_text((keep_header + "\n" if keep_header else "") + "\n".join(syms) + "\n", encoding="utf-8")


def _read_symbols(f: Path) -> tuple:
    """(header comment block, symbols) so edits keep the file's notes."""
    header, syms = [], []
    for line in f.read_text(encoding="utf-8").splitlines() if f.exists() else []:
        t = line.split("#", 1)[0].strip().split()
        if t:
            syms.append(t[0].upper())
        elif line.strip().startswith("#") and not syms:
            header.append(line)
    return "\n".join(header), syms


@app.post("/api/symbols/add")
def add_symbols(body: SymbolsIn) -> JSONResponse:
    """Add tickers, checking each has usable history before it lands in the file."""
    f = _safe(body.path)
    if f.suffix != ".txt":
        raise HTTPException(400, "watchlists are .txt files")
    header, current = _read_symbols(f)
    raw = body.symbols or ([body.symbol] if body.symbol else [])
    wanted, results = [], []
    for item in raw:
        for token in str(item).replace(",", " ").split():
            sym = token.strip().upper()
            if sym and sym not in wanted:
                wanted.append(sym)
    for sym in wanted:
        if not _TICKER_OK.match(sym):
            results.append({"symbol": sym, "ok": False, "error": "not a valid ticker"})
        elif sym in current:
            results.append({"symbol": sym, "ok": False, "error": "already on the list"})
        else:
            m = _symbol_meta(sym)
            results.append(m)
            if m["ok"]:
                current.append(sym)
    f.parent.mkdir(parents=True, exist_ok=True)
    _write_symbols(f, current, header)
    return JSONResponse({"path": body.path, "added": [r for r in results if r.get("ok")],
                         "rejected": [r for r in results if not r.get("ok")],
                         "symbols": current}, headers=NO_CACHE)


@app.post("/api/symbols/remove")
def remove_symbol(body: SymbolsIn) -> JSONResponse:
    f = _safe(body.path, must_exist=True)
    header, current = _read_symbols(f)
    drop = {(body.symbol or "").upper(), *[x.upper() for x in body.symbols]}
    kept = [x for x in current if x not in drop]
    if not kept:
        raise HTTPException(400, "a watchlist cannot be empty")
    _write_symbols(f, kept, header)
    return JSONResponse({"path": body.path, "symbols": kept}, headers=NO_CACHE)


class NewListIn(BaseModel):
    name: str


@app.post("/api/watchlists/new")
def new_watchlist(body: NewListIn) -> JSONResponse:
    safe_name = "".join(ch for ch in body.name.strip() if ch.isalnum() or ch in "-_")
    if not safe_name:
        raise HTTPException(400, "give the list a name")
    f = PACKAGE / "watchlists" / f"{safe_name}.txt"
    if f.exists():
        raise HTTPException(400, f"{safe_name}.txt already exists")
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(f"# {body.name.strip()}\n", encoding="utf-8")
    return JSONResponse({"path": str(f.relative_to(REPO))}, headers=NO_CACHE)


@app.get("/api/symbol")
def symbol_detail(name: str, state: Optional[str] = None, bars: int = 500) -> JSONResponse:
    """Everything the panel knows about one stock: price, grades, trades, position."""
    sym = name.upper()
    if not _TICKER_OK.match(sym):
        raise HTTPException(400, "not a valid ticker")
    out: Dict = {"symbol": sym}
    try:
        c = _load_bars(sym, max(50, min(int(bars), 2500)))
        out["candles"] = [{"t": datetime.fromtimestamp(x.timestamp, timezone.utc).date().isoformat(),
                           "o": float(x.open), "h": float(x.high),
                           "l": float(x.low), "c": float(x.close)} for x in c]
        out["meta"] = _symbol_meta(sym, c)
    except Exception as e:                                  # noqa: BLE001
        out["candles"] = []
        out["meta"] = {"symbol": sym, "ok": False, "error": f"{type(e).__name__}: {e}"}

    if state:
        d = _safe(state)
        rank = _ranking(d)
        out["research"] = [r for r in rank.get("ranked", []) if r.get("symbol") == sym]
        led = _ledger(d)
        pnl = _pnl(led)
        out["trades"] = [t for t in pnl.get("closedTrades", []) if t["symbol"] == sym]
        out["position"] = next((o for o in pnl.get("openPositions", []) if o["symbol"] == sym), None)
        out["realized"] = round(sum(t["pnl"] for t in out["trades"]), 2)
    return JSONResponse(out, headers=NO_CACHE)


# --------------------------------------------------------------- running
class RunIn(BaseModel):
    command: str                      # research | cycle | run | replay
    state: str
    watchlist: Optional[str] = None
    settings: Optional[str] = None
    min_grade: Optional[str] = None
    days: int = 250


@app.post("/api/run")
async def run(body: RunIn) -> JSONResponse:
    state = _safe(body.state)
    if body.command == "replay":
        cmd = PY + ["scripts/paper_replay.py", "--state", str(state), "--days", str(int(body.days))]
    elif body.command in ("research", "cycle", "run"):
        cmd = PY + ["-m", "systematic", body.command, "--state", str(state)]
        if body.command in ("research", "run"):
            if not body.watchlist:
                raise HTTPException(400, "research needs a watchlist")
            cmd += ["--watchlist", str(_safe(body.watchlist, must_exist=True))]
    else:
        raise HTTPException(400, f"unknown command {body.command!r}")

    if body.settings:
        cmd += ["--settings", str(_safe(body.settings, must_exist=True))]
    if body.min_grade:
        if body.min_grade not in PROMOTION_FLOORS:
            raise HTTPException(400, "FAILED can never be a promotion floor")
        cmd += ["--min-grade", body.min_grade]

    JOB.start(f"{body.command} ({body.state})", cmd)
    return JSONResponse({"started": True, "cmd": cmd}, headers=NO_CACHE)


class HaltIn(BaseModel):
    state: str
    by: str
    confirm: bool = False


@app.post("/api/reset-halt")
async def reset_halt(body: HaltIn) -> JSONResponse:
    if not body.confirm or not body.by.strip():
        raise HTTPException(400, "clearing a halt needs your name and an explicit confirm")
    cmd = PY + ["-m", "systematic", "reset-halt", "--state", str(_safe(body.state)),
                "--by", body.by.strip(), "--confirm"]
    JOB.start(f"reset-halt ({body.state})", cmd)
    return JSONResponse({"started": True}, headers=NO_CACHE)


@app.get("/api/job")
async def job() -> JSONResponse:
    return JSONResponse(JOB.snapshot(), headers=NO_CACHE)


def _already_serving(host: str, port: int) -> bool:
    """Is an instance of *this* UI already on the port?"""
    import json
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/api/config", timeout=2) as r:
            return "banner" in json.load(r)
    except Exception:                                  # noqa: BLE001
        return False


def serve(host: str = "127.0.0.1", port: int = 8601) -> None:
    import socket

    import uvicorn
    print(BANNER)
    with socket.socket() as s:
        taken = s.connect_ex((host, port)) == 0
    if taken:
        if _already_serving(host, port):
            print(f"Already running — open http://{host}:{port}")
            print("To restart it:  kill $(lsof -ti tcp:%d) && python -m systematic ui" % port)
        else:
            print(f"Port {port} is in use by something else. Pick another: "
                  f"python -m systematic ui --port {port + 1}")
        return
    print(f"Systematic paper UI: http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="warning")
