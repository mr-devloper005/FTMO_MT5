"""Company research for the research agent: a READ-ONLY bridge to the RAG (rag/).

coverage(symbol)          deterministic inventory of the company evidence in the RAG
                          database + an evidence-quality grade (Strong / Partial / Missing)
                          and an explicit list of what is missing. No LLM.
research(...)             one question through rag.answer.ask (cited, number-checked);
                          every call is logged to company_research_log for the report.
save_context / contexts   the agent's company-context write-up per stock, kept apart
                          from the deterministic backtest verdict (never changes it).

Nothing here writes to the RAG database or calls FMP/SEC; it only reads what
``python -m rag fmp ingest`` / ``python -m rag ingest`` stored.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from research_agent.config import Settings
from research_agent.store import Store, now

QUALITY = ("Missing", "Partial", "Strong")
MAX_QUESTION = 400

LOG_SCHEMA = """
CREATE TABLE IF NOT EXISTS company_research_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, symbol TEXT, question TEXT, answer TEXT,
  not_found INTEGER, grounded INTEGER, route_json TEXT, citations_json TEXT, evidence_json TEXT,
  guard_json TEXT, factcheck_json TEXT, cost_usd REAL, seconds REAL, created_at TEXT);
CREATE TABLE IF NOT EXISTS company_context (
  run_id TEXT, symbol TEXT, financial_condition TEXT, risks TEXT, guidance TEXT, management TEXT,
  company_context TEXT, final_reason TEXT, created_at TEXT, PRIMARY KEY (run_id, symbol));
"""


def _db(settings: Settings) -> Store:
    st = Store(settings.db_path)
    with st.conn() as c:
        c.executescript(LOG_SCHEMA)
    return st


# ------------------------------------------------------------------ evidence inventory (deterministic)
def coverage(symbol: str) -> Dict[str, Any]:
    """What company evidence the RAG holds for ``symbol`` and what is missing. Pure reads."""
    from rag.config import Settings as RagSettings
    from rag.store import Store as RagStore
    symbol = symbol.upper()
    with RagStore(RagSettings().pg_config()) as st:
        q = lambda sql, *a: st.db.execute(sql, a).fetchall()                       # noqa: E731
        stm = q("SELECT period_type, count(DISTINCT period_end) AS n, max(period_end) AS latest, "
                "max(period) FILTER (WHERE period_end = (SELECT max(period_end) FROM rag.financial_statements "
                "WHERE symbol=%s AND period_type='quarter')) AS latest_label "
                "FROM rag.financial_statements WHERE symbol=%s GROUP BY period_type", symbol, symbol)
        earn = q("SELECT count(*) AS n, max(announce_date) FILTER (WHERE eps_actual IS NOT NULL) AS latest "
                 "FROM rag.earnings WHERE symbol=%s", symbol)[0]
        docs = q("SELECT source, form, count(*) AS docs, sum(n_chunks) AS chunks, "
                 "string_agg(coalesce(period, 'n/a'), ', ' ORDER BY filing_date DESC) AS periods "
                 "FROM rag.documents WHERE symbol=%s GROUP BY source, form ORDER BY source, form", symbol)
        # an endpoint counts as refused only if its LATEST call was refused (a later success supersedes it)
        refused = q("SELECT * FROM (SELECT DISTINCT ON (endpoint) endpoint, outcome, http_status, message "
                    "FROM rag.fetch_log WHERE symbol=%s AND outcome IN ('ok', 'unsupported') "
                    "ORDER BY endpoint, fetched_at DESC) x WHERE outcome='unsupported'", symbol)
        sets = q("SELECT dataset, count(*) AS n FROM rag.company_data WHERE symbol=%s GROUP BY dataset", symbol)
    by_type = {r["period_type"]: r for r in stm}
    quarters = by_type.get("quarter", {}).get("n", 0) if by_type.get("quarter") else 0
    filings = [d for d in docs if d["form"] in ("10-K", "10-Q", "8-K")]
    mgmt = [d for d in docs if d["form"] in ("TRANSCRIPT", "8-K") or "release" in (d["form"] or "").lower()]
    refused_eps = {r["endpoint"]: r for r in refused}
    plan_blocked = any("for 'symbol'" in (r["message"] or "") for r in refused)

    missing: List[str] = []
    if plan_blocked:
        missing.append("FMP financial data unavailable: ticker not covered by the current FMP plan (HTTP 402)")
    if not quarters:
        missing.append("No structured financial statements")
    if not filings:
        missing.append("No filing text (10-K/10-Q notes or SEC filings)")
    if not any(d["source"] == "SEC EDGAR" for d in docs):
        missing.append("SEC filings (MD&A, Risk Factors) not ingested - SEC_USER_AGENT not set")
    if not any(d["form"] == "TRANSCRIPT" for d in docs):
        why = (" - FMP plan refuses transcripts (HTTP 402)"
               if "earning-call-transcript-dates" in refused_eps else "")
        missing.append("Management transcript unavailable" + why)
    if not mgmt:
        why = " - FMP plan refuses press releases (HTTP 402)" if "news/press-releases" in refused_eps else ""
        missing.append("Earnings release / company guidance text unavailable" + why)

    if quarters >= 4 and filings and mgmt:
        quality = "Strong"
    elif quarters or filings:
        quality = "Partial"
    else:
        quality = "Missing"
    return {"symbol": symbol, "evidence_quality": quality,
            "statements": {k: {"periods": v["n"], "latest_period_end": str(v["latest"]),
                               "latest_label": v.get("latest_label")} for k, v in by_type.items()},
            "earnings": {"rows": earn["n"], "latest_reported": str(earn["latest"]) if earn["latest"] else None},
            "documents": [{k: (int(v) if k in ("docs", "chunks") and v is not None else v) for k, v in d.items()}
                          for d in docs],
            "other_datasets": {r["dataset"]: r["n"] for r in sets},
            "fmp_refused_endpoints": sorted(refused_eps), "ticker_blocked_by_fmp_plan": plan_blocked,
            "missing": missing,
            "rule": "Strong = >=4 quarters of statements + filing text + management/guidance text; "
                    "Partial = some statements or filing text; Missing = neither (profile at most)."}


def coverage_line(cov: Dict[str, Any]) -> str:
    q = cov["statements"].get("quarter", {})
    docs = sum(d["docs"] for d in cov["documents"] if d["form"] in ("10-K", "10-Q"))
    calls = sum(d["docs"] for d in cov["documents"] if d["form"] == "TRANSCRIPT")
    releases = sum(d["docs"] for d in cov["documents"] if d["form"] == "8-K")
    return (f"{cov['evidence_quality']}: {q.get('periods', 0)} quarters of statements"
            f"{' (latest ' + q['latest_label'] + ')' if q.get('latest_label') else ''}, "
            f"{docs} 10-K/10-Q note sets, {calls} earnings-call transcripts, {releases} earnings releases")


# ------------------------------------------------------------------ one RAG question
def research(settings: Settings, run_id: str, symbol: str, question: str) -> Dict[str, Any]:
    """Ask the RAG one question about one company. Read-only; answer is cited and number-checked."""
    from rag.answer import ask
    from rag.config import Settings as RagSettings
    question = question.strip()[:MAX_QUESTION]
    r = ask(question, RagSettings(), symbol=symbol.upper())
    items = r.get("facts", []) + r.get("hits", [])
    evidence = [{"n": i + 1, "kind": it.get("kind", "document"),
                 "source": it["source"] if it.get("kind") == "fact" else
                 f"{it['symbol']} {it['form']} via {it.get('source') or 'SEC EDGAR'} ({it.get('period') or ''}) "
                 f"{it['section']} > {it.get('subsection') or ''}",
                 "excerpt": " ".join(it["text"].split())[:280]} for i, it in enumerate(items[:8])]
    out = {"symbol": symbol.upper(), "question": question, "answer": r["answer"], "not_found": r["not_found"],
           "grounded": r.get("grounded", True), "route": r.get("route"),
           "citations": [c["source"] for c in r.get("citations", [])], "evidence": evidence,
           "number_guard": {"unverified": r["guard"]["unverified"], "computed": r["guard"]["computed"]},
           "database_check": r.get("factcheck"), "cost_usd": r.get("cost_usd") or 0.0, "seconds": r.get("seconds")}
    with _db(settings).conn() as c:
        c.execute("INSERT INTO company_research_log (run_id, symbol, question, answer, not_found, grounded, "
                  "route_json, citations_json, evidence_json, guard_json, factcheck_json, cost_usd, seconds, "
                  "created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (run_id, out["symbol"], question, out["answer"], int(out["not_found"]), int(bool(out["grounded"])),
                   json.dumps(out["route"]), json.dumps(out["citations"]), json.dumps(evidence),
                   json.dumps(out["number_guard"]), json.dumps(out["database_check"], default=str),
                   out["cost_usd"], out["seconds"], now()))
    return out


def research_log(settings: Settings, run_id: str) -> List[Dict[str, Any]]:
    with _db(settings).conn() as c:
        rows = c.execute("SELECT * FROM company_research_log WHERE run_id=? ORDER BY id", (run_id,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        for k in ("route_json", "citations_json", "evidence_json", "guard_json", "factcheck_json"):
            d[k[:-5]] = json.loads(d.pop(k) or "null")
        out.append(d)
    return out


# ------------------------------------------------------------------ the agent's company write-up
FIELDS = ("financial_condition", "risks", "guidance", "management", "company_context", "final_reason")


def save_context(settings: Settings, run_id: str, symbol: str, fields: Dict[str, str]) -> Dict[str, Any]:
    row = {k: str(fields.get(k) or "").strip()[:3000] for k in FIELDS}
    with _db(settings).conn() as c:
        c.execute("INSERT OR REPLACE INTO company_context (run_id, symbol, financial_condition, risks, guidance, "
                  "management, company_context, final_reason, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                  (run_id, symbol.upper(), *[row[k] for k in FIELDS], now()))
    return {"symbol": symbol.upper(), **row}


def contexts(settings: Settings, run_id: str) -> Dict[str, Dict[str, Any]]:
    with _db(settings).conn() as c:
        rows = c.execute("SELECT * FROM company_context WHERE run_id=?", (run_id,)).fetchall()
    return {r["symbol"]: dict(r) for r in rows}
