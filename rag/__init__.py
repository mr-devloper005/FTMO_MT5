"""Company-knowledge RAG proof of concept (research only).

SEC filings (10-K, 10-Q, 8-K earnings releases) -> local raw files -> text ->
section-aware chunks -> local embeddings -> PostgreSQL + pgvector (vectors + full text) -> hybrid
retrieval with symbol / document-type / fiscal-period filters -> Claude answer
that must cite its excerpts.

Deliberately isolated from the FTMO trading system: nothing in this package
imports ``ftmo_mt5``, ``research_agent``, ``broker_hub`` or any broker module,
and the answering model is given no tools at all. tests/test_rag.py enforces it.
"""
