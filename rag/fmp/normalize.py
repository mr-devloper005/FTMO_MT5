"""FMP JSON -> rows for the structured tables, and -> RAG documents for narrative text.

Pure functions (no I/O), so every rule here is unit-tested on real FMP payloads."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple

from rag.chunk import TARGET_WORDS, _split_long, words
from rag.config import period_label

SOURCE = "FMP"
STATEMENTS = {"income": "income-statement", "balance": "balance-sheet-statement", "cashflow": "cash-flow-statement"}
QUARTERS = ("Q1", "Q2", "Q3", "Q4", "FY")
SKIP_SECTION = re.compile(r"\((?:Tables|Details|Detail)\)|^Cover$|^Audit Information$", re.I)
MIN_NOTE_CHARS = 200


def sha(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


def _date(v: Any) -> Optional[str]:
    if not v:
        return None
    s = str(v)[:10]
    try:
        return date.fromisoformat(s).isoformat()
    except ValueError:
        return None


def _num(v: Any) -> Optional[float]:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _fq(v: Any) -> Optional[str]:
    v = str(v or "").upper()
    return v if v in QUARTERS else None


def _fy(v: Any) -> Optional[int]:
    try:
        return int(str(v)[:4])
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------ structured
def statement_rows(symbol: str, statement: str, period_type: str, records: Iterable[Dict[str, Any]],
                   url: str) -> List[Dict[str, Any]]:
    """One row per period. All FMP fields are kept in ``data``; the key columns are typed."""
    out = []
    for r in records or []:
        end = _date(r.get("date"))
        if not end:
            continue
        fy, fq = _fy(r.get("fiscalYear")), _fq(r.get("period"))
        out.append({"symbol": symbol.upper(), "statement": statement, "period_type": period_type,
                    "period_end": end, "fiscal_year": fy, "fiscal_quarter": fq, "period": period_label(fy, fq),
                    "filing_date": _date(r.get("filingDate")), "accepted_at": r.get("acceptedDate") or None,
                    "reported_currency": r.get("reportedCurrency"), "cik": r.get("cik"),
                    "data": r, "data_sha": sha(r), "source": SOURCE, "source_url": url})
    return out


def fiscal_period_for(announce: str, quarters: List[Tuple[str, int, str]]) -> Tuple[Optional[int], Optional[str]]:
    """The quarter an earnings announcement reports: the latest quarter that ended
    before the announcement, reported within 100 days. Unknown for future dates."""
    d = date.fromisoformat(announce)
    best = [q for q in sorted(quarters)
            if date.fromisoformat(q[0]) < d <= date.fromisoformat(q[0]) + timedelta(days=100)]
    return (best[-1][1], best[-1][2]) if best else (None, None)


def earnings_rows(symbol: str, records: Iterable[Dict[str, Any]], quarters: List[Tuple[str, int, str]],
                  url: str) -> List[Dict[str, Any]]:
    out = []
    for r in records or []:
        d = _date(r.get("date"))
        if not d:
            continue
        fy, fq = fiscal_period_for(d, quarters) if r.get("epsActual") is not None else (None, None)
        out.append({"symbol": symbol.upper(), "announce_date": d, "fiscal_year": fy, "fiscal_quarter": fq,
                    "period": period_label(fy, fq) if fy else None,
                    "eps_actual": _num(r.get("epsActual")), "eps_estimated": _num(r.get("epsEstimated")),
                    "revenue_actual": _num(r.get("revenueActual")),
                    "revenue_estimated": _num(r.get("revenueEstimated")),
                    "last_updated": _date(r.get("lastUpdated")), "data_sha": sha(r), "source": SOURCE,
                    "source_url": url})
    return out


def company_rows(symbol: str, dataset: str, records: Iterable[Dict[str, Any]], url: str,
                 key_field: Optional[str] = None, as_of: Optional[str] = None) -> List[Dict[str, Any]]:
    """Profile, estimates, ratings, targets, TTM metrics, executives, filing index.
    ``key_field`` names the per-record key (e.g. 'date'); without it the dataset is one 'current' record."""
    recs = [r for r in (records or []) if isinstance(r, dict)]
    out = []
    for i, r in enumerate(recs):
        key = str(r.get(key_field) or i) if key_field else ("current" if len(recs) == 1 else str(i))
        out.append({"symbol": symbol.upper(), "dataset": dataset, "record_key": key,
                    "as_of": _date(r.get("date") or r.get("filingDate")) or as_of, "data": r, "data_sha": sha(r),
                    "source": SOURCE, "source_url": url})
    return out


# ------------------------------------------------------------------ narrative -> RAG documents
def clean_note_text(s: str) -> str:
    """XBRL text blocks arrive with headings glued to paragraphs ('ContingenciesTariffsIn February').
    Break at lower->Upper joins after a 3+ letter word, normalise whitespace."""
    s = s.replace(" ", " ").replace("​", "")
    s = re.sub(r"(?<=[a-z]{3})(?=[A-Z][a-z])", "\n", s)
    s = re.sub(r"(?<=[\.\)])(?=[A-Z][a-z])", "\n", s)                     # 'year.The' -> new paragraph
    return "\n".join(re.sub(r"[ \t]+", " ", ln).strip() for ln in s.splitlines() if ln.strip())


def report_notes(report: Dict[str, Any]) -> List[Tuple[str, str]]:
    """(note title, cleaned text) for each narrative note in a financial-reports-json payload."""
    notes = []
    for key, rows in report.items():
        if key in ("symbol", "period", "year") or not isinstance(rows, list) or not rows:
            continue
        first = rows[0] if isinstance(rows[0], dict) else {}
        title = next(iter(first), key) if first else key
        if SKIP_SECTION.search(title) or SKIP_SECTION.search(key):
            continue
        texts = []
        for row in rows[1:]:
            for vals in (row.values() if isinstance(row, dict) else []):
                for v in (vals if isinstance(vals, list) else [vals]):
                    if isinstance(v, str) and len(v) >= MIN_NOTE_CHARS:
                        texts.append(v)
        if texts:
            notes.append((title.strip(), clean_note_text("\n".join(texts))))
    return notes


def report_form(report: Dict[str, Any]) -> Optional[str]:
    for row in report.get("Cover", []) or []:
        if isinstance(row, dict) and "Document Type" in row:
            v = row["Document Type"]
            return (v[0] if isinstance(v, list) and v else str(v)).strip() or None
    return None


def _chunk_paragraphs(paras: List[str], limit: int = TARGET_WORDS) -> List[str]:
    out, cur = [], []
    for p in paras:
        for piece in (_split_long(p, limit) if words(p) > limit else [p]):
            if cur and words(" ".join(cur + [piece])) > limit:
                out.append("\n".join(cur))
                cur = []
            cur.append(piece)
    if cur:
        out.append("\n".join(cur))
    return [c for c in out if words(c) >= 8]


def report_document(symbol: str, company: str, fiscal_year: int, period: str, report: Dict[str, Any],
                    filing_date: Optional[str], url: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """The notes to the financial statements of one 10-Q / 10-K, as one RAG document."""
    form = report_form(report) or ("10-K" if period == "FY" else "10-Q")
    fq = _fq(period)
    doc_id = f"FMP:report:{symbol.upper()}:{fiscal_year}:{period}"
    label = period_label(fiscal_year, fq)
    dtype = f"notes to financial statements ({form}, via FMP)"
    chunks = []
    for title, text in report_notes(report):
        for piece in _chunk_paragraphs(text.splitlines()):
            chunks.append({"chunk_id": f"{doc_id}:{len(chunks):04d}", "doc_id": doc_id, "symbol": symbol.upper(),
                           "company": company, "document_type": dtype, "form": form, "filing_date": filing_date,
                           "fiscal_year": fiscal_year, "fiscal_quarter": fq, "section": "Notes to financial statements",
                           "subsection": title, "page": None, "kind": "text", "source_url": url, "text": piece,
                           "source": SOURCE, "period": label})
    doc = {"doc_id": doc_id, "symbol": symbol.upper(), "company": company, "form": form, "accession": None,
           "file": f"financial-reports-json {fiscal_year} {period}", "filing_date": filing_date,
           "fiscal_year": fiscal_year, "fiscal_quarter": fq, "source_url": url, "sha256": sha(report),
           "n_chunks": len(chunks), "source": SOURCE, "period": label}
    return doc, chunks


def profile_document(symbol: str, profile: Dict[str, Any], url: str,
                     as_of: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    company = profile.get("companyName") or symbol.upper()
    doc_id = f"FMP:profile:{symbol.upper()}"
    facts = ", ".join(f"{k}: {profile[k]}" for k in ("sector", "industry", "exchange", "country", "ceo",
                                                     "fullTimeEmployees", "ipoDate", "website") if profile.get(k))
    paras = [p for p in [facts] + str(profile.get("description") or "").split("\n") if p.strip()]
    chunks = [{"chunk_id": f"{doc_id}:{i:04d}", "doc_id": doc_id, "symbol": symbol.upper(), "company": company,
               "document_type": "company profile (FMP)", "form": "PROFILE", "filing_date": as_of,
               "fiscal_year": None, "fiscal_quarter": None, "section": "Company profile", "subsection": company,
               "page": None, "kind": "text", "source_url": url, "text": t, "source": SOURCE, "period": None}
              for i, t in enumerate(_chunk_paragraphs(paras))]
    doc = {"doc_id": doc_id, "symbol": symbol.upper(), "company": company, "form": "PROFILE",
           "file": "profile", "filing_date": as_of, "source_url": url,
           "sha256": sha({k: profile.get(k) for k in ("description", "sector", "industry", "ceo", "companyName")}),
           "n_chunks": len(chunks), "source": SOURCE, "period": None}
    return doc, chunks


# ------------------------------------------------------------------ earnings-call transcripts
TURN = re.compile(r"^\s*([A-Z][A-Za-z0-9 .,'&\-]{1,60}?)\s*:\s+(.+)$", re.S)
QA_START = re.compile(r"\b(first question|next question|question (?:comes|is) from|(?:open|turn)(?: it| the call| the line)? "
                      r"(?:up )?(?:for|to) (?:your )?questions|begin the question|take (?:our|your) (?:first )?questions?|"
                      r"move (?:on )?to (?:the )?Q\s?&\s?A|go to (?:the )?Q\s?&\s?A|say\.com questions|"
                      r"(?:retail|shareholder|investor) questions)\b", re.I)
TRANSCRIPT_WORDS = 280


def transcript_turns(content: str) -> List[Tuple[str, str]]:
    """'Name : text' paragraphs -> [(speaker, text)]. Lines without a speaker continue the previous turn."""
    turns: List[Tuple[str, str]] = []
    for para in re.split(r"\n\s*\n|\n(?=[A-Z][A-Za-z0-9 .,'&\-]{1,60}?\s*:\s)", content or ""):
        para = " ".join(para.split())
        if not para:
            continue
        m = TURN.match(para)
        if m and len(m.group(1).split()) <= 6:
            turns.append((m.group(1).strip(), m.group(2).strip()))
        elif turns:
            turns[-1] = (turns[-1][0], turns[-1][1] + " " + para)
        else:
            turns.append(("Unknown speaker", para))
    return turns


def transcript_sections(turns: List[Tuple[str, str]]) -> List[str]:
    """'Prepared remarks' until the call moves to questions (never on the opening turn), then 'Q&A'."""
    out, qa = [], False
    for i, (_, text) in enumerate(turns):
        m = QA_START.search(text) if not qa and i > 0 else None
        if m:
            qa = True
            # "...and with that, we'll open the line for questions" closing a long speech: the speech is
            # still prepared remarks and Q&A starts with the next turn
            closing = words(text) > 80 and m.start() > 0.6 * len(text)
            out.append("Prepared remarks" if closing else "Q&A")
            continue
        out.append("Q&A" if qa else "Prepared remarks")
    return out


def transcript_document(symbol: str, company: str, rec: Dict[str, Any],
                        url: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """One earnings call as one RAG document; chunks keep speaker names and the prepared/Q&A split."""
    fy, q = int(rec["year"]), str(rec.get("period") or "").upper()
    fq = _fq(q)
    doc_id = f"FMP:transcript:{symbol.upper()}:{fy}:{q}"
    label = period_label(fy, fq)
    turns = transcript_turns(rec.get("content", ""))
    secs = transcript_sections(turns)
    chunks: List[Dict[str, Any]] = []

    def emit(section: str, speakers: List[str], parts: List[str]) -> None:
        names = list(dict.fromkeys(speakers))
        chunks.append({"chunk_id": f"{doc_id}:{len(chunks):04d}", "doc_id": doc_id, "symbol": symbol.upper(),
                       "company": company, "document_type": "earnings call transcript (FMP)", "form": "TRANSCRIPT",
                       "filing_date": _date(rec.get("date")), "fiscal_year": fy, "fiscal_quarter": fq,
                       "section": f"Earnings call - {section}",
                       "subsection": ", ".join(names[:4]) + (" +" if len(names) > 4 else ""),
                       "page": None, "kind": "text", "source_url": url, "text": "\n".join(parts),
                       "source": SOURCE, "period": label})

    cur_sec, speakers, parts = None, [], []
    for (who, text), sec in zip(turns, secs):
        pieces = _split_long(text, TRANSCRIPT_WORDS) if words(text) > TRANSCRIPT_WORDS else [text]
        for k, piece in enumerate(pieces):
            line = f"{who}: {piece}" if k == 0 else f"{who} (continued): {piece}"
            if parts and (sec != cur_sec or words(" ".join(parts + [line])) > TRANSCRIPT_WORDS):
                emit(cur_sec, speakers, parts)
                speakers, parts = [], []
            cur_sec = sec
            speakers.append(who)
            parts.append(line)
    if parts:
        emit(cur_sec, speakers, parts)
    doc = {"doc_id": doc_id, "symbol": symbol.upper(), "company": company, "form": "TRANSCRIPT",
           "file": f"earning-call-transcript {fy} {q}", "filing_date": _date(rec.get("date")), "fiscal_year": fy,
           "fiscal_quarter": fq, "source_url": url, "sha256": sha(rec.get("content", "")), "n_chunks": len(chunks),
           "source": SOURCE, "period": label}
    return doc, chunks
