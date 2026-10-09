"""Paper-only systematic trading: research, validate, rank and paper-trade a watchlist.

Pipeline:  watchlist -> screen -> validate -> rank -> paper-trade

It stands entirely on the existing system: the Lab (``ftmo_mt5.lab``) does the
research and grading, the signal library supplies the strategies, the real
``RiskAgent`` approves or refuses every order, and the append-only
``EventStore`` records every decision.

Paper only, structurally. Nothing in this package imports a broker gateway,
``ftmo_mt5.main``, ``broker_hub`` or ``mt5_api_server``, and
``tests/test_systematic.py`` fails the build if that ever changes. Orders go to
``PaperBroker``, an in-process ledger. There is no switch that turns this into
live trading; adding one would mean writing new code, on purpose.
"""

# Read by the CLI and the tests. Changing it does nothing by itself: there is no
# live order path in this package to enable. It exists so the intent is stated
# in code, not only in prose.
LIVE_TRADING_ENABLED = False
