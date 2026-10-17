"""A vs B evaluation.

A = FMP + Claude: Claude gets the FMP data we already pull for the stock
    (8 quarters of income statements + the quality snapshot's estimates,
    ratios, quote, latest balance sheet / cash flow) and nothing else.
B = RAG + Claude: ``rag.answer.ask`` (retrieval over SEC filings; the period is
    read from the question, exactly as a user would type it).

Each question in ``rag/eval_questions.json`` carries the facts a correct
answer must contain (taken from the filings by hand, with the filing it came
from), so grading is mechanical:
  retrieval  - some top-k chunk is from the expected filing AND holds a fact
  answer     - every required fact appears in the answer (numbers unit- and
               rounding-tolerant); unanswerable questions must say not found
  citation   - a cited excerpt holds a fact, and no figure is unverifiable
  period     - the period filter B applied equals the period the question names
"""
from __future__ import annotations

import json
import statistics
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from rag import guard
from rag.answer import ask, claude, is_not_found
from rag.config import NOT_FOUND, ROOT, STATE, Settings

QUESTIONS = ROOT / "eval_questions.json"
FMP_INCOME = STATE / "fmp" / "fmp_income_quarter_2026-10-07.json"
QUALITY_SNAPSHOT = ROOT.parent / "research_agent" / "state" / "quality" / "fmp_snapshot_2026-10-03.json"

SYSTEM_A = f"""You answer questions about public companies using ONLY the Financial Modeling Prep (FMP) data in the user message.
Rules: copy figures exactly as the data states them (unit conversion allowed); never use outside knowledge or estimate;
if you derive a figure write it as: <figure> (computed from FMP fields ...); say which fiscal period each figure belongs to;
if the data does not contain the answer reply exactly: {NOT_FOUND}
Be concise: at most 8 sentences."""


# ------------------------------------------------------------------ baseline A context
def fmp_context(symbol: str) -> str:
    parts = []
    inc = json.loads(FMP_INCOME.read_text(encoding="utf-8"))
    cols = inc["columns"]
    rows = inc["stocks"].get(symbol, [])
    if rows:
        parts.append("FMP income-statement, quarterly (USD):\n" + " | ".join(cols) + "\n"
                     + "\n".join(" | ".join(str(v) for v in r) for r in rows))
    if QUALITY_SNAPSHOT.exists():
        snap = json.loads(QUALITY_SNAPSHOT.read_text(encoding="utf-8"))["stocks"].get(symbol, {})
        keep = {k: snap[k] for k in ("estimates", "ratios_ttm", "quote", "quote_change", "cashflow_q", "balance_q")
                if k in snap}
        if keep:
            parts.append("FMP analyst estimates, TTM ratios, quote, latest cash flow and balance sheet (JSON):\n"
                         + json.dumps(keep, separators=(",", ":")))
    return "\n\n".join(parts) or "(no FMP data for this symbol)"


def ask_a(q: Dict[str, Any]) -> Dict[str, Any]:
    t0 = time.time()
    ctx = fmp_context(q["symbol"])
    out = claude(SYSTEM_A, f"Question: {q['question']}\n\nFMP data for {q['symbol']}:\n{ctx}")
    text = out["text"] or NOT_FOUND
    return {"answer": text, "not_found": is_not_found(text), "guard": guard.check(text, [ctx]),
            "cost_usd": out.get("cost_usd"), "seconds": round(time.time() - t0, 1)}


# ------------------------------------------------------------------ grading
def _has_fact(text: str, fact: Dict[str, Any]) -> bool:
    low = text.lower()
    if "any" in fact:
        return any(p.lower() in low for p in fact["any"])
    target, tol = float(fact["num"]), float(fact.get("tol", 0.01))
    for n in guard.numbers(text):
        v = n["value"] * (1 if n["pct"] else n["scale"])
        for cand in ((v,) if n["pct"] or n["scale"] > 1 or fact.get("exact_units") else (v, v * 1e6, v * 1e9, v * 1e3)):
            if target == 0 and cand == 0 or target and abs(abs(cand) - abs(target)) <= tol * abs(target):
                return True
    return False


def grade_answer(q: Dict[str, Any], r: Dict[str, Any]) -> bool:
    if q.get("unanswerable"):
        return r["not_found"] or NOT_FOUND.rstrip(".").lower() in r["answer"].lower()
    if r["not_found"]:
        return False
    return all(_has_fact(r["answer"], f) for f in q["facts"])


def _from_source(h: Dict[str, Any], src: Dict[str, Any]) -> bool:
    if src.get("form") and h["form"] != src["form"]:
        return False
    if src.get("fiscal_year") and h["fiscal_year"] != src["fiscal_year"]:
        return False
    if src.get("fiscal_quarter") and h["fiscal_quarter"] != src["fiscal_quarter"]:
        return False
    return True


def grade_b(q: Dict[str, Any], r: Dict[str, Any]) -> Dict[str, Any]:
    src = q.get("source", {})
    hits = r["hits"]
    items = r.get("facts", []) + hits                      # citation numbers run over facts, then chunks
    holds = [any(_has_fact(h["text"], f) for f in q.get("facts", [])) for h in items]
    hit_holds = holds[len(items) - len(hits):]
    retrieval = None if q.get("unanswerable") else any(_from_source(h, src) and ok for h, ok in zip(hits, hit_holds))
    cited = [c["n"] for c in r["citations"]]
    if q.get("unanswerable"):
        citation = None
    else:
        citation = bool(cited) and any(holds[n - 1] for n in cited) and not r["guard"]["unverified"]
    want = (q.get("period") or {}).get("fiscal_year"), (q.get("period") or {}).get("fiscal_quarter")
    got = r["filters"].get("fiscal_year"), r["filters"].get("fiscal_quarter")
    period = None if not q.get("period") else (want == got or (want[1] in ("Q4", "FY") and got[1] in ("Q4", "FY")
                                                                and want[0] == got[0]))
    return {"retrieval": retrieval, "citation": citation, "period": period,
            "first_relevant_rank": next((i + 1 for i, (h, ok) in enumerate(zip(hits, hit_holds))
                                         if ok and _from_source(h, src)), None)}


# ------------------------------------------------------------------ runner
def _rate(xs: List[Optional[bool]]) -> Optional[float]:
    xs = [x for x in xs if x is not None]
    return round(100 * sum(xs) / len(xs), 1) if xs else None


def summarize(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    out: Dict[str, Any] = {"questions": len(rows)}
    for sysname in ("A", "B"):
        rs = [r[sysname] for r in rows if sysname in r]
        if not rs:
            continue
        s = {"answer_accuracy_pct": _rate([r["correct"] for r in rs]),
             "answered_with_unverifiable_figures": sum(bool(r["guard"]["unverified"]) for r in rs),
             "said_not_found": sum(r["not_found"] for r in rs),
             "median_seconds": statistics.median(r["seconds"] for r in rs),
             "total_cost_usd": round(sum(r.get("cost_usd") or 0 for r in rs), 4)}
        if sysname == "B":
            s.update({"retrieval_accuracy_pct": _rate([r["grade"]["retrieval"] for r in rs]),
                      "citation_accuracy_pct": _rate([r["grade"]["citation"] for r in rs]),
                      "period_filter_accuracy_pct": _rate([r["grade"]["period"] for r in rs]),
                      "median_retrieval_ms": statistics.median(r["retrieval_ms"] for r in rs)})
        by_cat: Dict[str, List[bool]] = {}
        for row in rows:
            if sysname in row:
                by_cat.setdefault(row["category"], []).append(row[sysname]["correct"])
        s["accuracy_by_category_pct"] = {c: _rate(v) for c, v in sorted(by_cat.items())}
        out[sysname] = s
    return out


def run(settings: Optional[Settings] = None, only: Optional[str] = None, systems: str = "A,B",
        log=print) -> Dict[str, Any]:
    settings = settings or Settings()
    settings.ensure_dirs()
    qs = json.loads(QUESTIONS.read_text(encoding="utf-8"))["questions"]
    if only:
        keep = {x.strip() for x in only.split(",")}
        qs = [q for q in qs if q["id"] in keep]
    want = {s.strip().upper() for s in systems.split(",")}
    rows = []
    for q in qs:
        row: Dict[str, Any] = {"id": q["id"], "symbol": q["symbol"], "category": q["category"],
                               "question": q["question"]}
        if "B" in want:
            r = ask(q["question"], settings, symbol=q["symbol"], k=8)
            g = grade_b(q, r)
            row["B"] = {"answer": r["answer"], "not_found": r["not_found"], "correct": grade_answer(q, r),
                        "grade": g, "guard": r["guard"], "filters": r["filters"],
                        "citations": r["citations"], "retrieval_ms": r["retrieval_ms"], "seconds": r["seconds"],
                        "cost_usd": r.get("cost_usd"),
                        "retrieved": [{"chunk_id": h["chunk_id"], "form": h["form"], "fy": h["fiscal_year"],
                                       "fq": h["fiscal_quarter"], "section": h["section"]} for h in r["hits"]]}
        if "A" in want:
            a = ask_a(q)
            row["A"] = {**a, "correct": grade_answer(q, a)}
        log(f"{q['id']:6} {q['symbol']:5} {q['category']:22} "
            + "  ".join(f"{k}={'OK ' if row[k]['correct'] else 'X  '}" for k in ("A", "B") if k in row)
            + (f" retr={row['B']['grade']['retrieval']} cite={row['B']['grade']['citation']}" if "B" in row else ""))
        rows.append(row)
    result = {"run_at": datetime.now().isoformat(timespec="seconds"), "summary": summarize(rows), "rows": rows}
    path = settings.eval_dir / f"eval_{datetime.now():%Y%m%d_%H%M%S}.json"
    path.write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
    result["saved_to"] = str(path)
    return result
