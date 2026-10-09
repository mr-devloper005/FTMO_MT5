"""Number guard: every figure in an answer must be traceable to the evidence the
model was shown. Unit-aware ("$54.2 billion" matches a table cell "54,229" in
millions) and rounding-aware. Figures the model marks as computed
("12.8 (computed from [1] and [2])") are reported separately, not trusted
silently."""
from __future__ import annotations

import re
from typing import Dict, Iterable, List, Set

SCALE = {"trillion": 1e12, "billion": 1e9, "bn": 1e9, "b": 1e9, "million": 1e6, "mn": 1e6, "m": 1e6,
         "thousand": 1e3, "k": 1e3}
NUM = re.compile(r"(?<![\w.])(\$)?\s?\(?(-?\d{1,3}(?:,\d{3})+|-?\d+)(\.\d+)?\)?\s?"
                 r"(%|percent|trillion|billion|million|thousand|bn|mn|[BMK](?![a-zA-Z]))?", re.I)
IGNORE = re.compile(r"\[\d+(?:\s*[,\u2013-]\s*\d+)*\]|\b\d{4}[-\u2010-\u2013]\d{2}[-\u2010-\u2013]\d{2}\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b|"  # cites, dates
                    r"\b(?:Q[1-4]|H[12]|FY\s?'?\d{2,4}|10-K|10-Q|8-K|Item\s+\d+[A-C]?|(?:19|20)\d\d|"
                    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|"
                    r"Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?\s+\d{1,2}"
                    r"(?:\s*(?:,|and|&|\u2013|-)\s*\d{1,2}\b(?!,\d))*)\b", re.I)
# "(computed from [a] and [b])" covers the figure it follows, up to 6 plain words earlier in the clause
# ("a beat of $4.29 per share (computed from [2])")
COMPUTED = re.compile(r"(-?\$?\d[\d,]*(?:\.\d+)?\s?(?:%|percent|billion|million|[BM])?(?:\s+[A-Za-z][\w\-']*){0,6})?"
                      r"\s*\(computed[^)]*\)", re.I)
GLUED = re.compile(r"(?<=[A-Za-z])(?=\d)")           # XBRL text runs labels into numbers: "Malaysia445"


def numbers(text: str) -> List[Dict]:
    out = []
    for m in NUM.finditer(IGNORE.sub(" ", text)):
        dollar, whole, frac, unit = m.groups()
        try:
            v = float((whole + (frac or "")).replace(",", ""))
        except ValueError:
            continue
        unit = (unit or "").lower()
        out.append({"raw": m.group(0).strip(), "value": v, "decimals": len(frac) - 1 if frac else 0,
                    "pct": unit in ("%", "percent"), "scale": SCALE.get(unit, 1.0), "dollar": bool(dollar),
                    "plain": not dollar and not unit and "," not in whole})
    return out


def evidence_values(texts: Iterable[str]) -> Set[float]:
    vals: Set[float] = set()
    for t in texts:
        for n in numbers(GLUED.sub(" ", t)):
            base = n["value"] * n["scale"]
            vals.add(round(n["value"], 6))
            for mult in (1.0, 1e3, 1e6, 1e9):          # tables "in millions"/"in thousands"
                vals.add(round(base * mult, 6))
    return vals


def _matches(n: Dict, vals: Set[float]) -> bool:
    a = abs(n["value"] * (1 if n["pct"] else n["scale"]))
    if a == 0:
        return True
    for v in vals:
        v = abs(v)
        if v and abs(a - v) <= 0.006 * v:
            return True
        if not n["pct"] and n["scale"] > 1 and round(v / n["scale"], n["decimals"]) == n["value"]:
            return True
    return False


def check(answer: str, evidence: Iterable[str]) -> Dict:
    """{'verified': [...], 'unverified': [...], 'computed': [...]} by raw figure text."""
    computed = [m.group(0).strip() for m in COMPUTED.finditer(answer)]
    body = COMPUTED.sub(" ", answer)
    vals = evidence_values(evidence)
    verified, unverified = [], []
    for n in numbers(body):
        if n["plain"] and abs(n["value"]) <= 12 and not n["decimals"]:
            continue                                     # counts, list numbers, "3 segments"
        (verified if _matches(n, vals) else unverified).append(n["raw"])
    return {"verified": verified, "unverified": unverified, "computed": computed}
