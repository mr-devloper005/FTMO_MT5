"""Independent AI trading research agent — research only.

Three jobs, in order:

    1. screen the NASDAQ universe, in parallel and incrementally;
    2. select existing strategies and propose new ones under a fixed schema;
    3. backtest every promising stock + strategy pair against Buy & Hold.

It reuses the Lab's engine (``ftmo_mt5.lab``), strategy library
(``ftmo_mt5.signals``) and results index (``mcp_server.results_index``). It never
imports a broker gateway, ``ftmo_mt5.main``, ``ftmo_mt5.agents``, ``broker_hub``
or ``mt5_api_server``, and ``tests/test_research_agent.py`` fails if it ever does.
There is no order path to switch on.
"""

RESEARCH_ONLY = True
ORDER_EXECUTION_ENABLED = False
