"""Financial Modeling Prep (FMP) collector for the research RAG.

client.py     HTTP client (header auth, rate limit, retries, typed errors, key redaction)
normalize.py  FMP JSON -> typed rows (statements, earnings, company data) and RAG documents
repo.py       PostgreSQL upserts / queries for the structured tables + fetch log
ingest.py     per-symbol orchestration with duplicate checks and incremental updates

Research data only: nothing here can reach a broker or place an order.
"""
