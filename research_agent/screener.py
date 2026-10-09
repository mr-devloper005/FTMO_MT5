"""Parallel, incremental NASDAQ screening.

Each stock is measured on its last year of completed daily bars:

    price · median daily $ volume (60 sessions) · ATR % (14) · history length
    · trend (close vs 200-day SMA) · 1-year return · 95th-percentile overnight gap

and passes when it is liquid, priced above the floor, moves enough to pay costs
but not so much it is unholdable, and has enough history to validate a strategy
out-of-sample. Passers are scored (liquidity 45%, volatility fit 35%, gap safety
20%) and the best become the shortlist.

Parallel: a process pool, one stock per task, so data fetching and maths both
spread across cores. Workers compute; the parent alone writes to the store.

Incremental: a stock screened today is skipped unless forced. Bars come from a
per-day cache, so the first pass of a day fetches and every later pass that day
is free. ``python -m research_agent watch`` repeats this on a timer.
"""
from __future__ import annotations

import math
import statistics as st
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Sequence

from research_agent.config import SCREEN_TIMEFRAME, Settings
from research_agent.store import Store, now

Progress = Optional[Callable[[str], None]]


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def measure_candles(symbol: str, candles: Sequence, settings: Settings) -> Dict:
    """Screening metrics and the pass/fail decision for one stock. Pure: no I/O."""
    row: Dict = {"symbol": symbol, "history_bars": len(candles)}
    if len(candles) < 60:
        row.update(passed=0, reason=f"only {len(candles)} bars of history")
        return row
    w = list(candles[-settings.screen_window:])
    closes = [float(c.close) for c in w]
    last = closes[-1]
    trs = [max(float(w[i].high) - float(w[i].low), abs(float(w[i].high) - closes[i - 1]),
               abs(float(w[i].low) - closes[i - 1])) for i in range(1, len(w))]
    gaps = sorted(abs(float(w[i].open) / closes[i - 1] - 1) * 100 for i in range(1, len(w)) if closes[i - 1])
    dollar = [float(c.close) * float(c.volume) for c in w[-60:] if c.volume]
    all_closes = [float(c.close) for c in candles]
    sma200 = st.fmean(all_closes[-200:]) if len(all_closes) >= 200 else None

    row.update(
        as_of=datetime.fromtimestamp(w[-1].timestamp, timezone.utc).date().isoformat(),
        price=round(last, 4),
        dollar_volume=round(st.median(dollar), 2) if dollar else 0.0,
        atr_pct=round(st.fmean(trs[-14:]) / last * 100, 3) if last else None,
        trend_up=int(sma200 is not None and last > sma200),
        return_1y=round((last / closes[0] - 1) * 100, 2) if closes[0] else None,
        gap_p95=round(gaps[int(len(gaps) * 0.95)], 3) if gaps else None,
    )

    fails = []
    if len(candles) < settings.min_history_bars:
        fails.append(f"history {len(candles)} bars < {settings.min_history_bars}")
    if last < settings.min_price:
        fails.append(f"price ${last:.2f} < ${settings.min_price:.0f}")
    if row["dollar_volume"] < settings.min_dollar_volume:
        fails.append(f"liquidity ${row['dollar_volume'] / 1e6:.1f}M/day < ${settings.min_dollar_volume / 1e6:.0f}M")
    atr = row["atr_pct"] or 0.0
    if not settings.min_atr_pct <= atr <= settings.max_atr_pct:
        fails.append(f"ATR {atr:.2f}% outside {settings.min_atr_pct}–{settings.max_atr_pct}%")
    row["passed"] = int(not fails)
    row["reason"] = "; ".join(fails) if fails else "liquid, tradable range, enough history"
    row["score"] = score(row, settings) if not fails else 0.0
    return row


def score(row: Dict, settings: Settings) -> float:
    """0–100: liquidity first, then a volatility sweet spot, then overnight-gap safety."""
    lo, hi = math.log10(settings.min_dollar_volume), math.log10(20e9)
    liq = _clamp((math.log10(max(row["dollar_volume"], 1.0)) - lo) / (hi - lo))
    a = row["atr_pct"] or 0.0
    vol_fit = 1.0 if 1.8 <= a <= 4.0 else (_clamp((a - 1.0) / 0.8) if a < 1.8 else _clamp(1 - (a - 4.0) / 4.0))
    gap = _clamp(1 - ((row["gap_p95"] or 0.0) / max(a, 0.01) - 0.6) / 1.4)
    return round(100 * (0.45 * liq + 0.35 * vol_fit + 0.20 * gap), 2)


# ---------------------------------------------------------------- workers
def _init_worker(state_dir: str) -> None:
    """Each process points the Lab's loader at today's cache folder."""
    from pathlib import Path
    from systematic.data import use_daily_cache
    use_daily_cache(Path(state_dir))


def _screen_one(symbol: str, settings_dict: Dict) -> Dict:
    from systematic.data import load_completed
    settings = Settings.from_dict(settings_dict)
    last_err = None
    for attempt in range(3):
        try:
            candles = load_completed(symbol, SCREEN_TIMEFRAME)
            row = measure_candles(symbol, candles, settings)
            row["error"] = None
            return row
        except Exception as e:                                   # noqa: BLE001
            last_err = f"{type(e).__name__}: {str(e)[:160]}"
            if "no 1d data" in str(e) or "DataUnavailable" in type(e).__name__:
                break                                            # delisted / unknown: retrying won't help
            time.sleep(1.5 * (attempt + 1))                      # rate limit or a network blip
    return {"symbol": symbol, "passed": 0, "error": last_err, "reason": "data unavailable"}


# ---------------------------------------------------------------- runner
def run(settings: Settings, symbols: Optional[List[str]] = None, limit: int = 0,
        force: bool = False, progress: Progress = None) -> Dict:
    """Screen what needs screening, in parallel. Returns counts and timing."""
    from systematic.data import NEW_YORK, use_daily_cache
    settings.ensure_dirs()
    store = Store(settings.db_path)
    use_daily_cache(settings.state_dir)
    today = datetime.now(NEW_YORK).date().isoformat()

    pool = symbols or store.active_symbols()
    done_today = set() if force else store.screened_on(datetime.now(timezone.utc).date().isoformat())
    todo = [s for s in pool if s not in done_today]
    skipped = len(pool) - len(todo)
    if limit:
        todo = todo[:limit]
    if not todo:
        return {"screened": 0, "skipped_fresh": skipped, "remaining": 0, "seconds": 0.0, "day": today}

    t0, passed, errors, n = time.time(), 0, 0, 0
    with ProcessPoolExecutor(max_workers=settings.workers, initializer=_init_worker,
                             initargs=(str(settings.state_dir),)) as ex:
        futures = {ex.submit(_screen_one, s, settings.to_dict()): s for s in todo}
        for fut in as_completed(futures):
            row = fut.result()
            row["screened_at"] = now()
            store.save_screen(row)
            n += 1
            passed += int(bool(row.get("passed")))
            errors += int(bool(row.get("error")))
            if progress and (n % 100 == 0 or n == len(todo)):
                progress(f"screened {n}/{len(todo)} · passed {passed} · data errors {errors}")
    remaining = len([s for s in pool if s not in done_today]) - len(todo)
    return {"screened": n, "passed": passed, "errors": errors, "skipped_fresh": skipped,
            "remaining": remaining, "seconds": round(time.time() - t0, 1), "day": today}


def shortlist(settings: Settings, limit: Optional[int] = None) -> List[Dict]:
    return Store(settings.db_path).shortlist(limit or settings.shortlist_size)
