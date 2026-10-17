"""Push the research report to a Google Sheet — the same tabs as the dashboard and Excel.

Source of truth stays the SQLite store: every sync rebuilds each tab from
``views.report_tables`` and overwrites it, so the Sheet is always a faithful
copy, never an independently edited record.

One-time setup (Google Cloud, ~5 minutes):
  1. Create a service account and a JSON key for it (Sheets API enabled).
  2. Save the key as ``research_agent/state/google-service-account.json``
     (gitignored) or point ``GOOGLE_SERVICE_ACCOUNT_FILE`` at it.
  3. Create a Google Sheet and share it (Editor) with the service account's
     email address. Paste the Sheet's URL in the dashboard, or set
     ``RESEARCH_SHEET_ID``.

Values are written RAW, so a company name can never be evaluated as a formula.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from research_agent import views
from research_agent.config import Settings
from research_agent.store import now

# The tabs are whatever views.report_tables returns (Summary, Timeframes, Leaderboard,
# Strategy Tests, Screening) — the same set the Excel workbook gets.
_COLOURS = {"Recommended": (0.84, 0.96, 0.88), "Watch": (1.0, 0.96, 0.8), "Rejected": (0.98, 0.88, 0.88),
            "PASS": (0.84, 0.96, 0.88), "FAIL": (0.98, 0.88, 0.88)}
_WIDTHS = {"Reason": 520, "Company": 200, "Strategy": 230, "Best Strategy": 230, "Parameters": 280,
           "Robustness": 130, "Screened at": 170}


# ---------------------------------------------------------------- config
def _config_path(settings: Settings) -> Path:
    return settings.state_dir / "sheets.json"


def _load(settings: Settings) -> Dict[str, Any]:
    try:
        return json.loads(_config_path(settings).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(settings: Settings, data: Dict[str, Any]) -> None:
    settings.ensure_dirs()
    _config_path(settings).write_text(json.dumps(data, indent=2), encoding="utf-8")


def credentials_path(settings: Settings) -> Path:
    return Path(os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE") or settings.state_dir / "google-service-account.json")


def parse_sheet_id(value: str) -> str:
    """Accept a full Sheet URL or a bare ID."""
    value = (value or "").strip()
    m = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]{20,})", value)
    sid = m.group(1) if m else value
    if not re.fullmatch(r"[A-Za-z0-9_-]{20,}", sid):
        raise ValueError("that is not a Google Sheet URL or ID")
    return sid


def sheet_id(settings: Settings) -> Optional[str]:
    return os.getenv("RESEARCH_SHEET_ID") or _load(settings).get("sheet_id")


def set_sheet(settings: Settings, url_or_id: str) -> Dict[str, Any]:
    data = _load(settings)
    data["sheet_id"] = parse_sheet_id(url_or_id)
    _save(settings, data)
    return status(settings)


def status(settings: Settings) -> Dict[str, Any]:
    data = _load(settings)
    sid = sheet_id(settings)
    creds = credentials_path(settings)
    try:
        import gspread  # noqa: F401
        lib = True
    except ImportError:
        lib = False
    return {"library": lib, "credentials_file_present": creds.exists(), "credentials_file": str(creds),
            "sheet_id": sid, "sheet_url": f"https://docs.google.com/spreadsheets/d/{sid}" if sid else None,
            "configured": bool(lib and creds.exists() and sid),
            "service_account_email": _service_account_email(creds),
            "last_sync": data.get("last_sync"), "last_error": data.get("last_error")}


def _service_account_email(path: Path) -> Optional[str]:
    """The address to share the Sheet with. Only this public field is read from the key file."""
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("client_email")
    except (OSError, ValueError, AttributeError):
        return None


def _client(settings: Settings):
    import gspread
    return gspread.service_account(filename=str(credentials_path(settings)))


# ---------------------------------------------------------------- sync
def _rows_for(tab: Dict[str, Any]) -> List[List[Any]]:
    rows = ([tab["columns"]] if tab["columns"] else []) + tab["rows"]
    return [["" if v is None else (json.dumps(v) if isinstance(v, (dict, list)) else v) for v in r] for r in rows]


def _requests(ws_id: int, name: str, tab: Dict[str, Any], old_rules: int) -> List[Dict[str, Any]]:
    """Formatting: header style, frozen header, widths, and verdict/result colours."""
    reqs: List[Dict[str, Any]] = [{"deleteConditionalFormatRule": {"sheetId": ws_id, "index": 0}}
                                  for _ in range(old_rules)]
    cols = tab["columns"]
    if not cols:                                             # Summary: label/value
        reqs += [{"updateDimensionProperties": {"range": {"sheetId": ws_id, "dimension": "COLUMNS",
                                                          "startIndex": i, "endIndex": i + 1},
                                                "properties": {"pixelSize": px}, "fields": "pixelSize"}}
                 for i, px in ((0, 300), (1, 760))]
        reqs.append({"repeatCell": {"range": {"sheetId": ws_id, "startRowIndex": 0, "endRowIndex": 1},
                                    "cell": {"userEnteredFormat": {"textFormat": {"bold": True, "fontSize": 14}}},
                                    "fields": "userEnteredFormat.textFormat"}})
        return reqs
    reqs.append({"repeatCell": {
        "range": {"sheetId": ws_id, "startRowIndex": 0, "endRowIndex": 1},
        "cell": {"userEnteredFormat": {"backgroundColor": {"red": 0.12, "green": 0.16, "blue": 0.27},
                                       "textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}},
                                       "wrapStrategy": "WRAP"}},
        "fields": "userEnteredFormat(backgroundColor,textFormat,wrapStrategy)"}})
    reqs.append({"updateSheetProperties": {"properties": {"sheetId": ws_id, "gridProperties": {"frozenRowCount": 1,
                                                                                            "frozenColumnCount": 2}},
                                           "fields": "gridProperties(frozenRowCount,frozenColumnCount)"}})
    for i, c in enumerate(cols):
        reqs.append({"updateDimensionProperties": {"range": {"sheetId": ws_id, "dimension": "COLUMNS",
                                                             "startIndex": i, "endIndex": i + 1},
                                                   "properties": {"pixelSize": _WIDTHS.get(c, 110)},
                                                   "fields": "pixelSize"}})
    for key in ("Verdict", "Result"):
        if key in cols:
            i = cols.index(key)
            for word, (r, g, b) in _COLOURS.items():
                reqs.append({"addConditionalFormatRule": {"index": 0, "rule": {
                    "ranges": [{"sheetId": ws_id, "startRowIndex": 1, "startColumnIndex": i, "endColumnIndex": i + 1}],
                    "booleanRule": {"condition": {"type": "TEXT_EQ", "values": [{"userEnteredValue": word}]},
                                    "format": {"backgroundColor": {"red": r, "green": g, "blue": b}}}}}})
    return reqs


def sync(settings: Settings, client: Any = None) -> Dict[str, Any]:
    """Rebuild every tab from the store. Returns per-tab row counts and the Sheet URL."""
    st = status(settings)
    if client is None:
        if not st["configured"]:
            missing = [m for m, ok in (("gspread library (pip install gspread)", st["library"]),
                                       (f"service-account key at {st['credentials_file']}", st["credentials_file_present"]),
                                       ("Sheet URL/ID", bool(st["sheet_id"]))) if not ok]
            raise RuntimeError("Google Sheets is not set up: missing " + "; ".join(missing))
        client = _client(settings)
    data = _load(settings)
    try:
        book = client.open_by_key(st["sheet_id"])
        tables = views.report_tables(settings)
        tables["Summary"]["rows"].insert(1, ["Synced to this sheet (UTC)", now()])
        meta = book.fetch_sheet_metadata({"fields": "sheets(properties(sheetId,title),conditionalFormats)"})
        rule_count = {s["properties"]["title"]: len(s.get("conditionalFormats") or []) for s in meta.get("sheets", [])}
        existing = {ws.title: ws for ws in book.worksheets()}
        counts, reqs = {}, []
        for name in tables:
            tab = tables[name]
            rows = _rows_for(tab)
            width = max(len(r) for r in rows)
            ws = existing.get(name) or book.add_worksheet(title=name, rows=len(rows) + 10, cols=width + 1)
            ws.clear()
            ws.resize(rows=max(len(rows) + 10, 20), cols=max(width, 2))
            ws.update(values=rows, range_name="A1", value_input_option="RAW")
            reqs += _requests(ws.id, name, tab, rule_count.get(name, 0))
            counts[name] = len(tab["rows"])
        if reqs:
            book.batch_update({"requests": reqs})
        data.update(last_sync=now(), last_error=None)
        _save(settings, data)
        return {"ok": True, "rows": counts, "sheet_url": st["sheet_url"] or getattr(book, "url", None),
                "synced_at": data["last_sync"]}
    except Exception as e:                                           # noqa: BLE001
        data["last_error"] = f"{type(e).__name__}: {str(e)[:300]}"
        _save(settings, data)
        raise
