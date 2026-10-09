---
name: add-broker
description: Add a new broker gateway (e.g. Dhan, Zerodha, Upstox) to the FTMO_MT5 broker hub. Use when asked to connect, integrate, or add a broker or trading account to the terminal.
---

# Add a broker

Three gateways already follow this pattern: `remote_gateway.py` (MT5), `tastytrade_gateway.py`, `webull_gateway.py`. Copy the closest one.

## Steps

1. **New file** `ftmo_mt5/execution/<broker>_gateway.py` implementing `BrokerGateway` from `ftmo_mt5/execution/broker_base.py` — all 10 methods:
   `initialize`, `shutdown`, `health_check`, `describe`, `get_account_info`, `get_positions`, `get_tick`, `get_ohlcv`, `order_send`, `close_position`.
2. **Register** in `broker_hub.py`'s `BROKERS` dict:
   `"<key>": Broker("<key>", MyGateway(), autoconnect=os.getenv("<KEY>_AUTOCONNECT", "0") == "1"),`
   That one line exposes every `/api/<key>/...` route.
3. **Credentials** go in `.env` as `<BROKER>_*` variables, read with `os.getenv`. Add the names (no values) to `.env.example`.
4. **Watchlist:** set a `default_symbols` attribute on the gateway if the broker's instruments differ from the default forex list.
5. **Research data:** only call `register_gateway_provider(...)` if the broker's data API is actually available (Dhan's is paid). Yahoo already covers most symbols free.

## Safety — non-negotiable

- **Read-only first.** Ship the gateway with `order_send` and `close_position` raising `NotImplementedError`. Enable them only after the user confirms the sandbox works and any IP whitelisting is done.
- `autoconnect` defaults to `"0"`.
- **Never** read, print, or write credential values. The user pastes them into `.env`.
- **Paper trades are permitted** — but only after proving the account is simulated. The broker must self-report demo/sandbox in `/api/brokers`, and `.env` must confirm it (`TT_SANDBOX=1`, `MT5_SERVER=FTMO-Demo`, sandbox endpoint). If you cannot prove it, do not send an order. Never place an order against an account whose mode you haven't checked this session.
- Prices and quantities are `Decimal`; convert to float only at the JSON boundary (`_num` in `broker_hub.py`).
- `health_check` must never raise.
- `mcp_server/` must never import the new gateway. The Lab MCP server imports only `ftmo_mt5.lab` and `ftmo_mt5.signals` — keep it that way.

## Broker notes

- **Dhan:** `pip install dhanhq`; `DhanContext(client_id, access_token)` → `dhanhq(ctx)`. Access token lasts 24h; API key + secret lasts 12 months. Static IP whitelist is mandatory for order placement. Trading API free, Data API paid.

## Done means

Hub starts, `/api/brokers` lists the new key, `/api/<key>/account` and `/api/<key>/positions` return real data from the sandbox, existing tests still pass, and no order path is enabled yet.
