"""Grounded answers. Claude sees only the evidence we retrieved, has NO tools (no
shell, files, web, MCP or broker), must cite evidence as [n], and must reply
exactly "Not found in the available data." when the evidence does not answer
the question.

Evidence = structured FMP rows (statements, earnings, estimates) and/or document
chunks (filing notes, profiles, SEC filings), chosen by ``structured.route``.
After the answer: the number guard checks every figure against the evidence,
and the fact check compares metric figures with the database values; a
mismatch is flagged and the database value appended (the LLM cannot override it)."""
from __future__ import annotations

import asyncio
import re
import time
from typing import Any, Dict, List, Optional, Sequence

from rag import guard, structured
from rag.config import ANSWER_MODEL, NOT_FOUND, NOT_FOUND_LEGACY, Settings, period_label
from rag.retrieve import MANAGEMENT, cite, management_first, search
from rag.store import Store

SYSTEM = f"""You answer questions about public companies using ONLY the numbered evidence in the user message.
Evidence is either structured financial data from Financial Modeling Prep (FMP) or excerpts from company documents.

Rules:
1. Every factual sentence ends with the evidence number(s) it comes from, like [2] or [1][3].
2. Copy financial figures exactly as the evidence states them (you may convert units, e.g. 54,229,000,000 -> $54.2 billion). Never use outside knowledge, never estimate, never fill gaps.
3. Figures in FMP structured data are authoritative: never adjust, round differently or contradict them.
4. If you derive a figure (difference, growth rate, beat/miss), put "(computed from [a] and [b])" IMMEDIATELY after
   EACH derived figure, e.g. "up $7.1 billion (computed from [1] and [2]), or 8.6% (computed from [1] and [2])".
5. Say which fiscal period each figure belongs to. Fiscal years differ from calendar years for some companies.
6. Analyst consensus estimates are NOT company guidance. Never present them as what management said or guided.
7. If the evidence does not contain the answer, reply with exactly: {NOT_FOUND}
   If it answers only part of the question, answer that part and say which part was not found.
8. Be concise: at most 8 sentences or a short list. No preamble."""


def build_context(items: List[Dict[str, Any]]) -> str:
    """Numbered evidence: structured fact blocks first, then document chunks."""
    blocks = []
    for n, h in enumerate(items, 1):
        if h.get("kind") == "fact":
            blocks.append(f"[{n}] {h['source']}\n{h['text']}")
            continue
        sec = h["section"] + (f" > {h['subsection']}" if h.get("subsection") else "")
        page = f" | page {h['page']}" if h.get("page") is not None else ""
        blocks.append(f"[{n}] {h['company']} ({h['symbol']}) | {h['document_type']} | "
                      f"{period_label(h['fiscal_year'], h['fiscal_quarter'])} | filed {h['filing_date']} | "
                      f"{sec}{page}\n{h['text']}")
    return "\n\n".join(blocks)


def source_of(item: Dict[str, Any]) -> str:
    return f"{item['source']} - {item['source_url']}" if item.get("kind") == "fact" else cite(item)


async def _claude(system: str, prompt: str, model: str) -> Dict[str, Any]:
    from claude_agent_sdk import (AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient, PermissionResultDeny,
                                  ResultMessage, TextBlock)

    async def deny_all(name, tool_input, context):                 # belt and braces: there are no tools
        return PermissionResultDeny(message="This research assistant has no tools.", interrupt=True)

    options = ClaudeAgentOptions(model=model, system_prompt=system, tools=[], allowed_tools=[],
                                 mcp_servers={}, strict_mcp_config=True, setting_sources=[],
                                 can_use_tool=deny_all, max_turns=1, permission_mode="default")
    texts, res = [], {}
    async with ClaudeSDKClient(options=options) as client:
        await client.query(prompt)
        async for msg in client.receive_response():
            if isinstance(msg, AssistantMessage):
                texts += [b.text for b in msg.content if isinstance(b, TextBlock)]
            elif isinstance(msg, ResultMessage):
                res = {"cost_usd": msg.total_cost_usd, "is_error": msg.is_error, "usage": msg.usage}
    return {"text": "\n".join(t for t in texts if t.strip()).strip(), **res}


def claude(system: str, prompt: str, model: str = ANSWER_MODEL) -> Dict[str, Any]:
    t0 = time.time()
    out = asyncio.run(_claude(system, prompt, model))
    out["seconds"] = round(time.time() - t0, 1)
    return out


def is_not_found(text: str) -> bool:
    t = text.strip().rstrip(".").lower()
    return any(t.startswith(p.rstrip(".").lower()) for p in (NOT_FOUND, NOT_FOUND_LEGACY))


def cited_numbers(text: str, n_hits: int) -> List[int]:
    nums = {int(x) for grp in re.findall(r"\[(\d+(?:\s*[,–-]\s*\d+)*)\]", text)
            for x in re.findall(r"\d+", grp)}
    return sorted(n for n in nums if 1 <= n <= n_hits)


def ask(question: str, settings: Optional[Settings] = None, symbol: Optional[str] = None,
        doc_types: Optional[Sequence[str]] = None, fiscal_year: Optional[int] = None,
        fiscal_quarter: Optional[str] = None, k: int = 8, model: str = ANSWER_MODEL,
        auto_period: bool = True, route: Optional["structured.Route"] = None) -> Dict[str, Any]:
    t0 = time.time()
    settings = settings or Settings()
    rt = route or structured.route(question)
    with Store(settings.pg_config()) as store:
        facts = (structured.evidence(store.db, symbol, question, rt, fiscal_year, fiscal_quarter)
                 if rt.structured and symbol else [])
        # numeric questions fall back to documents when the database has no matching rows
        kd = k if not facts else max(4, k - len(facts))
        if not (rt.documents or not facts):
            found = {"hits": [], "filters": {}, "ms": 0}
        elif MANAGEMENT.search(question) and not doc_types:
            found = management_first(question, settings, symbol, k=kd, store=store, fiscal_year=fiscal_year,
                                     fiscal_quarter=fiscal_quarter, auto_period=auto_period)
        else:
            found = search(question, settings, symbol, doc_types, fiscal_year, fiscal_quarter, kd, auto_period,
                           store=store)
    items = facts + found["hits"]
    base = {"question": question, "route": {"structured": rt.structured, "documents": rt.documents,
                                            "reason": rt.reason},
            "facts": facts, "hits": found["hits"], "filters": found["filters"], "retrieval_ms": found["ms"]}
    if not items:
        return {**base, "answer": NOT_FOUND, "not_found": True, "citations": [], "grounded": True,
                "guard": {"verified": [], "unverified": [], "computed": []},
                "factcheck": {"checked": 0, "derived_skipped": 0, "derived_verified": 0, "doc_supported": 0, "conflicts": []}, "cost_usd": 0.0,
                "seconds": round(time.time() - t0, 1), "note": "no evidence matches the question / filters"}
    prompt = f"Question: {question}\n\nEvidence:\n\n{build_context(items)}"
    out = claude(SYSTEM, prompt, model)
    text = out["text"] or NOT_FOUND
    cited = cited_numbers(text, len(items))
    check = guard.check(text, [i["text"] for i in items])
    fc = structured.factcheck(text, facts, [h["text"] for h in found["hits"]])
    if fc["conflicts"]:
        text = text.rstrip() + "\n\n" + structured.correction_note(fc["conflicts"])
    return {**base, "answer": text, "not_found": is_not_found(text),
            "citations": [{"n": n, "id": items[n - 1].get("chunk_id") or items[n - 1]["source"],
                           "kind": items[n - 1].get("kind", "document"), "source": source_of(items[n - 1])}
                          for n in cited],
            "guard": check, "factcheck": fc, "grounded": not check["unverified"] and not fc["conflicts"],
            "cost_usd": out.get("cost_usd"), "llm_seconds": out["seconds"], "seconds": round(time.time() - t0, 1)}
