"""Filing HTML -> clean lines. Paragraphs become one line each; tables become
``[TABLE] ... [/TABLE]`` blocks of ``cell | cell`` rows; page breaks become
``[[PAGE]]`` so chunks can cite a page. Hidden inline-XBRL data is dropped."""
from __future__ import annotations

import re
import warnings
from typing import List

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

BLOCK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "center", "blockquote", "pre", "dt", "dd"}
PAGE_BREAK = re.compile(r"page-break-(?:before|after)\s*:\s*always", re.I)
HIDDEN = re.compile(r"display\s*:\s*none", re.I)
NOISE = re.compile(r"^(?:\d{1,3}|[ivxlc]{1,6}|table of contents|index|page \d+( of \d+)?)$", re.I)
TABLE, END_TABLE, PAGE = "[TABLE]", "[/TABLE]", "[[PAGE]]"


def _cell_text(td) -> str:
    return re.sub(r"\s+", " ", td.get_text(" ", strip=True)).strip()


def _table_rows(table) -> List[str]:
    rows = []
    for tr in table.find_all("tr"):
        cells = [_cell_text(td) for td in tr.find_all(["td", "th"])]
        merged: List[str] = []
        for c in cells:
            if not c:
                continue
            if merged and (c in {"%", ")", "%)", ")%"} or merged[-1] in {"$", "(", "$("}):
                merged[-1] = (merged[-1] + c) if merged[-1] in {"$", "(", "$("} else merged[-1] + c
            else:
                merged.append(c)
        if merged:
            rows.append(" | ".join(merged))
    return rows


def html_to_lines(html: str) -> List[str]:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "head", "title"]):
        tag.decompose()
    for tag in soup.find_all(re.compile(r"^ix:header$", re.I)):
        tag.decompose()
    for tag in soup.find_all(style=HIDDEN):
        tag.decompose()
    for tag in soup.find_all(style=PAGE_BREAK):
        tag.insert_before(f"\n{PAGE}\n")
    for tag in soup.find_all("hr"):
        tag.replace_with(f"\n{PAGE}\n")
    for table in soup.find_all("table"):
        if table.find_parent("table") is not None:
            continue
        rows = _table_rows(table)
        if not rows:
            table.decompose()
        elif len(rows) <= 2 and sum(len(r) for r in rows) < 400:   # layout table: a heading or a line
            table.replace_with("\n" + "\n".join(r.replace(" | ", " ") for r in rows) + "\n")
        else:
            table.replace_with("\n" + TABLE + "\n" + "\n".join(rows) + "\n" + END_TABLE + "\n")
    for tag in soup.find_all(BLOCK):
        tag.insert_before("\n")
        tag.insert_after("\n")
    text = soup.get_text("")
    out: List[str] = []
    for line in text.splitlines():
        line = re.sub(r"[ \t ​]+", " ", line).strip()
        if not line or NOISE.match(line):
            continue
        if line == PAGE and out and out[-1] == PAGE:
            continue
        out.append(line)
    return out


def text_to_lines(text: str) -> List[str]:
    """Plain-text documents (some older exhibits)."""
    return [re.sub(r"\s+", " ", ln).strip() for ln in re.split(r"\n\s*\n", text) if ln.strip()]
