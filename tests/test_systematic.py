"""The paper-only systematic pipeline: safety boundary, paper ledger, ranking, and a full cycle.

Everything here runs offline: candles are synthetic and the risk agent is the
real one. Nothing touches a network or a broker.
"""
import asyncio
import json
import subprocess
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from ftmo_mt5.marketdata.base import Candle
from systematic import LIVE_TRADING_ENABLED
from systematic.config import Settings, build_ftmo_limits, load_ftmo_config
from systematic.data import completed_bars
from systematic.paper_broker import HaltedError, PaperBroker
from systematic.research import rank, robustness_score
from systematic.trader import Cycle, desired_position
from systematic.universe import load_watchlist

REPO = Path(__file__).resolve().parents[1]


def _clean_env():
    """A minimal environment for a clean child Python. Windows cannot even import asyncio without
    SYSTEMROOT (WinError 10106), so its essentials are kept; nothing else (no broker keys) is."""
    env = {"PYTHONPATH": str(REPO)}
    if os.name == "nt":
        keep = {"SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "TEMP", "TMP", "PATH", "PATHEXT", "COMSPEC"}
        env.update({k: v for k, v in os.environ.items() if k.upper() in keep})
    else:
        env["PATH"] = "/usr/bin:/bin"
    return env
DAY = 86400


# ---------------------------------------------------------------- safety
FORBIDDEN = ("ftmo_mt5.execution", "ftmo_mt5.main", "broker_hub", "mt5_api_server",
             "MetaTrader5", "tastytrade", "webull")


def test_live_trading_is_disabled():
    assert LIVE_TRADING_ENABLED is False


def test_package_never_loads_a_broker_or_the_live_loop():
    """Importing every module of the package must not load any order-capable code."""
    code = (
        "import sys, pkgutil, importlib, systematic\n"
        "for m in pkgutil.iter_modules(systematic.__path__):\n"
        "    importlib.import_module('systematic.' + m.name)\n"
        f"bad = sorted(n for n in sys.modules if n.startswith({FORBIDDEN!r}))\n"
        "print(bad)\n"
        "sys.exit(1 if bad else 0)\n"
    )
    r = subprocess.run([sys.executable, "-c", code], cwd=REPO, capture_output=True, text=True,
                       env=_clean_env())
    assert r.returncode == 0, f"order-capable modules were imported: {r.stdout} {r.stderr[-400:]}"


def test_no_source_file_imports_a_broker():
    for path in (REPO / "systematic").glob("*.py"):
        for line in path.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s.startswith(("import ", "from ")):
                assert not any(f in s for f in FORBIDDEN), f"{path.name}: {s}"


# ---------------------------------------------------------------- inputs
def test_watchlist_parsing(tmp_path):
    f = tmp_path / "w.txt"
    f.write_text("# team list\nnvda  # from Priya\n\nMSFT\nNVDA\nBRK-B\n", encoding="utf-8")
    assert load_watchlist(f) == ["NVDA", "MSFT", "BRK-B"]


def test_watchlist_rejects_futures(tmp_path):
    f = tmp_path / "w.txt"
    f.write_text("AAPL\n/NQ\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_watchlist(f)


def test_settings_guard_rails():
    with pytest.raises(ValueError):
        Settings(min_grade="GREAT")
    with pytest.raises(ValueError):
        Settings(min_grade="FAILED")       # a failed rule is never tradeable, even on paper
    with pytest.raises(ValueError):
        Settings(timeframes=["1h"])        # no intraday completed-bar rule yet


def test_incomplete_daily_bar_is_dropped():
    today = datetime(2026, 9, 25, tzinfo=timezone.utc)                 # a Friday
    bars = [Candle(timestamp=int(today.timestamp()) - DAY, open=1, high=1, low=1, close=1, volume=0),
            Candle(timestamp=int(today.timestamp()), open=1, high=1, low=1, close=1, volume=0)]
    during = datetime(2026, 9, 25, 18, 0, tzinfo=timezone.utc)         # 14:00 New York
    after = datetime(2026, 9, 25, 21, 0, tzinfo=timezone.utc)          # 17:00 New York
    assert len(completed_bars(bars, "1d", during)) == 1
    assert len(completed_bars(bars, "1d", after)) == 2


# ---------------------------------------------------------------- paper ledger
def broker(tmp_path, commission="1", slip="1"):
    return PaperBroker(tmp_path / "ledger.json", Decimal("100000"), Decimal(commission), Decimal(slip))


def test_long_round_trip_accounts_for_slippage_and_commission(tmp_path):
    b = broker(tmp_path)
    f = b.open("NVDA", "LONG", Decimal("10"), Decimal("100.00"), "TEST")
    assert f.fill_price == Decimal("100.01")                  # one tick against the buyer
    b.mark({"NVDA": Decimal("110.00")})
    assert b.equity() == Decimal("100000") - Decimal("10") + (Decimal("110.00") - Decimal("100.01")) * 10
    c = b.close("NVDA", Decimal("110.00"), "test")
    assert c.fill_price == Decimal("109.99")                  # one tick against the seller
    assert c.realized_pnl == (Decimal("109.99") - Decimal("100.01")) * 10
    assert b.balance() == Decimal("100000") + c.realized_pnl - Decimal("20")
    assert b.state["trades"][0]["net_pnl"] == "79.80"


def test_short_profits_when_price_falls(tmp_path):
    b = broker(tmp_path, commission="0", slip="0")
    b.open("MU", "SHORT", Decimal("5"), Decimal("50"), "TEST")
    c = b.close("MU", Decimal("40"), "test")
    assert c.realized_pnl == Decimal("50")


def test_ledger_persists_and_halt_blocks_new_risk(tmp_path):
    b = broker(tmp_path)
    b.open("AMD", "LONG", Decimal("3"), Decimal("20"), "TEST")
    b.halt("test halt")
    b.save()
    again = broker(tmp_path)
    assert "AMD" in again.positions() and again.halted["reason"] == "test halt"
    with pytest.raises(HaltedError):
        again.open("MSFT", "LONG", Decimal("1"), Decimal("10"), "TEST")
    again.close("AMD", Decimal("20"), "flatten")               # closing is always allowed
    again.clear_halt("tester")
    assert again.halted is None and again.state["halt_history"][0]["cleared_by"] == "tester"


# ---------------------------------------------------------------- ranking
def cand(symbol, verdict, folds=3, held=60.0, ticks=4.0, oos=5.0, sharpe=1.0, dd=10.0):
    return {"symbol": symbol, "timeframe": "1d", "strategy": f"S_{symbol}_{verdict}",
            "strategy_parameters": {}, "spec": {"strategy": "S"}, "verdict": verdict,
            "foldsWon": folds, "foldsRun": 5, "variationsHeldPct": held, "survivesTicks": ticks,
            "oosVsBHPct": oos, "sharpe": sharpe, "maxDDPct": dd}


def test_rank_is_grade_first_and_promotes_only_at_or_above_the_floor():
    cands = [cand("AAA", "FRAGILE", folds=5, held=100, ticks=8, oos=30, sharpe=3, dd=0),   # great score, weak grade
             cand("BBB", "MODERATE", folds=3),
             cand("CCC", "ROBUST", folds=3),
             cand("CCC", "FAILED"),
             cand("DDD", "FAILED")]
    r = rank(cands, Settings(min_grade="MODERATE"))
    assert [c["symbol"] for c in r["ranked"]] == ["CCC", "BBB", "AAA", "DDD"]
    assert [p["symbol"] for p in r["promoted"]] == ["CCC", "BBB"]
    assert r["exploratory"] is False


def test_lowering_the_floor_is_labelled_exploratory():
    r = rank([cand("AAA", "FRAGILE")], Settings(min_grade="FRAGILE"))
    assert r["exploratory"] is True and r["promoted"][0]["symbol"] == "AAA"


def test_nothing_promoted_when_nothing_qualifies():
    r = rank([cand("AAA", "FAILED"), cand("BBB", "FRAGILE")], Settings())
    assert r["promoted"] == []


def test_benchmark_is_never_a_candidate(monkeypatch):
    from systematic import research
    seen = []
    monkeypatch.setattr(research, "load_completed", lambda *a, **k: series(150))
    monkeypatch.setattr(research.metrics, "measure", lambda sym, tf, spec, c: seen.append(spec["strategy"]) or
                        {"beatsBH": False, "trades": 0, "topTradeShare": 0, "bh": 0, "vsBHPct": 0})
    monkeypatch.setattr(research, "_record", lambda *a, **k: None)
    research.screen(["TST"], Settings())
    assert seen and "BUY_AND_HOLD_BASELINE" not in seen


def test_research_and_paper_charge_the_same_costs():
    from systematic.research import spec_for
    s = Settings(commission_per_unit=0.005, slippage_ticks=2)
    sp = spec_for("DONCHIAN_BREAKOUT", {}, s, 100)
    assert sp["commission"] == 0.005 and sp["slippage_ticks"] == 2.0
    assert s.commission() == Decimal("0.005") and s.slippage() == Decimal("2")
    assert "commission" not in spec_for("DONCHIAN_BREAKOUT", {}, Settings(), 100)   # default: Lab's own


def test_robustness_score_is_bounded():
    assert 0 <= robustness_score(cand("X", "ROBUST", 5, 100, 8, 99, 9, 0)) <= 100
    assert robustness_score(cand("X", "FAILED", 0, 0, 0, -99, -9, 99)) == 0


# ---------------------------------------------------------------- the cycle
def series(n=120, start=100.0, step=0.5):
    t0 = int(datetime(2026, 1, 5, tzinfo=timezone.utc).timestamp())
    out = []
    for i in range(n):
        p = start + i * step
        out.append(Candle(timestamp=t0 + i * DAY, open=p, high=p + 1, low=p - 1, close=p, volume=1000))
    return out


def spec(warmup):
    return {"strategy": "BUY_AND_HOLD_BASELINE", "strategy_parameters": {"warmup_bars": warmup},
            "allow_long": True, "allow_short": True, "position_size": 1.0}


def test_desired_position_joins_only_fresh_signals():
    candles = series()
    fresh = desired_position("TST", spec(len(candles) - 1), candles, Settings())
    assert (fresh.direction, fresh.fresh, fresh.entry_age_bars) == ("LONG", True, 0)
    old = desired_position("TST", spec(1), candles, Settings())
    assert old.direction == "LONG" and old.fresh is False


def make_cycle(tmp_path, candles, monkeypatch):
    settings = Settings(state_dir=tmp_path, max_positions=5)
    cycle = Cycle(settings, load_ftmo_config(), fetch=lambda s, tf, bars, now=None: candles)
    # The pre-midnight flatten depends on the wall clock; keep this test independent of it.
    no_flatten = type("F", (), {"should_flatten": False, "positions_to_close": [], "reason": ""})()
    monkeypatch.setattr(cycle.risk.rule_engine, "check_overnight_safety", lambda **kw: no_flatten)
    return cycle


def ranking_for(warmup):
    return {"generatedAt": datetime.now(timezone.utc).isoformat(), "exploratory": False,
            "promoted": [{"symbol": "TST", "timeframe": "1d", "strategy": "BUY_AND_HOLD_BASELINE",
                          "strategy_parameters": {"warmup_bars": warmup}, "spec": spec(warmup),
                          "verdict": "MODERATE", "score": 50}]}


def test_full_cycle_open_hold_close_through_risk_agent(tmp_path, monkeypatch):
    candles = series()
    rk = ranking_for(len(candles) - 1)

    r1 = asyncio.run(make_cycle(tmp_path, candles, monkeypatch).run(rk))
    opened = [a for a in r1["actions"] if a["kind"] == "OPEN"]
    assert len(opened) == 1 and opened[0]["riskState"] == "NORMAL"
    qty = int(opened[0]["quantity"])
    assert qty == int(Decimal("100000") / 5 // Decimal(str(candles[-1].close)))   # one equal-weight slot

    r2 = asyncio.run(make_cycle(tmp_path, candles, monkeypatch).run(rk))         # nothing changed
    assert r2["actions"] == [] and "TST" in r2["positions"]

    empty = {"generatedAt": rk["generatedAt"], "promoted": []}
    r3 = asyncio.run(make_cycle(tmp_path, candles, monkeypatch).run(empty))       # demoted -> closed
    assert [a["kind"] for a in r3["actions"]] == ["CLOSE"] and r3["positions"] == {}

    events = (tmp_path / "paper" / "events" / "events.jsonl").read_text(encoding="utf-8").splitlines()
    kinds = [json.loads(e)["__type__"] for e in events]
    for k in ("SignalGenerated", "RiskChecked", "OrderFilled", "PositionClosed"):
        assert k in kinds, f"missing {k} in the paper event log"


def test_breach_halts_flattens_and_stays_halted(tmp_path, monkeypatch):
    candles = series()
    rk = ranking_for(len(candles) - 1)
    asyncio.run(make_cycle(tmp_path, candles, monkeypatch).run(rk))              # open TST

    ledger = tmp_path / "paper" / "ledger.json"
    s = json.loads(ledger.read_text(encoding="utf-8"))
    s["realized_pnl"] = "-11000"                   # past the 10% max-loss limit
    s["positions"]["TST"]["last_price"] = s["positions"]["TST"]["avg_price"]
    ledger.write_text(json.dumps(s), encoding="utf-8")

    empty = {"generatedAt": rk["generatedAt"], "promoted": []}
    r = asyncio.run(make_cycle(tmp_path, candles, monkeypatch).run(empty))
    assert r["halted"] is not None and r["positions"] == {}
    assert any(a["kind"] == "HALT" for a in r["actions"])

    with pytest.raises(HaltedError):                # a halt never clears itself
        asyncio.run(make_cycle(tmp_path, candles, monkeypatch).run(rk))


def test_limits_match_the_live_loop():
    cfg = load_ftmo_config()
    lim = build_ftmo_limits(cfg, Decimal("100000"))
    assert lim.mdl_limit == Decimal("5000") and lim.ml_limit == Decimal("10000")
