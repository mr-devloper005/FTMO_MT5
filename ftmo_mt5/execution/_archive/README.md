# Archived gateways

Working code that is no longer wired into the running system.

- `ib_gateway.py` — Interactive Brokers via `ib_async`. Removed from the broker
  hub on 2026-08-13 because IB requires full KYC plus a funded account before
  it will serve real-time data. It was never verified against a live IB
  session. To bring it back: move the file up one level, re-add the import and
  the `"ib"` entry in `broker_hub.py`, and restore the `IB_*` keys in `.env`.
