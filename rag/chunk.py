"""Section-aware chunking.

10-K / 10-Q: the SEC "Item" structure (Item 1A Risk Factors, Item 7 MD&A, ...)
is the top-level section; short title-like lines inside an item become
sub-sections. 8-K exhibits (press releases) only have the sub-section level.
Chunks never cross a top-level section. Tables are chunked separately, keep
their header row and the sentence that introduces them.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional

from rag.extract import END_TABLE, PAGE, TABLE

TARGET_WORDS = 280          # ~380 tokens: bge-small embeds the first 512 tokens
TABLE_WORDS = 150           # numbers tokenise heavily
MIN_WORDS = 25

ITEM = re.compile(r"^item\s*(\d{1,2}[a-c]?)\s*[\.:\-\u2013\u2014]*\s*(.*)$", re.I)
PART = re.compile(r"^part\s+(i{1,3}|iv)\b[\.\s:\-\u2013\u2014]*(.*)$", re.I)

ITEM_TITLES_10K = {"1": "Business", "1A": "Risk Factors", "1B": "Unresolved Staff Comments", "1C": "Cybersecurity",
                   "2": "Properties", "3": "Legal Proceedings", "4": "Mine Safety Disclosures",
                   "5": "Market for Registrant's Common Equity", "6": "[Reserved]",
                   "7": "Management's Discussion and Analysis", "7A": "Quantitative and Qualitative Disclosures About Market Risk",
                   "8": "Financial Statements and Supplementary Data", "9": "Changes in and Disagreements with Accountants",
                   "9A": "Controls and Procedures", "9B": "Other Information", "9C": "Foreign Jurisdictions",
                   "10": "Directors, Executive Officers and Corporate Governance", "11": "Executive Compensation",
                   "12": "Security Ownership", "13": "Certain Relationships", "14": "Principal Accountant Fees",
                   "15": "Exhibits and Financial Statement Schedules", "16": "Form 10-K Summary"}
ITEM_TITLES_10Q = {("I", "1"): "Financial Statements", ("I", "2"): "Management's Discussion and Analysis",
                   ("I", "3"): "Quantitative and Qualitative Disclosures About Market Risk",
                   ("I", "4"): "Controls and Procedures", ("II", "1"): "Legal Proceedings",
                   ("II", "1A"): "Risk Factors", ("II", "2"): "Unregistered Sales of Equity Securities",
                   ("II", "3"): "Defaults Upon Senior Securities", ("II", "4"): "Mine Safety Disclosures",
                   ("II", "5"): "Other Information", ("II", "6"): "Exhibits"}


@dataclass
class Chunk:
    section: str
    subsection: str
    text: str
    page: int
    kind: str = "text"                   # text | table
    meta: Dict = field(default_factory=dict)

    @property
    def section_path(self) -> str:
        return f"{self.section} > {self.subsection}" if self.subsection else self.section


def words(s: str) -> int:
    return len(s.split())


def is_subheading(line: str) -> bool:
    if len(line) > 90 or len(line) < 4 or line.endswith((".", ",", ";", ":")) or line[0].isdigit() or line[0] in "($•":
        return False
    letters = sum(c.isalpha() for c in line)
    if letters < 0.7 * len(line.replace(" ", "")):
        return False
    toks = [t for t in re.split(r"\s+", line) if t[:1].isalpha()]
    if not toks or len(toks) > 12:
        return False
    small = {"and", "of", "the", "for", "in", "on", "to", "a", "an", "or", "by", "with", "from", "at", "vs", "per"}
    capped = sum(t[0].isupper() or t.lower() in small for t in toks)
    return line.isupper() or capped == len(toks)


def _item_label(form: str, part: Optional[str], num: str, title: str) -> str:
    num = num.upper()
    if form == "10-Q":
        canon = ITEM_TITLES_10Q.get((part or "I", num))
        return f"Part {part or 'I'}, Item {num}. {canon or title}".strip()
    canon = ITEM_TITLES_10K.get(num)
    return f"Item {num}. {canon or title}".strip()


def sectioned_blocks(lines: List[str], form: str) -> Iterator[Dict]:
    """Yield {'section','subsection','page','kind','text'} paragraphs and tables in order."""
    structured = form in ("10-K", "10-Q")
    section = "Cover and front matter" if structured else "Press release"
    sub, part, page, i = "", None, 1, 0
    while i < len(lines):
        ln = lines[i]
        if ln == PAGE:
            page += 1
            i += 1
            continue
        if ln == TABLE:
            j = lines.index(END_TABLE, i) if END_TABLE in lines[i:] else len(lines)
            rows = [r for r in lines[i + 1:j] if r != PAGE]
            yield {"section": section, "subsection": sub, "page": page, "kind": "table", "rows": rows}
            i = j + 1
            continue
        if structured:
            pm = PART.match(ln)
            if pm and len(ln) < 120:
                part = pm.group(1).upper()
                i += 1
                continue
            im = ITEM.match(ln)
            if im and len(ln) < 160:
                title = im.group(2).strip(" .")
                if not title and i + 1 < len(lines) and len(lines[i + 1]) < 120 and lines[i + 1] != TABLE:
                    title = lines[i + 1].strip(" .")
                    i += 1
                section, sub = _item_label(form, part, im.group(1), title), ""
                i += 1
                continue
        if is_subheading(ln):
            sub = ln.title() if ln.isupper() else ln
            i += 1
            continue
        yield {"section": section, "subsection": sub, "page": page, "kind": "text", "text": ln}
        i += 1


def _split_long(text: str, limit: int) -> List[str]:
    sents = re.split(r"(?<=[\.\?\!;])\s+(?=[A-Z\(\"$])", text)
    out, cur = [], []
    for s in sents:
        if cur and words(" ".join(cur + [s])) > limit:
            out.append(" ".join(cur))
            cur = []
        cur.append(s)
    if cur:
        out.append(" ".join(cur))
    return out


def chunk_lines(lines: List[str], form: str) -> List[Chunk]:
    chunks: List[Chunk] = []
    buf: List[str] = []
    cur: Optional[Dict] = None
    last_text = ""

    def flush(carry: bool = False) -> None:
        nonlocal buf
        if buf and cur is not None:
            text = "\n".join(buf).strip()
            if words(text) >= MIN_WORDS or not chunks or chunks[-1].section != cur["section"] or chunks[-1].kind != "text":
                chunks.append(Chunk(cur["section"], cur["subsection"], text, cur["page"]))
            else:                                                     # tiny tail: fold into previous chunk
                chunks[-1].text += "\n" + text
        tail = buf[-1] if carry and buf and words(buf[-1]) <= 50 else None
        buf = [tail] if tail else []

    for b in sectioned_blocks(lines, form):
        if b["kind"] == "table":
            flush()
            rows, header = b["rows"], b["rows"][0]
            intro = last_text if last_text and words(last_text) <= 60 else ""
            part: List[str] = []
            for r in rows:
                if part and words(" ".join(part + [r])) > TABLE_WORDS:
                    chunks.append(Chunk(b["section"], b["subsection"],
                                        "\n".join(([intro] if intro else []) + part), b["page"], "table"))
                    part = [header] if header != r else []
                part.append(r)
            if part:
                chunks.append(Chunk(b["section"], b["subsection"],
                                    "\n".join(([intro] if intro else []) + part), b["page"], "table"))
            cur = None
            continue
        if cur is None or b["section"] != cur["section"]:
            flush()
            buf = []
            cur = dict(b)
        elif b["subsection"] != cur["subsection"] and words("\n".join(buf)) >= 80:
            flush(carry=False)
            cur = dict(b)
        for piece in (_split_long(b["text"], TARGET_WORDS) if words(b["text"]) > TARGET_WORDS else [b["text"]]):
            if buf and words("\n".join(buf + [piece])) > TARGET_WORDS:
                flush(carry=True)
                cur = {**cur, "page": b["page"], "subsection": b["subsection"]}
            buf.append(piece)
        last_text = b["text"]
    flush()
    return [c for c in chunks if words(c.text) >= 8]
