"""Score stocks with Rahul's lenses and store the results in the research database.

    snapshot (FMP responses, saved as JSON)  ->  inputs.py  ->  lenses.py  ->  model.py  ->  `quality` table

The database row is the source of truth for the dashboard, Excel and Google
Sheet. The raw FMP snapshot is kept beside it so any score can be traced back
to the exact numbers it came from.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

from research_agent.config import Settings
from research_agent.quality import inputs, lenses as L, mcs, model
from research_agent.store import Store, now

SCHEMA = """
CREATE TABLE IF NOT EXISTS quality (
    symbol TEXT PRIMARY KEY, as_of TEXT, computed_at TEXT, source TEXT,
    composite REAL, bucket TEXT, lenses_json TEXT NOT NULL, why_json TEXT,
    inputs_json TEXT, notes_json TEXT
);
"""

LENS_NAMES = ["fundamentals", "valuation", "technicals", "management", "options"]


def _db(settings: Settings) -> Store:
    store = Store(settings.db_path)
    with store.conn() as c:
        c.executescript(SCHEMA)
    return store


def score_snapshot(snapshot: Dict[str, Any], today: Optional[date] = None) -> Dict[str, Dict[str, Any]]:
    """Every stock in an FMP snapshot -> lens scores, composite, bucket, reasons and the inputs used."""
    out: Dict[str, Dict[str, Any]] = {}
    for sym, raw in snapshot["stocks"].items():
        co = inputs.company(raw, raw["sector"])
        fs = inputs.peer_comp(raw, {p: snapshot["peers"][p] for p in raw.get("peers", []) if p in snapshot["peers"]}, today)
        ta = inputs.technicals(raw)
        F, V, T = L.fundamentals_score(co), L.valuation_score(co, fs, ta), L.technicals_score(ta)
        M = mcs.management_lens(sym)                 # None unless a guidance ledger exists for the stock
        lenses = {"fundamentals": F, "valuation": V, "technicals": T, "management": M and M["score"],
                  "options": None}
        comp = model.composite(lenses)
        why = {"fundamentals": L.fundamentals_rationale(co, F), "valuation": L.valuation_rationale(co, fs, ta, V),
               "technicals": L.technicals_rationale(ta, T)}
        if M:
            why["management"] = M["why"]
        missing = {"options": "no options data source yet (not in FMP; Massive plan lacks chains)"}
        if not M:
            missing["management"] = "no guidance ledger yet (research_agent/quality/ledgers/<SYMBOL>.json)"
        out[sym] = {
            "lenses": lenses, "composite": comp, "bucket": model.bucket(comp), "why": why,
            "inputs": {"sector": raw["sector"], "fundamentals": co["fundamentals"], "valuation": fs, "technicals": ta,
                       "peer_source": raw.get("peer_source")},
            "notes": {"technicals": inputs.TECHNICAL_NOTES, "missing": missing},
        }
    return out


def save(settings: Settings, results: Dict[str, Dict[str, Any]], as_of: str, source: str) -> int:
    store = _db(settings)
    with store.conn() as c:
        for sym, r in results.items():
            c.execute("""INSERT OR REPLACE INTO quality(symbol, as_of, computed_at, source, composite, bucket,
                                                       lenses_json, why_json, inputs_json, notes_json)
                         VALUES(?,?,?,?,?,?,?,?,?,?)""",
                      (sym, as_of, now(), source, r["composite"], r["bucket"], json.dumps(r["lenses"]),
                       json.dumps(r["why"]), json.dumps(r["inputs"], default=str), json.dumps(r["notes"])))
    return len(results)


def load(settings: Settings) -> List[Dict[str, Any]]:
    store = _db(settings)
    with store.conn() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM quality ORDER BY composite DESC")]
    for r in rows:
        for k in ("lenses", "why", "inputs", "notes"):
            r[k] = json.loads(r.pop(f"{k}_json") or "null")
    return rows


def import_snapshot(settings: Settings, path: Path, as_of: str, today: Optional[date] = None) -> Dict[str, Any]:
    snapshot = json.loads(Path(path).read_text(encoding="utf-8"))
    results = score_snapshot(snapshot, today)
    n = save(settings, results, as_of=as_of, source=f"FMP snapshot {Path(path).name}")
    return {"stored": n, "symbols": sorted(results), "as_of": as_of}
