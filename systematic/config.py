"""Settings for the systematic pipeline, and the FTMO rules it trades under.

FTMO limits come from ``config/default.yaml`` — the same file the live loop
reads — so the paper account is held to the challenge it is practising for.
This module deliberately does not call ``ftmo_mt5.main.load_config``: importing
``main`` imports the live broker gateways, and this package must never do that.
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields
from decimal import Decimal
from pathlib import Path
from typing import List, Optional

import yaml

from ftmo_mt5.core.models import FTMOConfig, FTMOLimits

REPO = Path(__file__).resolve().parents[1]
PACKAGE = Path(__file__).resolve().parent
DEFAULT_SETTINGS = PACKAGE / "settings.yaml"
DEFAULT_STATE = PACKAGE / "state"
FTMO_YAML = REPO / "config" / "default.yaml"

GRADES = ["FAILED", "FRAGILE", "MODERATE", "ROBUST"]   # weakest -> strongest
PROMOTION_FLOORS = ["FRAGILE", "MODERATE", "ROBUST"]   # FAILED is never tradeable, even on paper
PAPER_TIMEFRAMES = {"1d"}                              # see trader.completed_bars


@dataclass
class Settings:
    """Everything the pipeline can be told. Defaults are the Lab's canonical run."""

    # research
    timeframes: List[str] = field(default_factory=lambda: ["1d"])
    bars: int = 600                      # Lab canonical window
    allow_short: bool = True             # Lab canonical
    candidates_per_symbol: int = 3       # how many screened rules per symbol go to validation
    min_trades: int = 10                 # below this a result is noise (CLAUDE.md research rules)
    max_top_trade_share: float = 50.0    # one trade carrying >= half the profit is not a system

    # promotion
    min_grade: str = "MODERATE"          # ROBUST or MODERATE only, unless deliberately lowered
    max_positions: int = 5               # config/default.yaml ftmo.max_concurrent_positions

    # paper execution
    max_entry_age_bars: int = 1          # only enter on a fresh signal; don't join old trades
    max_gross_exposure: float = 1.0      # gross notional may not exceed this x equity
    margin_rate: float = 1.0             # cash-like account: every dollar of exposure is margin
    commission_per_unit: Optional[float] = None   # None = the Lab's own cost (per share per side)
    slippage_ticks: Optional[float] = None        # None = the Lab's own slippage

    state_dir: Path = DEFAULT_STATE

    def __post_init__(self) -> None:
        if self.min_grade not in PROMOTION_FLOORS:
            raise ValueError(f"min_grade must be one of {PROMOTION_FLOORS}, got {self.min_grade!r}")
        bad = [t for t in self.timeframes if t not in PAPER_TIMEFRAMES]
        if bad:
            raise ValueError(
                f"Paper trading supports {sorted(PAPER_TIMEFRAMES)} only (got {bad}). "
                "Intraday bars need an intraday completed-bar rule before they can be traded.")
        self.state_dir = Path(self.state_dir)

    # Costs default to the Lab's, so a paper result is comparable to the
    # validation that promoted it.
    def commission(self) -> Decimal:
        from ftmo_mt5.lab import metrics
        v = metrics.COMMISSION if self.commission_per_unit is None else self.commission_per_unit
        return Decimal(str(v))

    def slippage(self) -> Decimal:
        from ftmo_mt5.lab import metrics
        v = metrics.SLIPPAGE_TICKS if self.slippage_ticks is None else self.slippage_ticks
        return Decimal(str(v))


def load_settings(path: Optional[Path] = None, **overrides) -> Settings:
    data = {}
    p = Path(path) if path else DEFAULT_SETTINGS
    if p.exists():
        data = yaml.safe_load(p.read_text()) or {}
    known = {f.name for f in fields(Settings)}
    unknown = sorted(set(data) - known)
    if unknown:
        raise ValueError(f"Unknown settings in {p.name}: {unknown}")
    data.update({k: v for k, v in overrides.items() if v is not None})
    return Settings(**data)


def load_ftmo_config(path: Path = FTMO_YAML) -> FTMOConfig:
    raw = (yaml.safe_load(Path(path).read_text()) or {}).get("ftmo", {})
    return FTMOConfig(**{k: v for k, v in raw.items() if k in FTMOConfig.model_fields})


def build_ftmo_limits(ftmo_config: FTMOConfig, balance: Decimal) -> FTMOLimits:
    """Same limits the live loop builds (ftmo_mt5.main.build_ftmo_limits).

    Copied rather than imported: importing ``main`` pulls in the live broker
    gateways. Keep the two in step if either changes.
    """
    capital = Decimal(str(ftmo_config.initial_capital))
    return FTMOLimits(
        mdl_limit=capital * Decimal(str(ftmo_config.mdl_pct)) / Decimal("100"),
        ml_limit=capital * Decimal(str(ftmo_config.ml_pct)) / Decimal("100"),
        balance_at_midnight=balance,
        highest_eod_balance=max(balance, capital),
        challenge_type=ftmo_config.challenge_type,
    )
