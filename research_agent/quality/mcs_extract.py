"""Draft MCS ledger entries from an earnings-call transcript: the guidance sentences, parsed.

The parser is deliberately narrow: it only recognises the standard ways CFOs read out guidance
("revenue to be $8.7 billion, plus or minus $200 million", "gross margin in the range of 39.5% plus or
minus 100 basis points", "revenue between $2.75 billion and $2.85 billion", "approximately $1.34 billion").
Everything it finds is a DRAFT for a person to check against the press release before it goes into a
ledger; anything it cannot parse is left out rather than guessed.

Transcripts come from FMP (endpoint earning-call-transcript). This repo's API key cannot read that endpoint
(HTTP 402), so fetch_transcript() raises FmpPlanRestricted there; load a saved FMP response with
load_transcript() instead (e.g. one exported from the FMP connector).
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

_NUM = r"\$?\s?(\d[\d,]*(?:\.\d+)?)\s*(billion|million|bn|m|b)?"
_PM = r",?\s*(?:plus\s+or\s+minus|\+/-|±)\s*"
_METRICS = {
    "revenue": r"(?:total\s+)?(?:revenue|revenues|sales)",
    "gross_margin": r"gross\s+margins?",
    "opex": r"operating\s+expenses|opex",
    "eps": r"(?:diluted\s+)?(?:EPS|earnings\s+per\s+share)",
    "tax_rate": r"tax\s+rate",
}
_LEAD = r"(?:to\s+be|of|at|in\s+the\s+range\s+of|between)?\s*(?:a\s+record\s+)?(?:approximately|about|around|roughly)?\s*"


def _scale(value: str, unit: Optional[str], metric: str) -> float:
    v = float(value.replace(",", ""))
    if metric in ("gross_margin", "tax_rate", "eps"):
        return v
    u = (unit or "").lower()
    if u in ("billion", "bn", "b"):
        return v * 1000
    if u in ("million", "m"):
        return v
    return v / 1e6 if v > 1e5 else v                       # bare dollars -> millions


def _sentences(text: str) -> List[str]:
    return [s.strip() for s in re.split(r"(?<=[.;])\s+(?=[A-Z])", text.replace("\n", " ")) if s.strip()]


def _short(sentence: str, words: int = 25) -> str:
    w = sentence.split()
    return " ".join(w[:words]) + (" ..." if len(w) > words else "")


def parse_guidance(text: str) -> List[Dict[str, Any]]:
    """Every guidance item found, as draft ledger items (unit, kind, low/high/mid, quote)."""
    found: Dict[str, Dict[str, Any]] = {}
    anchors = list(re.finditer(r"(turning to|turn to|now to)\s+(our\s+)?(guidance|outlook)|guidance for (the\s+)?"
                               r"(fiscal\s+)?(first|second|third|fourth|next|Q[1-4]|F?Q[1-4])", text, re.I))
    if anchors:                      # the outlook section, when the call has one, is where guidance lives
        text = text[anchors[-1].start():]
    window = 0                       # guidance is read out as a block: keep looking a few sentences on
    for sent in _sentences(text):
        if re.search(r"\b(was|were|came in|delivered|reported|grew|increased|decreased)\b", sent, re.I) \
                and not re.search(r"\b(expect|will be|to be)\b", sent, re.I):
            continue                 # a result, not a forecast
        if re.search(r"\b(expect|guid|forecast|project|outlook|anticipate)", sent, re.I):
            window = 4
        elif window:
            window -= 1
        else:
            continue
        for metric, name in _METRICS.items():
            if metric in found:
                continue
            item = _parse_one(sent, metric, name)
            if item:
                item["guide_quote"] = _short(sent)
                found[metric] = item
    return list(found.values())


def _parse_one(sent: str, metric: str, name: str) -> Optional[Dict[str, Any]]:
    pct = metric in ("gross_margin", "tax_rate")
    unit = "pct" if pct else "usd" if metric == "eps" else "usd_m"
    head = rf"\b(?:{name})\b[^.;$\d%]{{0,60}}?{_LEAD}"
    if pct:
        m = re.search(head + r"(\d+(?:\.\d+)?)\s?%" + _PM + r"(\d+(?:\.\d+)?)\s*(basis\s+points|bps|%|percentage\s+points)", sent, re.I)
        if m:
            mid, width = float(m.group(1)), float(m.group(2))
            if "basis" in m.group(3).lower() or "bps" in m.group(3).lower():
                width /= 100
            return _item(metric, unit, "range", mid - width, mid + width, mid)
        m = re.search(head + r"(\d+(?:\.\d+)?)\s?%\s*(?:to|and|-|–)\s*(\d+(?:\.\d+)?)\s?%", sent, re.I)
        if m:
            lo, hi = float(m.group(1)), float(m.group(2))
            return _item(metric, unit, "range", lo, hi, (lo + hi) / 2)
        m = re.search(head + r"(\d+(?:\.\d+)?)\s?%", sent, re.I)
        if m:
            v = float(m.group(1))
            return _item(metric, unit, "approx", v, v, v)
        return None
    m = re.search(head + _NUM + r"(?:\s*per\s+share)?" + _PM + _NUM, sent, re.I)
    if m:
        mid = _scale(m.group(1), m.group(2), metric)
        width = _scale(m.group(3), m.group(4) or m.group(2), metric)     # "$8.7 billion ± $200 million"
        return _item(metric, unit, "range", mid - width, mid + width, mid)
    m = re.search(head + _NUM + r"\s*(?:to|and|-|–)\s*" + _NUM, sent, re.I)
    if m:
        lo = _scale(m.group(1), m.group(2) or m.group(4), metric)
        hi = _scale(m.group(3), m.group(4), metric)
        return _item(metric, unit, "range", lo, hi, (lo + hi) / 2)
    m = re.search(head + _NUM, sent, re.I)
    if m and re.search(r"approximately|about|around|roughly", sent, re.I):
        v = _scale(m.group(1), m.group(2), metric)
        return _item(metric, unit, "approx", v, v, v)
    return None


def _item(metric: str, unit: str, kind: str, low: float, high: float, mid: float) -> Dict[str, Any]:
    r = lambda v: round(v, 4)
    return {"metric": metric, "unit": unit, "kind": kind, "low": r(low), "high": r(high), "mid": r(mid),
            "actual": None, "status": "draft - verify against the press release"}


def load_transcript(path: Path) -> Dict[str, Any]:
    """A saved FMP transcript response (a list of {symbol, period, year, date, content}) or plain text."""
    raw = Path(path).read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except ValueError:
        return {"date": None, "content": raw}
    rec = data[0] if isinstance(data, list) else data
    return {"date": rec.get("date"), "content": rec.get("content", ""), "symbol": rec.get("symbol"),
            "period": rec.get("period"), "year": rec.get("year")}


def fetch_transcript(symbol: str, year: int, quarter: int) -> Dict[str, Any]:
    """One transcript from FMP. Raises rag.fmp.client.FmpPlanRestricted on plans without transcripts."""
    from rag.fmp.client import FmpClient
    body = FmpClient().get("earning-call-transcript", symbol=symbol.upper(), year=year, quarter=quarter)
    rec = body[0] if isinstance(body, list) and body else {}
    return {"date": rec.get("date"), "content": rec.get("content", ""), "symbol": symbol.upper(),
            "period": rec.get("period"), "year": rec.get("year")}


def draft_period(transcript: Dict[str, Any], period: str) -> Dict[str, Any]:
    """A ledger period skeleton: the guidance parsed from this call, actuals to fill from the next one."""
    return {"period": period, "guided_on": transcript.get("date"), "reported_on": None,
            "items": parse_guidance(transcript.get("content", ""))}
