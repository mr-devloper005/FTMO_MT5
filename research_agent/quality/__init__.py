"""Quality engine: Rahul's five-lens stock scoring, fed by FMP.

    lenses.py   his lens + rationale functions, ported verbatim (owner: Rahul)
    peers.py    his hand-picked peer sets (tickers only)
    inputs.py   FMP responses -> the input shapes the lenses expect
    model.py    his composite score (missing lenses renormalised) and buckets
    mcs.py      Management lens: MCS v2 from a sourced guidance ledger (ledgers/<SYMBOL>.json)
    mcs_extract.py  draft ledger guidance items parsed from an earnings-call transcript

Phase 1 = Fundamentals, Valuation, Technicals. Management is scored only for stocks
with a ledger (others stay missing and are renormalised away, never defaulted to 50).
Options needs a data source with option chains (not in FMP; the current Massive plan
has no chain snapshots or open interest).
"""
