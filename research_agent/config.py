"""Settings for the research agent: one dataclass, research defaults.

Costs default to realistic US-stock costs ($0.005/share + 1 tick), not the Lab's
futures-calibrated $2.50 per unit — charged per share, that figure ate 70% of
gross profit in an earlier replay (systematic/FINDINGS.md). Research and every
recorded result use the same costs, so a ranking compares like with like.
"""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

PACKAGE = Path(__file__).resolve().parent
REPO = PACKAGE.parent
DEFAULT_STATE = Path(os.getenv("RESEARCH_AGENT_STATE", PACKAGE / "state"))
TEAM_STRATEGIES = PACKAGE / "strategies" / "team"

NASDAQ_LISTING_URL = "https://www.nasdaqtrader.com/dynamic/symdir/nasdaqlisted.txt"

# ---------------------------------------------------------------------------
# THE 10 TIMEFRAMES — the one place they are defined.
# Every strategy is backtested on every stock on each of these. Everything else
# (backtester, agent tools and prompt, leaderboard, dashboard, Excel, Google
# Sheet, report) reads this tuple; a test fails if a timeframe literal appears
# anywhere else in research_agent/.
#
# Chosen 2026-10-03 ("stock-adjusted set"): the Lab's 11 minus 12h, because on
# US stocks (6.5-hour sessions) the 12h bar is the daily bar again (483 of 498
# AAPL bars closed exactly at the daily close) with only 2 years of history.
# Every entry must be a timeframe the Lab supports (ftmo_mt5/lab/windows.py).
# ---------------------------------------------------------------------------
TIMEFRAMES: Tuple[str, ...] = ("5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "1d", "1w")

TIMEFRAME_LABELS: Dict[str, str] = {
    "5m": "5 minutes", "15m": "15 minutes", "30m": "30 minutes", "1h": "1 hour", "2h": "2 hours",
    "4h": "4 hours", "6h": "6 hours", "8h": "8 hours", "1d": "1 day", "1w": "1 week",
}

# History per timeframe: daily keeps `Settings.bars` (~10 years, so results stay
# comparable with every earlier run); every other timeframe uses all the history
# Yahoo serves (5m/15m/30m ~60 days, 1h-8h ~2 years, 1w ~26 years).
DAILY = "1d"

# Screening (liquidity, price, ATR, history) is a property of the stock, not of
# a strategy, so it stays on daily bars.
SCREEN_TIMEFRAME = DAILY

GRADES = ["FAILED", "FRAGILE", "MODERATE", "ROBUST"]          # weakest -> strongest
VERDICTS = ["Rejected", "Watch", "Recommended"]               # weakest -> strongest


@dataclass
class Settings:
    state_dir: Path = DEFAULT_STATE

    # parallelism
    workers: int = 8

    # data
    timeframes: Tuple[str, ...] = TIMEFRAMES   # see TIMEFRAMES above; override only in tests
    bars: int = 2500                  # daily bars for backtests (~10 years); other timeframes use all history
    screen_window: int = 252          # one year of bars for screening metrics
    min_history_bars: int = 1000      # ~4 years: enough to validate out-of-sample

    # screening filters
    min_price: float = 5.0
    min_dollar_volume: float = 20e6   # median daily $ volume over 60 sessions
    min_atr_pct: float = 1.0          # too quiet to pay costs below this
    max_atr_pct: float = 8.0          # too violent to hold above this
    shortlist_size: int = 25

    # backtest costs (per share, per side) and options
    commission_per_unit: float = 0.005
    slippage_ticks: float = 1.0
    allow_short: bool = True

    # research flow
    validate_top_n: int = 3           # best screen-phase strategies per stock that get the full robustness test
    min_trades: int = 10

    # verdict policy
    target_outperformance_pts: float = 50.0   # strategy return − Buy & Hold return, in percentage points
    promote_grade: str = "MODERATE"            # robustness floor for Recommended

    def __post_init__(self) -> None:
        self.state_dir = Path(self.state_dir)
        if self.promote_grade not in GRADES[1:]:
            raise ValueError(f"promote_grade must be one of {GRADES[1:]}")
        from ftmo_mt5.lab import windows
        self.timeframes = tuple(self.timeframes)
        unknown = [tf for tf in self.timeframes if tf not in windows.TIMEFRAMES]
        if unknown or not self.timeframes or len(set(self.timeframes)) != len(self.timeframes):
            raise ValueError(f"timeframes must be distinct Lab timeframes {windows.TIMEFRAMES}; got {self.timeframes}")

    # ---- paths -----------------------------------------------------------
    @property
    def db_path(self) -> Path:
        return self.state_dir / "research.db"

    @property
    def reports_dir(self) -> Path:
        return self.state_dir / "reports"

    @property
    def exports_dir(self) -> Path:
        return self.state_dir / "exports"

    @property
    def runs_dir(self) -> Path:
        return self.state_dir / "runs"

    @property
    def strategy_log(self) -> Path:
        return self.state_dir / "strategy_log.jsonl"

    def history_bars(self, timeframe: str) -> Optional[int]:
        """Bars to backtest on: daily is capped at `bars`, every other timeframe uses all history."""
        return self.bars if timeframe == DAILY else None

    def ensure_dirs(self) -> None:
        for d in (self.state_dir, self.reports_dir, self.exports_dir, self.runs_dir):
            d.mkdir(parents=True, exist_ok=True)

    # ---- process boundaries ---------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["state_dir"] = str(self.state_dir)
        d["timeframes"] = list(self.timeframes)
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Settings":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})

    def cost_key(self) -> str:
        """Part of every test's identity: the same rule under other costs is another test."""
        return f"c{self.commission_per_unit}_s{self.slippage_ticks}_short{int(self.allow_short)}"
