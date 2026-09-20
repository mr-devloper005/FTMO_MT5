# Archived UIs

Removed from the running system on 2026-08-14 so exactly one interface exists.

- `trading_dashboard.html` — an older build of the terminal. Nothing referenced it.
- `broker_panel.html` — the compact multi-broker overview, once served at
  `:8600/panel`. Everything it showed (both brokers, combined view, search) is
  in the terminal itself now.

The single surviving UI is `trading_dashboard_live.html`, served by
`broker_hub.py` at http://localhost:8600 — nowhere else.

## src-nehanth/ (was `src/nehanth/`)

An older copy of the whole trading package, archived 2026-08-14. `ftmo_mt5/` is
the code that actually runs; this copy still carried both critical bugs fixed on
2026-08-13 — the `start_as_current_span` span crash and the risk engine's
permanent RISK_OFF — and had neither `market_data_loop` nor any strategy, so it
could not trade at all.

It was dangerous precisely because it looked authoritative: the test suite
imported from it, so anyone reading or "fixing" it would be working on code no
process ever loads. Tests now import `ftmo_mt5`.
