"""Leaderboard to CSV, and the full report to Excel.

CSV: the leaderboard rows asked for (headline columns first — Stock, Strategy,
Return, Buy & Hold, Outperformance, Robustness, Verdict, Reason — then detail).

Excel: the report workbook — Summary, Timeframes, Leaderboard, Strategy Tests, Screening —
built from ``views.report_tables``, the same tables the dashboard shows and the
Google Sheet receives.
"""
from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from research_agent.config import Settings

HEADLINE = ["Rank", "Stock", "Strategy", "Timeframe", "Return %", "Buy & Hold %", "Outperformance (pts)",
            "Robustness", "Verdict", "Reason"]
DETAIL = ["Company", "Source", "CAGR %", "B&H CAGR %", "Max DD %", "B&H Max DD %", "Sharpe", "Trades",
          "Win %", "Profit factor", "OOS vs B&H (pts)", "Folds won", "Survives slippage (ticks)",
          "Param variations held %", "Years", "Parameters", "Data as of", "Tested at"]
COLUMNS = HEADLINE + DETAIL
_FILL = {"Recommended": "D7F5E1", "Watch": "FFF4CC", "Rejected": "FBE0E0", "PASS": "D7F5E1", "FAIL": "FBE0E0"}
_WIDTHS = {"Rank": 6, "Stock": 8, "Symbol": 8, "Timeframe": 10, "Label": 12, "History": 22,
           "Best result": 40, "Strategy": 30, "Best Strategy": 30, "Reason": 70, "Company": 28,
           "Parameters": 40, "Robustness": 17, "Verdict": 13, "Result": 8, "Screened at": 22, "Data as of": 12}


def _cell(v: Any) -> Any:
    if isinstance(v, dict):
        return json.dumps(v, sort_keys=True)
    if isinstance(v, float):
        return round(v, 2)
    return v


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def to_csv(rows: List[Dict[str, Any]], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: _cell(r.get(k)) for k in COLUMNS})
    return path


def to_xlsx(path: Path, settings: Settings) -> Path:
    """The report workbook: the same four tabs as the dashboard and the Google Sheet (views.report_tables)."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    from research_agent import views

    wb = Workbook()
    wb.remove(wb.active)
    for name, tab in views.report_tables(settings).items():
        ws = wb.create_sheet(name)
        cols = tab["columns"]
        if cols:
            ws.append(cols)
            for c in ws[1]:
                c.font = Font(bold=True, color="FFFFFF")
                c.fill = PatternFill("solid", fgColor="1F2A44")
                c.alignment = Alignment(vertical="center", wrap_text=True)
            ws.freeze_panes = "C2"
        for row in tab["rows"]:
            ws.append([_cell(v) for v in row])
        if cols:
            ws.auto_filter.ref = ws.dimensions
            for col_name in ("Verdict", "Result"):
                if col_name in cols:
                    i = cols.index(col_name) + 1
                    for r in range(2, ws.max_row + 1):
                        fill = _FILL.get(ws.cell(row=r, column=i).value)
                        if fill:
                            ws.cell(row=r, column=i).fill = PatternFill("solid", fgColor=fill)
            if "Reason" in cols:
                ri = cols.index("Reason")
                for row in ws.iter_rows(min_row=2):
                    row[ri].alignment = Alignment(wrap_text=True, vertical="top")
            for i, k in enumerate(cols, 1):
                ws.column_dimensions[get_column_letter(i)].width = _WIDTHS.get(k, 13)
        else:                                            # Summary: label / value
            ws.column_dimensions["A"].width = 38
            ws.column_dimensions["B"].width = 110
            for r in range(1, ws.max_row + 1):
                a = ws.cell(row=r, column=1)
                if r == 1 or (a.value and str(a.value).isupper()):
                    a.font = Font(bold=True, size=13 if r == 1 else 11)
                ws.cell(row=r, column=2).alignment = Alignment(wrap_text=True, vertical="top")
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


def export(settings: Settings, rows: List[Dict[str, Any]], summary: Dict[str, Any],
           name: Optional[str] = None) -> Dict[str, str]:
    """Write CSV + XLSX into the exports folder (never anywhere else)."""
    base = settings.exports_dir / f"{name or 'leaderboard'}-{_stamp()}"
    out = {"csv": str(to_csv(rows, base.with_suffix(".csv")))}
    try:
        out["xlsx"] = str(to_xlsx(base.with_suffix(".xlsx"), settings))
    except ImportError:
        out["xlsx_error"] = "openpyxl not installed: pip install 'openpyxl>=3.1' (or the 'research' extra)"
    return out
