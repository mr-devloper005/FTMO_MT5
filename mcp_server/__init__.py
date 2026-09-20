"""MCP server exposing the FTMO_MT5 Lab as tools.

Deliberately imports only from ftmo_mt5.lab / ftmo_mt5.signals -- never from
ftmo_mt5.execution, ftmo_mt5.agents, or ftmo_mt5.main. That import boundary is
the whole security story: nothing in this package can reach a broker, an
order, a credential, or the live trading loop, no matter what a tool call
asks for. See ../FTMO_PROGRAM_PRD.md and ../IMPLEMENTATION_PLAN.md (Phase 1).
"""
