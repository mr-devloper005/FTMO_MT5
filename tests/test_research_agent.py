"""Research agent: universe, screening, strategy schema, backtests, ranking, safety.

Everything here runs offline on synthetic data; the live NASDAQ/Yahoo path is
exercised by the end-to-end demo, not by unit tests.
"""
from __future__ import annotations

import math
import subprocess
import os
import sys
from pathlib import Path

import pytest

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
sys.path.insert(0, str(REPO))

from research_agent import config, universe                        # noqa: E402
from research_agent.store import Store                             # noqa: E402

LISTING = """Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares
AAPL|Apple Inc. - Common Stock|Q|N|N|100|N|N
QQQ|Invesco QQQ Trust, Series 1|G|N|N|100|Y|N
ZZZT|Test Issue Corp - Common Stock|Q|Y|N|100|N|N
ABCDW|Abcd Holdings - Warrant|S|N|N|100|N|N
ABCDU|Abcd Holdings - Units|S|N|N|100|N|N
PFXX|Pfx Bank - Depositary Shares each representing 1/40th Preferred Stock|S|N|N|100|N|N
DEFI|Deficient Co - Common Stock|S|N|D|100|N|N
ASML|ASML Holding N.V. - New York Registry Shares|Q|N|N|100|N|N
BRK.X|Fake Class X - Common Stock|Q|N|N|100|N|N
File Creation Time: 1002202611:01|||||||
"""


@pytest.fixture
def settings(tmp_path):
    return config.Settings(state_dir=tmp_path / "state", workers=2)


# ------------------------------------------------------------------ universe
def test_listing_parses_and_drops_the_footer():
    rows = universe.parse_listing(LISTING)
    assert len(rows) == 9
    assert all("File Creation" not in r["Symbol"] for r in rows)


def test_only_common_stocks_survive():
    kept, dropped = universe.common_stocks(universe.parse_listing(LISTING))
    assert [k["symbol"] for k in kept] == ["AAPL", "ASML", "BRK-X"]
    assert dropped == {"ETF": 1, "test issue": 1, "not common stock": 3, "financial status D": 1}


def test_listing_without_header_is_refused():
    with pytest.raises(ValueError):
        universe.parse_listing("garbage\nmore garbage")


def test_refresh_diffs_listings_incrementally(settings):
    first = universe.refresh(settings, text=LISTING)
    assert first["common_stocks"] == 3 and first["added"] == 3 and first["removed"] == 0
    smaller = LISTING.replace("ASML|ASML Holding N.V. - New York Registry Shares|Q|N|N|100|N|N\n", "")
    second = universe.refresh(settings, text=smaller)
    assert second["added"] == 0 and second["removed"] == 1
    assert Store(settings.db_path).active_symbols() == ["AAPL", "BRK-X"]
    third = universe.refresh(settings, text=LISTING)              # relisted
    assert third["added"] == 1


# ------------------------------------------------------------------ screening
def _candles(n=1500, start=100.0, drift=0.0006, swing=0.02, volume=2_000_000, gap=0.0):
    """Deterministic synthetic daily bars: a seeded random walk with a slow cycle."""
    import random
    from ftmo_mt5.marketdata.base import Candle
    rng = random.Random(7)
    out, price = [], start
    for i in range(n):
        o = price * (1 + gap * (1 if i % 7 == 0 else 0))
        c = o * (1 + drift + swing * (0.5 * rng.gauss(0, 1) + 0.15 * math.sin(i / 25.0)))
        h, l = max(o, c) * (1 + swing / 2), min(o, c) * (1 - swing / 2)
        out.append(Candle(timestamp=1_500_000_000 + i * 86400, open=o, high=h, low=l, close=c, volume=volume))
        price = c
    return out


def test_liquid_stock_passes_and_scores(settings):
    from research_agent.screener import measure_candles
    row = measure_candles("GOOD", _candles(), settings)
    assert row["passed"] == 1, row["reason"]
    assert 0 < row["score"] <= 100
    assert row["dollar_volume"] > settings.min_dollar_volume


def test_each_filter_rejects_with_a_reason(settings):
    from research_agent.screener import measure_candles
    assert "liquidity" in measure_candles("THIN", _candles(volume=1_000), settings)["reason"]
    assert "history" in measure_candles("NEW", _candles(n=300), settings)["reason"]
    assert "price" in measure_candles("PENNY", _candles(start=1.0, drift=0.0), settings)["reason"]
    assert "ATR" in measure_candles("FLAT", _candles(swing=0.0005), settings)["reason"]


def test_screening_is_incremental(settings, monkeypatch):
    """A stock screened today is skipped; force rescreens it."""
    from research_agent import screener
    universe.refresh(settings, text=LISTING)
    monkeypatch.setattr(screener, "ProcessPoolExecutor", _InlinePool)
    monkeypatch.setattr(screener, "_screen_one",
                        lambda s, d: {**screener.measure_candles(s, _candles(), settings), "error": None})
    first = screener.run(settings)
    assert first["screened"] == 3 and first["skipped_fresh"] == 0
    second = screener.run(settings)
    assert second["screened"] == 0 and second["skipped_fresh"] == 3
    assert screener.run(settings, force=True)["screened"] == 3
    assert len(screener.shortlist(settings)) == 3


class _InlinePool:
    """ProcessPoolExecutor stand-in: same interface, runs in-process (tests only)."""
    def __init__(self, max_workers=None, initializer=None, initargs=()):
        pass
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False
    def submit(self, fn, *args):
        from concurrent.futures import Future
        f = Future()
        f.set_result(fn(*args))
        return f


# ------------------------------------------------------------------ strategy contract
GOOD_SPEC = {
    "name": "rsi_dip_trend",
    "description": "Buy RSI dips while above the long average; exit on RSI recovery.",
    "params": {"rsi_period": 14, "trend_period": 100, "dip_level": 35, "exit_level": 55},
    "long_entry": {"all": [
        {"left": {"ind": "close"}, "op": ">", "right": {"ind": "sma", "period": "$trend_period"}},
        {"left": {"ind": "rsi", "period": "$rsi_period"}, "op": "<", "right": {"value": "$dip_level"}}]},
    "long_exit": {"any": [{"left": {"ind": "rsi", "period": "$rsi_period"}, "op": ">", "right": {"value": "$exit_level"}}]},
    "short_entry": None, "short_exit": None,
}


def test_valid_spec_passes_and_name_is_stable():
    from research_agent import strategy_spec as ss
    clean = ss.validate(GOOD_SPEC)
    n1 = ss.registered_name(clean)
    assert n1.startswith("AI_RSI_DIP_TREND_") and n1 == ss.registered_name(ss.validate(dict(GOOD_SPEC)))
    changed = {**GOOD_SPEC, "params": {**GOOD_SPEC["params"], "dip_level": 30}}
    assert ss.registered_name(ss.validate(changed)) != n1


@pytest.mark.parametrize("mutate, needle", [
    (lambda s: s.update(params={}), "params must be"),
    (lambda s: s["params"].update(unused_period=10), "never used"),
    (lambda s: s.update(long_exit=None), "long_entry needs long_exit"),
    (lambda s: s["params"].update(rsi_period=14.5), "must be an integer"),
    (lambda s: s.update(params={"rsi_len": 14, "trend_period": 100, "dip_level": 35, "exit_level": 55},
                        long_exit={"any": [{"left": {"ind": "rsi", "period": "$rsi_len"}, "op": ">", "right": {"value": "$exit_level"}}]},
                        long_entry={"all": [{"left": {"ind": "close"}, "op": ">", "right": {"ind": "sma", "period": "$trend_period"}},
                                            {"left": {"ind": "rsi", "period": "$rsi_len"}, "op": "<", "right": {"value": "$dip_level"}}]}),
     "_period or _lookback"),
    (lambda s: s["long_entry"]["all"].append({"left": {"ind": "macd", "period": 3}, "op": ">", "right": {"value": 0}}), "ind must be"),
    (lambda s: s["long_entry"]["all"].append({"left": {"ind": "close"}, "op": "~", "right": {"value": 0}}), "op must be"),
    (lambda s: s["long_entry"]["all"].append({"left": {"ind": "close", "shift": -1}, "op": ">", "right": {"value": 1}}), "shift must be"),
    (lambda s: s.update(name="Bad Name!"), "slug"),
])
def test_contract_violations_are_rejected_with_reasons(mutate, needle):
    import copy
    from research_agent import strategy_spec as ss
    spec = copy.deepcopy(GOOD_SPEC)
    mutate(spec)
    with pytest.raises(ss.SpecError) as e:
        ss.validate(spec)
    assert needle in str(e.value)


def test_spec_strategy_has_no_look_ahead():
    """Changing the future must not change any past decision."""
    from research_agent import strategy_spec as ss
    strat = ss.SpecStrategy(GOOD_SPEC)
    candles = _candles(n=600, swing=0.04)
    def run(cs):
        st_ = strat.prepare(cs, {})
        return [strat.evaluate(cs, i, st_).signal for i in range(len(cs))]
    base = run(candles)
    future_shock = candles[:400] + _candles(n=200, start=5.0, drift=-0.01)
    assert run(future_shock)[:400] == base[:400]
    assert {"BUY", "EXIT"} <= set(base)


def test_spec_strategy_runs_through_the_existing_engine(settings):
    from research_agent import strategy_spec as ss
    from ftmo_mt5.lab import metrics
    strat = ss.SpecStrategy(GOOD_SPEC)
    ss.install(strat)
    spec = {"strategy": strat.name, "strategy_parameters": dict(GOOD_SPEC["params"]),
            "allow_long": True, "allow_short": False, "position_size": 100.0,
            "commission": 0.005, "slippage_ticks": 1.0}
    r = metrics.measure("TEST", "1d", spec, _candles(n=1200, swing=0.04))
    assert r["trades"] >= 2 and "bhReturnPct" in r and "vsBHPct" in r
    assert r["warmupBars"] == 100                      # inferred from trend_period


def test_team_specs_load_and_proposals_are_logged(settings):
    import json as _json
    from research_agent import catalog
    assert {t["file"] for t in catalog.team_specs()} == {"rsi2_pullback.json", "volume_breakout.json"}
    bad = catalog.propose(settings, {"name": "x"}, "too short")
    assert not bad["accepted"]
    ok = catalog.propose(settings, GOOD_SPEC, "Dips inside an uptrend mean-revert faster than trend breaks.")
    again = catalog.propose(settings, GOOD_SPEC, "Same idea proposed twice by the agent in one run.")
    assert ok["accepted"] and not ok["duplicate"] and again["duplicate"]
    log = [_json.loads(l) for l in settings.strategy_log.read_text(encoding="utf-8").splitlines()]
    assert [e["event"] for e in log] == ["rejected", "accepted", "duplicate"]
    names = catalog.install_all(settings)
    assert ok["name"] in names and len(names) == 3
    kinds = {s["kind"] for s in catalog.catalog(settings)}
    assert kinds == {"builtin", "team", "ai"}
    assert "BUY_AND_HOLD_BASELINE" not in {s["name"] for s in catalog.builtin()}


# ------------------------------------------------------------------ backtester
@pytest.fixture
def offline_backtests(settings, monkeypatch):
    """Inline pool, synthetic bars, and no writes to the project-wide results index."""
    import systematic.data
    from research_agent import backtester, catalog
    monkeypatch.setattr(backtester, "ProcessPoolExecutor", _InlinePool)
    monkeypatch.setattr(backtester, "_index", lambda row: None)
    from research_agent import bars as bars_mod
    monkeypatch.setattr(systematic.data, "load_completed",
                        lambda sym, tf, bars=None, now=None: _candles(n=1200, swing=0.04))
    monkeypatch.setattr(bars_mod, "load", lambda sym, tf, settings, now=None: _candles(n=1200, swing=0.04))
    catalog.install_all(settings)
    return backtester


NTF = len(config.TIMEFRAMES)


def test_backtests_dedupe_and_store_full_metrics(settings, offline_backtests):
    bt = offline_backtests
    pairs = [{"symbol": "AAA", "strategy": "DONCHIAN_BREAKOUT", "params": {"entry_lookback": 55, "exit_lookback": 20}}]
    first = bt.run(settings, pairs + pairs)              # duplicate inside one batch
    assert first["unique"] == NTF and first["ran"] == NTF and first["errors"] == 0   # one per timeframe
    again = bt.run(settings, pairs)
    assert again["ran"] == 0 and again["skipped_known"] == NTF
    rows = Store(settings.db_path).tests(symbol="AAA")
    assert sorted(r["timeframe"] for r in rows) == sorted(config.TIMEFRAMES)
    for k in ("returnPct", "bhReturnPct", "vsBHPct", "maxDDPct", "sharpe", "trades", "winRate", "cagrPct"):
        assert k in rows[0]["metrics"], k
    cheaper = config.Settings(state_dir=settings.state_dir, workers=2, commission_per_unit=0.0)
    assert bt.run(cheaper, pairs)["ran"] == NTF          # other costs = another test


def test_validation_phase_grades_and_scores(settings, offline_backtests):
    bt = offline_backtests
    pairs = [{"symbol": "AAA", "strategy": "DONCHIAN_BREAKOUT", "params": {"entry_lookback": 55, "exit_lookback": 20}}]
    bt.run(settings, pairs, phase="screen")
    picked = bt.pick_for_validation(settings, ["AAA"])
    # top 3 strategy x timeframe combinations per stock, each naming its timeframe
    assert len(picked) == settings.validate_top_n and all(p["strategy"] == "DONCHIAN_BREAKOUT" for p in picked)
    assert len({p["timeframe"] for p in picked}) == settings.validate_top_n
    res = bt.run(settings, picked, phase="validated")
    assert res["ran"] == settings.validate_top_n            # only the picked timeframes, not all 10
    row = Store(settings.db_path).tests(symbol="AAA", phase="validated")[0]
    assert row["timeframe"] in {p["timeframe"] for p in picked}
    assert row["grade"] in config.GRADES and row["why"] is not None
    for k in ("oosVsBHPct", "foldsWon", "survivesTicks", "variationsHeldPct", "robustnessScore"):
        assert k in row["metrics"], k


def test_backtest_errors_are_reported_not_stored(settings, offline_backtests):
    res = offline_backtests.run(settings, [{"symbol": "AAA", "strategy": "NO_SUCH_RULE", "params": {}}])
    assert res["errors"] == NTF and Store(settings.db_path).test_count() == {}


# ------------------------------------------------------------------ verdicts + leaderboard
def _row(phase="validated", grade="MODERATE", ret=150.0, bh=80.0, trades=40, **extra):
    m = {"returnPct": ret, "bhReturnPct": bh, "vsBHPct": ret - bh, "trades": trades,
         "oosVsBHPct": 12.0, "foldsWon": 3, "foldsRun": 5, "survivesTicks": 4.0, "robustnessScore": 60.0, **extra}
    return {"phase": phase, "grade": grade if phase == "validated" else None, "metrics": m, "why": ["x"]}


@pytest.mark.parametrize("row, verdict, needle", [
    (_row(), "Recommended", "Beats Buy & Hold by 70.0 pts"),
    (_row(grade="ROBUST"), "Recommended", "grade ROBUST"),
    (_row(phase="screen"), "Watch", "not yet robustness-tested"),
    (_row(grade="FRAGILE"), "Watch", "grades FRAGILE"),
    (_row(ret=100.0, bh=80.0), "Rejected", "target is 50"),
    (_row(ret=60.0, bh=80.0), "Rejected", "Trails Buy & Hold by 20.0"),
    (_row(ret=-5.0, bh=-60.0), "Rejected", "Lost money"),
    (_row(trades=4), "Rejected", "Only 4 trades"),
])
def test_verdict_policy(settings, row, verdict, needle):
    from research_agent.ranking import verdict_for
    v = verdict_for(row, settings)
    assert v["verdict"] == verdict and needle in v["reason"], v


def test_target_is_a_setting(settings):
    from research_agent.ranking import verdict_for
    lenient = config.Settings(state_dir=settings.state_dir, target_outperformance_pts=10.0)
    assert verdict_for(_row(ret=100.0, bh=80.0), lenient)["verdict"] == "Recommended"


def test_leaderboard_ranks_and_exports(settings, tmp_path):
    import csv as _csv
    from research_agent import export, ranking
    store = Store(settings.db_path)
    def save(fp, sym, strat, row, tf=config.DAILY):
        store.save_test({"fingerprint": fp, "symbol": sym, "strategy": strat, "params": {"a_period": 5},
                         "phase": row["phase"], "as_of": "2026-10-01", "bars": 2500, "timeframe": tf,
                         "metrics": row["metrics"], "grade": row["grade"], "why": row["why"]})
    save("1", "AAA", "R1", _row(ret=60.0))                       # rejected (trails)
    save("2", "AAA", "R2", _row(phase="screen"))                 # screen row ...
    save("3", "AAA", "R2", _row())                               # ... superseded by validated
    save("4", "BBB", "R1", _row(grade="FRAGILE"))                # watch
    rows = ranking.leaderboard(settings)
    assert [(r["Stock"], r["Strategy"], r["Verdict"]) for r in rows] == [
        ("AAA", "R2", "Recommended"), ("BBB", "R1", "Watch"), ("AAA", "R1", "Rejected")]
    assert rows[0]["Robustness"] == "MODERATE (60/100)" and rows[0]["Rank"] == 1
    assert len(ranking.leaderboard(settings, best_per_stock=True)) == 2
    out = export.export(settings, rows, ranking.summary(rows))
    with open(out["csv"], encoding="utf-8") as fh:
        got = list(_csv.reader(fh))
    assert got[0][:10] == ["Rank", "Stock", "Strategy", "Timeframe", "Return %", "Buy & Hold %",
                           "Outperformance (pts)", "Robustness", "Verdict", "Reason"]
    assert {r[3] for r in got[1:]} == {config.DAILY}
    assert len(got) == 4 and Path(out["xlsx"]).exists()
    assert Path(out["csv"]).parent == settings.exports_dir


# ------------------------------------------------------------------ safety
FORBIDDEN = ("ftmo_mt5.execution", "ftmo_mt5.main", "ftmo_mt5.agents", "broker_hub", "mt5_api_server",
             "MetaTrader5", "tastytrade", "webull", "ib_insync", "ib_async", "systematic.trader",
             "systematic.paper_broker", "mcp_server.server")


def test_import_boundary_in_a_clean_process():
    """Loading everything the agent can reach pulls in no broker or execution code."""
    code = (
        "import sys, json\n"
        "import research_agent.agent, research_agent.tools, research_agent.pipeline, research_agent.export\n"
        "import research_agent.web, research_agent.views, research_agent.sheets\n"
        "import research_agent.control, research_agent.worker\n"
        "import ftmo_mt5.lab.metrics, ftmo_mt5.lab.verdict, ftmo_mt5.lab.service, systematic.research, systematic.data\n"
        "import mcp_server.results_index\n"
        "print(json.dumps(sorted(sys.modules)))\n")
    out = subprocess.run([sys.executable, "-c", code], cwd=REPO, capture_output=True, text=True, timeout=120,
                         env=_clean_env())
    assert out.returncode == 0, out.stderr[-2000:]
    import json as _json
    mods = _json.loads(out.stdout.strip().splitlines()[-1])
    leaked = [m for m in mods if any(m == f or m.startswith(f + ".") for f in FORBIDDEN)]
    assert not leaked, leaked


def test_no_source_file_names_a_forbidden_module():
    import re as _re
    pat = _re.compile(r"^\s*(from|import)\s+(" + "|".join(_re.escape(f) for f in FORBIDDEN) + r")\b", _re.M)
    hits = [p.name for p in (REPO / "research_agent").rglob("*.py") if pat.search(p.read_text(encoding="utf-8"))]
    assert not hits, hits


def test_tool_allowlist_is_research_only():
    from research_agent import tools
    assert set(tools.TOOLS) == {"refresh_universe", "screen_universe", "get_shortlist", "market_snapshot",
                                "list_strategies", "strategy_schema", "propose_strategy", "run_backtests",
                                "validate_top", "lookup_results", "get_leaderboard", "export_leaderboard",
                                "write_report", "screen_symbols", "company_coverage", "company_research",
                                "record_company_context", "finalize_research"}
    for name in tools.TOOLS:
        assert not any(w in name for w in ("order", "trade", "position", "broker", "account", "shell", "exec", "file"))
    assert all(a.startswith("mcp__research__") for a in tools.ALLOWED)


def test_agent_options_and_gates(settings):
    import asyncio as _asyncio
    from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny
    from research_agent import agent, tools
    ctx = tools.ToolContext(settings=settings, run_id="t")
    denied = []
    o = agent.build_options(ctx, "m", 5, 1.0, settings.state_dir, denied)
    assert o.tools == [] and o.allowed_tools == [] and o.strict_mcp_config is True
    assert o.setting_sources == [] and o.permission_mode == "default"
    assert set(o.mcp_servers) == {"research"} and {"Bash", "Write", "Read", "WebFetch"} <= set(o.disallowed_tools)
    run = _asyncio.run
    assert isinstance(run(o.can_use_tool("mcp__research__get_leaderboard", {}, None)), PermissionResultAllow)
    for bad in ("Bash", "Write", "mcp__ftmo__place_order", "mcp__broker_hub__submit", "mcp__research_evil__x"):
        assert isinstance(run(o.can_use_tool(bad, {}, None)), PermissionResultDeny), bad
    hook = o.hooks["PreToolUse"][0].hooks[0]
    assert run(hook({"tool_name": "mcp__research__run_backtests"}, None, None)) == {}
    assert run(hook({"tool_name": "Bash"}, None, None))["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert len(denied) == 6


def test_report_and_export_stay_in_their_folders(settings):
    import asyncio as _asyncio
    from research_agent import tools
    ctx = tools.ToolContext(settings=settings, run_id="t")
    res = _asyncio.run(tools.call(ctx, "write_report", {"title": "../../../etc/passwd", "markdown": "x"}))
    assert Path(res["path"]).parent == settings.reports_dir
    res = _asyncio.run(tools.call(ctx, "export_leaderboard", {}))
    assert all(Path(p).parent == settings.exports_dir for k, p in res["files"].items() if k in ("csv", "xlsx"))


def test_tool_errors_and_budgets_come_back_as_data(settings):
    import asyncio as _asyncio
    from research_agent import tools
    ctx = tools.ToolContext(settings=settings, run_id="t", job_budget=5, max_new_strategies=1)
    call = lambda n, a: _asyncio.run(tools.call(ctx, n, a))
    assert "error" in call("run_backtests", {"symbols": ["AAPL"], "strategies": ["NOPE"]})
    assert "error" in call("run_backtests", {"symbols": ["rm -rf"], "strategies": ["all"]})
    universe.refresh(settings, text=LISTING)
    assert "run budget" in call("run_backtests", {"symbols": ["AAPL"], "strategies": ["all"]})["error"]
    assert call("propose_strategy", {"spec": {"name": "x"}, "rationale": "r"})["accepted"] is False
    assert "at most 1" in call("propose_strategy", {"spec": GOOD_SPEC, "rationale": "r" * 30})["error"]
    assert ctx.calls and all("tool" in c for c in ctx.calls)


# ------------------------------------------------------------------ output layer: views, sheets, web
def _seed_output(settings):
    """A tiny universe, two screens and three tests, written through the real store."""
    store = Store(settings.db_path)
    universe.refresh(settings, text=LISTING)
    store.save_screen({"symbol": "AAPL", "screened_at": "2026-10-02T10:00:00+00:00", "as_of": "2026-10-01",
                       "passed": 1, "score": 90.0, "price": 200.0, "dollar_volume": 9e9, "atr_pct": 2.0,
                       "history_bars": 2500, "trend_up": 1, "return_1y": 20.0, "gap_p95": 1.5,
                       "reason": "liquid, tradable range, enough history"})
    store.save_screen({"symbol": "ASML", "screened_at": "2026-10-02T10:00:00+00:00", "as_of": "2026-10-01",
                       "passed": 0, "score": 0.0, "price": 3.0, "dollar_volume": 5e6, "atr_pct": 2.0,
                       "history_bars": 2500, "trend_up": 0, "return_1y": -5.0, "gap_p95": 1.0,
                       "reason": "price $3.00 < $5; liquidity $5.0M/day < $20M"})
    for fp, strat, row in (("1", "R1", _row()), ("2", "R2", _row(ret=60.0)), ("3", "R3", _row(grade="FRAGILE"))):
        store.save_test({"fingerprint": fp, "symbol": "AAPL", "strategy": strat, "params": {"a_period": 5},
                         "timeframe": config.DAILY,
                         "phase": row["phase"], "as_of": "2026-10-01", "bars": 2500,
                         "metrics": row["metrics"], "grade": row["grade"], "why": row["why"]})


def test_views_read_the_store_and_agree_with_ranking(settings):
    from research_agent import ranking, views
    _seed_output(settings)
    s = views.summary(settings)
    assert (s["universe"], s["scanned"], s["passed"], s["shortlisted"], s["strategies_tested"], s["backtests"]) == \
           (3, 2, 1, 1, 3, 0)            # _row tests are 'validated' phase
    assert s["validations"] == 3 and s["verdicts"] == ranking.summary(ranking.leaderboard(settings))["verdicts"]
    lb = views.leaderboard(settings)
    assert len(lb) == 1 and lb[0]["Best Strategy"] == "R1" and lb[0]["Verdict"] == "Recommended"
    assert lb[0]["Combinations tried"] == 3 and lb[0]["Parameters"] == "a_period=5"
    assert lb[0]["Timeframe"] == config.DAILY and all(t["Timeframe"] == config.DAILY for t in views.strategy_tests(settings))
    tfs = s["timeframes"]
    assert [x["Timeframe"] for x in tfs] == list(config.TIMEFRAMES) and s["timeframe_list"] == list(config.TIMEFRAMES)
    by = {x["Timeframe"]: x for x in tfs}
    assert by[config.DAILY]["Tests"] == 3 and sum(x["Tests"] for x in tfs) == 3      # nothing else has run yet
    assert by[config.DAILY]["History"] == "last 2,500 bars"
    assert all(x["History"] == "all available history" for x in tfs if x["Timeframe"] != config.DAILY)
    tests = views.strategy_tests(settings)
    assert [t["Strategy"] for t in tests] == ["R1", "R3", "R2"]      # by outperformance within the stock
    scr = views.screening(settings)
    assert [(r["Symbol"], r["Result"], r["Trend"]) for r in scr] == [("AAPL", "PASS", "Uptrend"), ("ASML", "FAIL", "Downtrend")]
    assert "price $3.00 < $5" in scr[1]["Reason"] and scr[0]["Liquidity ($M/day)"] == 9000.0
    assert len(views.screening(settings, "fail")) == 1
    tabs = views.report_tables(settings)
    assert list(tabs) == ["Summary", "Timeframes", "Leaderboard", "Strategy Tests", "Screening"]
    assert tabs["Leaderboard"]["columns"][:2] == ["Rank", "Stock"] and len(tabs["Screening"]["rows"]) == 2


def test_excel_workbook_has_the_report_tabs(settings):
    from openpyxl import load_workbook
    from research_agent import export, views
    _seed_output(settings)
    path = export.to_xlsx(settings.exports_dir / "r.xlsx", settings)
    wb = load_workbook(path)
    assert wb.sheetnames == ["Summary", "Timeframes", "Leaderboard", "Strategy Tests", "Screening"]
    assert [r[0].value for r in wb["Timeframes"].iter_rows(min_row=2)] == list(config.TIMEFRAMES)
    assert [c.value for c in wb["Leaderboard"][1]] == views.LEADERBOARD_COLS
    assert wb["Screening"].max_row == 3


class _FakeWS:
    def __init__(self, title, wid):
        self.title, self.id, self.values, self.cleared = title, wid, None, 0
    def clear(self): self.cleared += 1
    def resize(self, rows=None, cols=None): pass
    def update(self, values=None, range_name=None, value_input_option=None):
        assert value_input_option == "RAW"; self.values = values


class _FakeBook:
    def __init__(self):
        self.ws = {"Summary": _FakeWS("Summary", 0)}
        self.requests = []
    def fetch_sheet_metadata(self, params=None):
        return {"sheets": [{"properties": {"title": "Summary"}, "conditionalFormats": [{}, {}]}]}
    def worksheets(self): return list(self.ws.values())
    def add_worksheet(self, title, rows, cols):
        self.ws[title] = _FakeWS(title, len(self.ws)); return self.ws[title]
    def batch_update(self, body): self.requests += body["requests"]


class _FakeClient:
    def __init__(self): self.book = _FakeBook(); self.opened = None
    def open_by_key(self, key): self.opened = key; return self.book


def test_sheets_sync_writes_the_report_tabs(settings):
    from research_agent import sheets, views
    _seed_output(settings)
    with pytest.raises(RuntimeError, match="not set up"):
        sheets.sync(settings)
    with pytest.raises(ValueError):
        sheets.set_sheet(settings, "not a sheet")
    sid = "1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"
    st = sheets.set_sheet(settings, f"https://docs.google.com/spreadsheets/d/{sid}/edit#gid=0")
    assert st["sheet_id"] == sid and not st["credentials_file_present"]
    fake = _FakeClient()
    res = sheets.sync(settings, client=fake)
    assert fake.opened == sid and res["rows"] == {"Summary": len(views.report_tables(settings)["Summary"]["rows"]) + 1,
                                                  "Timeframes": NTF, "Leaderboard": 1, "Strategy Tests": 3,
                                                  "Screening": 2}
    book = fake.book
    assert set(book.ws) == set(views.report_tables(settings))
    assert book.ws["Leaderboard"].values[0] == views.LEADERBOARD_COLS
    assert book.ws["Screening"].values[1][0] == "AAPL" and book.ws["Summary"].cleared == 1
    deletes = [r for r in book.requests if "deleteConditionalFormatRule" in r]
    assert len(deletes) == 2                                      # stale Summary rules removed, not stacked
    assert sheets.status(settings)["last_sync"] and sheets.status(settings)["last_error"] is None


def test_web_endpoints_serve_the_views(settings, monkeypatch):
    from fastapi.testclient import TestClient
    from research_agent import web
    _seed_output(settings)
    monkeypatch.setattr(web, "_settings", lambda: settings)
    c = TestClient(web.standalone_app())
    assert "AI Research" in c.get("/research").text
    assert c.get("/api/research/summary").json()["passed"] == 1
    assert c.get("/api/research/leaderboard").json()["rows"][0]["Stock"] == "AAPL"
    assert len(c.get("/api/research/tests", params={"symbol": "aapl"}).json()["rows"]) == 3
    assert len(c.get("/api/research/screening", params={"status": "pass"}).json()["rows"]) == 1
    assert c.get("/api/research/screening", params={"status": "bogus"}).status_code == 400
    x = c.get("/api/research/export.xlsx")
    assert x.status_code == 200 and x.content[:2] == b"PK"
    assert c.post("/api/research/sheets/config", json={"sheet": "nope"}).status_code == 400
    assert "not set up" in c.post("/api/research/sheets/sync").json()["detail"]


def test_web_router_has_no_write_paths_beyond_sheets():
    from research_agent import web
    posts = sorted(r.path for r in web.router.routes if "POST" in getattr(r, "methods", set()))
    assert posts == ["/api/research/agent/pause", "/api/research/agent/run-now", "/api/research/agent/settings",
                     "/api/research/agent/start", "/api/research/agent/stop",
                     "/api/research/sheets/config", "/api/research/sheets/sync"]


# ------------------------------------------------------------------ 24/7 worker + control center
@pytest.fixture
def ctl(settings, monkeypatch):
    from research_agent import control
    spawned = []
    monkeypatch.setattr(control.Control, "spawn_worker", lambda self: spawned.append(1) or 4242)
    c = control.Control(settings)
    c.spawned = spawned
    return c


def _fake_run(summary=None, sleep=0.0, emit=True):
    """Stands in for agent.run_agent: same signature, emits the same transcript entries."""
    import asyncio as _asyncio
    async def fake(settings, on_event=None, run_id=None, **kw):
        if emit:
            for e in ({"type": "tool_use", "tool": "mcp__research__screen_universe", "input": {"limit": 600}},
                      {"type": "progress", "tool": "screen_universe", "message": "screened 100/600"},
                      {"type": "tool_call", "tool": "screen_universe", "seconds": 1.2, "ok": True,
                       "brief": {"screened": 600, "passed": 130}},
                      {"type": "tool_use", "tool": "mcp__research__run_backtests", "input": {"symbols": ["AAPL"]}},
                      {"type": "text", "text": "Backtests done."},
                      {"type": "result", "cost_usd": 0.4}):
                on_event({**e, "run_id": run_id})
        await _asyncio.sleep(sleep)
        return {"run_id": run_id, "outcome": "completed", "cost_usd": 0.4, "turns": 9, "tool_calls": 4,
                **(summary or {})}
    return fake


def test_control_commands_and_settings(ctl):
    assert ctl.status()["state"] == "stopped"
    s = ctl.start()
    assert s["desired"] == "running" and s["next_run_at"] and ctl.spawned == [1]
    assert ctl.pause()["state"] == "paused"
    assert ctl.stop()["state"] == "stopped"
    r = ctl.run_now()
    assert r["desired"] == "paused" and r["run_now_pending"]        # one-off run, schedule stays off
    new = ctl.save_settings({"interval_minutes": 60, "shortlist_size": 10, "budget_usd": 2, "focus": "  dips  "})
    assert (new.interval_minutes, new.shortlist_size, new.budget_usd, new.focus) == (60, 10, 2.0, "dips")
    for bad in ({"interval_minutes": 5}, {"budget_usd": 999}, {"daily_budget_usd": 1}, {"nope": 1},
                {"model": "gpt-5"}):
        with pytest.raises(ValueError):
            ctl.save_settings(bad)
    kinds = [e["kind"] for e in ctl.events()]
    assert kinds.count("command") == 4 and "settings" in kinds


def test_worker_runs_a_scheduled_cycle_and_reports_progress(settings, ctl, monkeypatch):
    import asyncio as _asyncio
    from research_agent import worker as W
    monkeypatch.setattr(W.agent, "run_agent", _fake_run())
    ctl.start()
    wk = W.Worker(settings)
    async def go():
        assert wk.claim()
        assert not await wk.tick()                    # due now -> starts the cycle
        assert ctl.row()["current_run_id"] and ctl.status()["state"] == "running"
        await wk.task
        await wk.tick()                               # collects the result
    _asyncio.run(go())
    st = ctl.status()
    assert st["state"] == "scheduled" and st["last_result"]["outcome"] == "completed"
    assert st["current_run_id"] is None and st["consecutive_failures"] == 0
    gap = (W._parse(st["next_run_at"]) - W._parse(st["last_run_at"])).total_seconds() / 60
    assert 239 <= gap <= 241                          # default interval
    msgs = [e["message"] for e in ctl.events()]
    assert any("→ screen_universe" in m for m in msgs) and any("screened=600" in m for m in msgs)
    assert any(m.startswith("Run completed — $0.40") for m in msgs)
    stages = {e["stage"] for e in ctl.events()}
    assert {"screening", "backtesting"} <= stages


def test_stop_cancels_a_running_cycle(settings, ctl, monkeypatch):
    import asyncio as _asyncio
    from research_agent import worker as W
    monkeypatch.setattr(W.agent, "run_agent", _fake_run(sleep=30, emit=False))
    ctl.start()
    wk = W.Worker(settings)
    async def go():
        wk.claim()
        await wk.tick()
        await _asyncio.sleep(0.05)
        ctl.stop()
        assert ctl.status()["state"] in ("stopping", "stopped")
        assert await wk.tick() is True                 # worker decides to exit
        await wk._cancel_run()
    _asyncio.run(go())
    assert ctl.status()["last_result"]["outcome"] == "cancelled"
    assert ctl.row()["current_run_id"] is None


def test_worker_recovers_an_interrupted_run(settings, ctl):
    from research_agent import worker as W
    ctl.store.start_run("agent-crashed", "agent")
    ctl.update(desired="running", current_run_id="agent-crashed", pid=None)
    assert W.Worker(settings).claim()
    hist = {h["run_id"]: h for h in ctl.history()}
    assert hist["agent-crashed"]["outcome"] == "interrupted"
    assert any("Recovered" in e["message"] for e in ctl.events())


def test_repeated_failures_auto_pause(settings, ctl, monkeypatch):
    import asyncio as _asyncio
    from research_agent import worker as W
    monkeypatch.setattr(W.agent, "run_agent", _fake_run(
        summary={"outcome": "failed", "error": "401 expired", "fix": "claude auth login"}, emit=False))
    ctl.start()
    wk = W.Worker(settings)
    async def go():
        wk.claim()
        for _ in range(3):
            ctl.update(next_run_at=W.now())
            await wk.tick(); await wk.task; await wk.tick()
    _asyncio.run(go())
    st = ctl.status()
    assert st["desired"] == "paused" and "auto-paused after 3" in st["pause_reason"]
    assert "claude auth login" in st["pause_reason"]


def test_daily_budget_blocks_new_runs(settings, ctl, monkeypatch):
    import asyncio as _asyncio
    from research_agent import worker as W
    monkeypatch.setattr(W.agent, "run_agent", _fake_run())
    ctl.save_settings({"budget_usd": 5, "daily_budget_usd": 5})
    ctl.store.start_run("agent-earlier", "agent")
    ctl.store.finish_run("agent-earlier", {"cost_usd": 1.0})
    ctl.start()
    wk = W.Worker(settings)
    async def go():
        wk.claim(); await wk.tick()
    _asyncio.run(go())
    assert wk.task is None and ctl.row()["current_run_id"] is None
    assert any(e["kind"] == "budget" for e in ctl.events())


def test_single_worker_instance(settings, ctl):
    import subprocess as _sp
    from research_agent import worker as W
    # a live "other" worker: a real process whose command line names research_agent
    other = _sp.Popen([sys.executable, "-c", "import time; time.sleep(60)", "research_agent", "worker"])
    try:
        ctl.update(pid=other.pid, heartbeat_at=W.now(), status="running")
        assert W.Worker(settings).claim() is False
        ctl.update(pid=_unrelated_pid(), heartbeat_at=W.now(), status="running")   # recycled PID: not ours
        assert W.Worker(settings).claim() is True
    finally:
        other.kill()
        other.wait()


def _unrelated_pid() -> int:
    import subprocess as _sp
    p = _sp.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    _UNRELATED.append(p)
    return p.pid


_UNRELATED: list = []


@pytest.fixture(autouse=True)
def _reap_unrelated():
    yield
    while _UNRELATED:
        p = _UNRELATED.pop()
        p.kill()
        p.wait()


def test_worker_gets_no_broker_credentials(settings, monkeypatch):
    from research_agent import control
    seen = {}
    class P:
        pid = 777
        def wait(self):
            return 0
    def fake_popen(cmd, **kw):
        seen.update(cmd=cmd, env=kw["env"], session=kw.get("start_new_session"), flags=kw.get("creationflags"))
        return P()
    monkeypatch.setattr(control.subprocess, "Popen", fake_popen)
    monkeypatch.setenv("TASTYTRADE_CLIENT_SECRET", "x")
    monkeypatch.setenv("MT5_PASSWORD", "x")
    monkeypatch.setenv("WEBULL_APP_SECRET", "x")
    control.Control(settings).spawn_worker()
    assert seen["cmd"][-2:] == ["research_agent", "worker"]
    # detached from the parent: a new session on POSIX, a new process group without a console on Windows
    assert bool(seen["flags"]) if os.name == "nt" else seen["session"] is True
    assert not [k for k in seen["env"] if any(w in k for w in ("TASTY", "MT5", "WEBULL", "BROKER", "IB_"))]
    assert seen["env"]["RESEARCH_AGENT_STATE"] == str(settings.state_dir)


def test_control_center_api(settings, monkeypatch):
    from fastapi.testclient import TestClient
    from research_agent import control, web
    monkeypatch.setattr(web, "_settings", lambda: settings)
    monkeypatch.setattr(control.Control, "spawn_worker", lambda self: 4242)
    c = TestClient(web.standalone_app())
    assert c.get("/api/research/agent/status").json()["state"] == "stopped"
    assert c.post("/api/research/agent/start").json()["desired"] == "running"
    assert c.post("/api/research/agent/pause").json()["state"] == "paused"
    assert c.post("/api/research/agent/run-now").json()["run_now_pending"] is True
    assert c.post("/api/research/agent/stop").json()["state"] == "stopped"
    assert c.post("/api/research/agent/settings", json={"interval_minutes": 90}).json()["interval_minutes"] == 90
    assert c.post("/api/research/agent/settings", json={"interval_minutes": 1}).status_code == 400
    ev = c.get("/api/research/agent/events").json()["events"]
    assert ev and ev[-1]["kind"] == "settings"
    after = c.get("/api/research/agent/events", params={"after": ev[-1]["id"]}).json()["events"]
    assert after == []
    assert "runs" in c.get("/api/research/agent/runs").json()
    assert "rows" in c.get("/api/research/strategies").json()


# ------------------------------------------------------------------ the 10 timeframes
def test_ten_timeframes_are_defined_once_and_supported_by_the_lab():
    """config.TIMEFRAMES is the only place: no other research_agent file names a timeframe literally."""
    import re as _re
    from ftmo_mt5.lab import windows
    assert len(config.TIMEFRAMES) == 10 and len(set(config.TIMEFRAMES)) == 10
    assert set(config.TIMEFRAMES) <= set(windows.TIMEFRAMES)
    assert set(config.TIMEFRAME_LABELS) == set(config.TIMEFRAMES)
    lit = _re.compile(r"""["'](?:%s)["']""" % "|".join(_re.escape(tf) for tf in windows.TIMEFRAMES))
    files = [p for p in (REPO / "research_agent").rglob("*") if p.suffix in (".py", ".html")
             and "state" not in p.parts and p.name != "config.py"]
    hits = [f"{p.name}: {m.group(0)}" for p in files for m in lit.finditer(p.read_text(encoding="utf-8"))]
    assert not hits, hits


def test_every_strategy_is_planned_on_all_ten_timeframes(settings):
    from research_agent import backtester
    jobs = backtester.plan(settings, [{"symbol": "AAA", "strategy": "S1", "params": {}},
                                      {"symbol": "AAA", "strategy": "S2", "params": {}}], "screen")
    assert len(jobs) == 2 * NTF
    for s in ("S1", "S2"):
        assert sorted(j["timeframe"] for j in jobs if j["strategy"] == s) == sorted(config.TIMEFRAMES)
    assert len({j["fingerprint"] for j in jobs}) == 2 * NTF
    with pytest.raises(ValueError):
        backtester.plan(settings, [{"symbol": "AAA", "strategy": "S1", "params": {}, "timeframe": "12h"}], "screen")


def test_daily_fingerprints_survive_the_upgrade(settings):
    """Daily tests recorded before multi-timeframe research must still dedupe."""
    import hashlib, json as _json
    from research_agent import backtester
    old = hashlib.sha256(_json.dumps({"s": "AAA", "k": "S", "p": {}, "ph": "screen", "d": "2026-10-01",
                                      "b": settings.bars, "c": settings.cost_key()}, sort_keys=True).encode()).hexdigest()[:24]
    assert backtester.fingerprint("AAA", "S", {}, "screen", "2026-10-01", settings, config.DAILY) == old
    assert backtester.fingerprint("AAA", "S", {}, "screen", "2026-10-01", settings, config.TIMEFRAMES[0]) != old


def test_old_store_is_migrated_with_daily_timeframe(tmp_path):
    import sqlite3
    db = tmp_path / "old.db"
    c = sqlite3.connect(db)
    c.execute("""CREATE TABLE tests (fingerprint TEXT PRIMARY KEY, symbol TEXT NOT NULL, strategy TEXT NOT NULL,
                 params_json TEXT, phase TEXT NOT NULL, as_of TEXT, bars INTEGER, metrics_json TEXT NOT NULL,
                 grade TEXT, why_json TEXT, run_id TEXT, created_at TEXT)""")
    c.execute("INSERT INTO tests VALUES ('f','AAA','S','{}','screen','2026-10-01',2500,'{}',NULL,'[]',NULL,'x')")
    c.commit(); c.close()
    rows = Store(db).tests()
    assert rows[0]["timeframe"] == config.DAILY


def test_history_policy_and_completed_bars(settings):
    from datetime import datetime, timezone
    from ftmo_mt5.lab import windows
    from research_agent import bars
    assert settings.history_bars(config.DAILY) == settings.bars
    assert all(settings.history_bars(tf) is None for tf in config.TIMEFRAMES if tf != config.DAILY)
    tf = config.TIMEFRAMES[0]
    cs = _candles(n=10)
    now = datetime.fromtimestamp(cs[-1].timestamp + windows.SECONDS[tf] - 1, timezone.utc)
    assert len(bars.completed(cs, tf, now)) == 9                     # last bar still forming
    later = datetime.fromtimestamp(cs[-1].timestamp + windows.SECONDS[tf], timezone.utc)
    assert len(bars.completed(cs, tf, later)) == 10


def test_unknown_timeframe_is_refused(tmp_path):
    with pytest.raises(ValueError):
        config.Settings(state_dir=tmp_path, timeframes=("1d", "3h"))


def test_fixed_universe_run_cannot_touch_other_tickers(settings):
    import asyncio as _asyncio
    from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny
    from research_agent import agent, tools
    ctx = tools.ToolContext(settings=settings, run_id="fixed", fixed_symbols=["MU", "TSLA"])
    run = _asyncio.run
    for name, args in (("run_backtests", {"symbols": ["AAPL"], "strategies": ["all"]}),
                       ("validate_top", {"symbols": ["MU", "NVDA"]}),
                       ("market_snapshot", {"symbols": ["GS"]}),
                       ("company_research", {"symbol": "MSFT", "question": "risks?"}),
                       ("lookup_results", {})):
        assert "error" in run(tools.call(ctx, name, args)), name
    names = tools.tool_names(ctx)
    assert {"company_research", "company_coverage", "finalize_research"} <= set(names)
    assert not {"screen_universe", "refresh_universe", "get_shortlist", "write_report"} & set(names)
    plain = tools.tool_names(tools.ToolContext(settings=settings, run_id="u"))
    assert "company_research" not in plain and "screen_universe" in plain
    o = agent.build_options(ctx, "m", 5, 1.0, settings.state_dir, [])
    assert isinstance(run(o.can_use_tool("mcp__research__company_research", {}, None)), PermissionResultAllow)
    assert isinstance(run(o.can_use_tool("mcp__research__screen_universe", {}, None)), PermissionResultDeny)


def test_record_company_context_requires_every_field(settings):
    import asyncio as _asyncio
    from research_agent import company, tools
    ctx = tools.ToolContext(settings=settings, run_id="fixed", fixed_symbols=["TSLA"])
    bad = _asyncio.run(tools.call(ctx, "record_company_context", {"symbol": "TSLA", "risks": "x"}))
    assert "missing fields" in bad["error"]
    full = {k: "Not found in the available data." for k in company.FIELDS}
    ok = _asyncio.run(tools.call(ctx, "record_company_context", {"symbol": "TSLA", **full}))
    assert ok["symbol"] == "TSLA" and company.contexts(settings, "fixed")["TSLA"]["risks"].startswith("Not found")


def test_run_backtests_all_is_bounded_by_jobs_not_by_the_name_cap(settings, monkeypatch):
    """'all' used to fail once the catalog grew past MAX_STRATEGIES names (seen 2026-10-08 with 100 strategies)."""
    import asyncio as _asyncio
    from research_agent import catalog, pipeline, tools
    many = [{"name": f"S{i}", "params": {}, "kind": "builtin"} for i in range(tools.MAX_STRATEGIES + 20)]
    monkeypatch.setattr(catalog, "catalog", lambda s, kinds=None: many)
    monkeypatch.setattr(tools, "_new_jobs", lambda ctx, syms, names: len(syms) * len(names) * len(s_tf))
    seen = {}

    def fake_test(settings_, syms, names, run_id=None):
        seen["names"] = names
        return {"fingerprints": [], "unique": 0, "ran": 0, "skipped_known": 0, "errors": 0}
    s_tf = settings.timeframes
    monkeypatch.setattr(pipeline, "test_strategies", fake_test)
    ctx = tools.ToolContext(settings=settings, run_id="t")
    out = _asyncio.run(tools.call(ctx, "run_backtests", {"symbols": ["AAA"], "strategies": ["all"]}))
    assert "error" not in out and len(seen["names"]) == len(many)
    bad = _asyncio.run(tools.call(ctx, "run_backtests", {"symbols": ["AAA"], "strategies": [f"S{i}" for i in range(31)]}))
    assert "error" in bad
