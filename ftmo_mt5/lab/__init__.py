"""Backtest Lab — the research loop as a tool instead of a conversation.

`windows` chooses and shares the data, `metrics` measures one run, `verdict`
grades the evidence, `service` turns a form submission into a set of runs, and
`store` keeps them so a question answered once can be re-read instead of re-run.
"""
from ftmo_mt5.lab import metrics, service, store, verdict, windows

__all__ = ["metrics", "service", "store", "verdict", "windows"]
