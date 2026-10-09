"""Rahul's composite score and priority buckets (dashboard_build/scoring.py @ 3b81a2a).

Weighted average over the lenses that are PRESENT; a missing lens is dropped and
the remaining weights rescaled to sum to 1 — exactly as in his dashboard, so a
stock is never penalised as if a missing lens scored zero.
"""
from __future__ import annotations

from typing import Dict, Optional

WEIGHTS: Dict[str, float] = {"fundamentals": 0.35, "technicals": 0.35, "management": 0.15,
                             "options": 0.10, "valuation": 0.05}
BUCKETS = [("High", 85.0), ("Medium", 70.0), ("Low", 55.0), ("Watchlist", 40.0)]   # below 40: Avoid


def composite(scores: Dict[str, Optional[float]]) -> Optional[float]:
    pairs = [(s, WEIGHTS[k]) for k, s in scores.items() if s is not None and k in WEIGHTS]
    if not pairs:
        return None
    total = sum(w for _, w in pairs)
    return round(max(0.0, min(100.0, sum(s * w for s, w in pairs) / total)), 2)


def bucket(score: Optional[float]) -> Optional[str]:
    if score is None:
        return None
    for name, floor in BUCKETS:
        if score >= floor:
            return name
    return "Avoid"
