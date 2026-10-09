"""The team's watchlist: which symbols the pipeline researches.

One symbol per line. Blank lines and ``#`` comments are ignored, and anything
after a symbol on the same line is treated as a note (``AAPL  # from Priya``).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import List

from systematic.config import PACKAGE

DEFAULT_WATCHLIST = PACKAGE / "watchlist.txt"

# Yahoo-style tickers: letters, digits, dot and dash (BRK-B, RELIANCE.NS).
# Futures ("/NQ") are refused: the paper broker sizes positions as shares.
# The cap is 20: an exchange suffix costs 3-4 characters, so a 10-char limit
# rejected every Indian ticker (RELIANCE.NS is 11) despite the docs promising it.
_TICKER = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,19}$")


def load_watchlist(path: Path = DEFAULT_WATCHLIST) -> List[str]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Watchlist not found: {p}")
    symbols: List[str] = []
    bad: List[str] = []
    for line in p.read_text().splitlines():
        token = line.split("#", 1)[0].strip().split()
        if not token:
            continue
        sym = token[0].upper()
        if not _TICKER.match(sym):
            bad.append(sym)
        elif sym not in symbols:
            symbols.append(sym)
    if bad:
        raise ValueError(f"Not valid stock tickers: {bad}. One ticker per line, e.g. AAPL or BRK-B.")
    if not symbols:
        raise ValueError(f"Watchlist {p} has no symbols.")
    return symbols
