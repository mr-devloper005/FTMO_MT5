"""The NASDAQ universe, from NASDAQ Trader's official daily symbol directory.

``nasdaqlisted.txt`` is free, keyless and republished every business day. It
flags ETFs, test issues and financial status; names identify warrants, rights,
units and preferreds. What survives is the screenable common-stock universe.

Incremental by construction: each refresh diffs against the stored universe,
marks new listings active and delistings inactive, and keeps a dated copy of the
raw file so any day's universe can be reconstructed.
"""
from __future__ import annotations

import re
import urllib.request
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from research_agent.config import NASDAQ_LISTING_URL, Settings
from research_agent.store import Store

# Instruments that trade on NASDAQ but are not common stock.
# ADR common shares stay; preferred depositary shares say "Preferred" and go.
_NOT_COMMON = re.compile(r"\b(warrants?|rights?|units?|preferred|notes? due|debentures?|subordinated)\b", re.I)
_FINANCIAL_OK = {"N", ""}          # N = normal; D/E/G/H/J/K/Q = deficient, delinquent, bankrupt...
_YAHOO_OK = re.compile(r"^[A-Z0-9][A-Z0-9\-]{0,9}$")


def fetch_listing(url: str = NASDAQ_LISTING_URL, timeout: int = 30) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "FTMO_MT5-research-agent/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:      # noqa: S310 — fixed https URL
        return r.read().decode("utf-8", errors="replace")


def parse_listing(text: str) -> List[Dict[str, str]]:
    """Pipe-delimited rows; the last line is a 'File Creation Time' footer."""
    lines = [l for l in text.splitlines() if l.strip()]
    if not lines or not lines[0].startswith("Symbol|"):
        raise ValueError("not a NASDAQ Trader listing: header row missing")
    header = lines[0].split("|")
    rows = []
    for line in lines[1:]:
        if line.startswith("File Creation Time"):
            continue
        parts = line.split("|")
        if len(parts) != len(header):
            continue
        rows.append(dict(zip(header, parts)))
    return rows


def classify(row: Dict[str, str]) -> Tuple[bool, str]:
    """Is this row a screenable common stock? Returns (ok, reason when not)."""
    if row.get("Test Issue") == "Y":
        return False, "test issue"
    if row.get("ETF") == "Y":
        return False, "ETF"
    if row.get("Financial Status", "") not in _FINANCIAL_OK:
        return False, f"financial status {row.get('Financial Status')}"
    if _NOT_COMMON.search(row.get("Security Name", "")):
        return False, "not common stock"
    if not _YAHOO_OK.match(to_yahoo(row.get("Symbol", ""))):
        return False, "symbol not usable"
    return True, ""


def to_yahoo(symbol: str) -> str:
    """Yahoo spells share classes with a dash (BRK.B -> BRK-B)."""
    return symbol.strip().upper().replace(".", "-")


def common_stocks(rows: List[Dict[str, str]]) -> Tuple[List[Dict[str, str]], Dict[str, int]]:
    kept, dropped = [], {}
    for r in rows:
        ok, why = classify(r)
        if ok:
            kept.append({"symbol": to_yahoo(r["Symbol"]), "name": r.get("Security Name", "").strip(),
                         "market_category": r.get("Market Category", "")})
        else:
            dropped[why] = dropped.get(why, 0) + 1
    return kept, dropped


def refresh(settings: Settings, text: Optional[str] = None) -> Dict[str, object]:
    """Fetch (or take) today's listing, filter it, and diff it into the store."""
    settings.ensure_dirs()
    if text is None:
        text = fetch_listing()
    archive = settings.state_dir / "universe"
    archive.mkdir(parents=True, exist_ok=True)
    (archive / f"nasdaqlisted-{date.today().isoformat()}.txt").write_text(text, encoding="utf-8")

    rows = parse_listing(text)
    kept, dropped = common_stocks(rows)
    diff = Store(settings.db_path).upsert_universe(kept)
    return {"listed": len(rows), "common_stocks": len(kept), "dropped": dropped, **diff}
