"""The AI Research dashboard: one router, mounted by the FTMO hub (broker_hub.py)
at /research, or served on its own with ``python -m research_agent ui``.

Every endpoint is a thin wrapper over ``views`` (read), ``sheets``/``export``
(which read ``views``) or ``control`` (the 24/7 worker's desired state). Nothing
here computes a metric, a verdict or a reason, and nothing here touches a
broker. The Control Center endpoints only change state in the research store;
the separate worker process does the research.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from research_agent import catalog, export, sheets, views
from research_agent.config import Settings
from research_agent.control import Control

PAGE = Path(__file__).parent / "ui" / "research.html"
NO_CACHE = {"Cache-Control": "no-store, must-revalidate"}

router = APIRouter()


def _settings() -> Settings:
    return Settings()


@router.get("/research", response_class=HTMLResponse)
def research_page() -> HTMLResponse:
    return HTMLResponse(PAGE.read_text(encoding="utf-8"), headers=NO_CACHE)


@router.get("/api/research/summary")
def research_summary() -> Dict[str, Any]:
    return views.summary(_settings())


@router.get("/api/research/leaderboard")
def research_leaderboard() -> Dict[str, Any]:
    return {"rows": views.leaderboard(_settings())}


@router.get("/api/research/tests")
def research_tests(symbol: Optional[str] = None, timeframe: Optional[str] = None) -> Dict[str, Any]:
    s = _settings()
    if timeframe and timeframe not in s.timeframes:
        raise HTTPException(400, f"timeframe must be one of {list(s.timeframes)}")
    return {"rows": views.strategy_tests(s, symbol, timeframe)}


@router.get("/api/research/timeframes")
def research_timeframes() -> Dict[str, Any]:
    return {"rows": views.timeframes(_settings())}


@router.get("/api/research/screening")
def research_screening(status: str = "all") -> Dict[str, Any]:
    if status not in ("all", "pass", "fail"):
        raise HTTPException(400, "status must be all, pass or fail")
    return {"rows": views.screening(_settings(), status)}


@router.get("/api/research/export.xlsx")
def research_export() -> FileResponse:
    s = _settings()
    s.ensure_dirs()
    path = export.to_xlsx(s.exports_dir / f"ftmo-ai-research-{export._stamp()}.xlsx", s)
    return FileResponse(path, filename=path.name,
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


class SheetConfig(BaseModel):
    sheet: str


@router.get("/api/research/sheets")
def research_sheets_status() -> Dict[str, Any]:
    return sheets.status(_settings())


@router.post("/api/research/sheets/config")
def research_sheets_config(body: SheetConfig) -> Dict[str, Any]:
    try:
        return sheets.set_sheet(_settings(), body.sheet)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.post("/api/research/sheets/sync")
def research_sheets_sync() -> Dict[str, Any]:
    try:
        return sheets.sync(_settings())
    except Exception as e:                                       # noqa: BLE001 — shown to the user as-is
        raise HTTPException(400, f"{type(e).__name__}: {str(e)[:400]}") from e


# ---------------------------------------------------------------- quality (Rahul's lenses) x tested edge
@router.get("/api/research/quality")
def research_quality() -> Dict[str, Any]:
    return views.quality(_settings())


# ---------------------------------------------------------------- strategies
@router.get("/api/research/strategies")
def research_strategies() -> Dict[str, Any]:
    s = _settings()
    rows = catalog.catalog(s)
    tested: Dict[str, Dict[str, Any]] = {}
    for t in views.strategy_tests(s):
        d = tested.setdefault(t["Strategy"], {"tests": 0, "beat": 0, "best": None, "verdicts": {}})
        d["tests"] += 1
        d["beat"] += int((t["Outperformance (pts)"] or 0) > 0)
        d["verdicts"][t["Verdict"]] = d["verdicts"].get(t["Verdict"], 0) + 1
        if d["best"] is None or (t["Outperformance (pts)"] or -1e9) > (d["best"]["Outperformance (pts)"] or -1e9):
            d["best"] = {k: t[k] for k in ("Symbol", "Timeframe", "Return %", "Buy & Hold %", "Outperformance (pts)",
                                           "Robustness")}
    return {"rows": [{**r, **tested.get(r["name"], {"tests": 0, "beat": 0, "best": None, "verdicts": {}})}
                     for r in rows]}


# ---------------------------------------------------------------- agent control center
def _ctl() -> Control:
    return Control(_settings())


@router.get("/api/research/agent/status")
def agent_status() -> Dict[str, Any]:
    ctl = _ctl()
    ctl.ensure_worker()                          # recovery: agent should be on but worker died
    return ctl.status()


@router.get("/api/research/agent/events")
def agent_events(after: int = 0, limit: int = 200, run_id: Optional[str] = None) -> Dict[str, Any]:
    return {"events": _ctl().events(after=after, limit=limit, run_id=run_id)}


@router.get("/api/research/agent/runs")
def agent_runs(limit: int = 20) -> Dict[str, Any]:
    return {"runs": _ctl().history(min(limit, 100))}


@router.post("/api/research/agent/start")
def agent_start() -> Dict[str, Any]:
    return _ctl().start()


@router.post("/api/research/agent/pause")
def agent_pause() -> Dict[str, Any]:
    return _ctl().pause()


@router.post("/api/research/agent/stop")
def agent_stop() -> Dict[str, Any]:
    return _ctl().stop()


@router.post("/api/research/agent/run-now")
def agent_run_now() -> Dict[str, Any]:
    return _ctl().run_now()


@router.get("/api/research/agent/settings")
def agent_settings_get() -> Dict[str, Any]:
    return _ctl().status()["settings"]


@router.post("/api/research/agent/settings")
def agent_settings_set(body: Dict[str, Any]) -> Dict[str, Any]:
    from dataclasses import asdict
    try:
        return asdict(_ctl().save_settings(body))
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


def autostart() -> None:
    """Called when the hub / dashboard starts: bring the worker back if the agent was left on."""
    try:
        if _ctl().ensure_worker():
            import logging
            logging.getLogger("research_agent").info("research worker restarted (agent was left running)")
    except Exception as e:                                       # noqa: BLE001 — never block the host app
        import logging
        logging.getLogger("research_agent").warning("research worker autostart failed: %s", e)


def standalone_app():
    """The dashboard without the trading hub — for viewing on any machine with the state folder."""
    from fastapi import FastAPI
    from fastapi.responses import RedirectResponse
    app = FastAPI(title="FTMO AI Research")
    app.include_router(router)
    autostart()

    @app.get("/")
    def root():
        return RedirectResponse("/research")
    return app
