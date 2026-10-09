"""Every strategy the research agent can test, from three sources.

    builtin   the hand-written rules in ftmo_mt5.signals.strategies (on their own defaults)
    team      JSON specs a human put in research_agent/strategies/team/
    ai        specs the agent proposed and the validator accepted

Team and AI specs go through the same contract (strategy_spec.validate). Every
AI proposal — accepted or rejected — is appended to state/strategy_log.jsonl
with its rationale, so what the agent tried is auditable after the fact.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from research_agent.config import TEAM_STRATEGIES, Settings
from research_agent.store import Store, now
from research_agent import strategy_spec as ss

# The benchmark, not a candidate: it holds from bar one, so "beating" it with
# itself is a rounding error.
NOT_CANDIDATES = {"BUY_AND_HOLD_BASELINE"}


def builtin() -> List[Dict[str, Any]]:
    from ftmo_mt5.signals.strategies import list_strategies
    out = []
    for d in list_strategies():
        if d["name"] in NOT_CANDIDATES:
            continue
        out.append({"name": d["name"], "kind": "builtin", "label": d.get("label"),
                    "description": d.get("description", ""),
                    "params": {p["key"]: p.get("value", p["default"]) for p in d["parameters"]}})
    return out


def team_specs() -> List[Dict[str, Any]]:
    out = []
    for f in sorted(TEAM_STRATEGIES.glob("*.json")):
        spec = ss.validate(json.loads(f.read_text()))      # a broken team file should fail loudly
        out.append({"name": ss.registered_name(spec, "TEAM"), "kind": "team", "spec": spec,
                    "description": spec["description"], "params": spec["params"], "file": f.name})
    return out


def sync(settings: Settings) -> Dict[str, int]:
    """Record builtin and team strategies in the store so the catalog is one query."""
    store = Store(settings.db_path)
    b, t = builtin(), team_specs()
    for s in b:
        store.save_strategy(s["name"], "builtin", None, s["params"], s["description"], "ftmo_mt5")
    for s in t:
        store.save_strategy(s["name"], "team", s["spec"], s["params"], s["description"], f"team:{s['file']}")
    return {"builtin": len(b), "team": len(t)}


def _log(settings: Settings, entry: Dict[str, Any]) -> None:
    settings.ensure_dirs()
    with settings.strategy_log.open("a") as fh:
        fh.write(json.dumps({"at": now(), **entry}, default=str) + "\n")


def propose(settings: Settings, spec: Dict[str, Any], rationale: str, author: str = "agent") -> Dict[str, Any]:
    """Validate an AI-proposed spec; register it if it passes. Always logged."""
    rationale = (rationale or "").strip()
    try:
        if len(rationale) < 20:
            raise ss.SpecError("rationale must explain (20+ chars) why this rule might beat buy & hold")
        clean = ss.validate(spec)
    except ss.SpecError as e:
        _log(settings, {"event": "rejected", "author": author, "spec": spec, "rationale": rationale, "errors": str(e)})
        return {"accepted": False, "errors": str(e)}
    name = ss.registered_name(clean)
    store = Store(settings.db_path)
    existing = {s["name"] for s in store.strategies(kinds=["ai"])}
    duplicate = name in existing
    if not duplicate:
        store.save_strategy(name, "ai", clean, clean["params"], rationale, author)
    _log(settings, {"event": "duplicate" if duplicate else "accepted", "author": author, "name": name,
                    "spec": clean, "rationale": rationale})
    return {"accepted": True, "name": name, "duplicate": duplicate, "params": clean["params"]}


def install_all(settings: Settings) -> List[str]:
    """Make every team and AI spec resolvable by name in this process (workers call this)."""
    names = []
    for s in team_specs():
        names.append(ss.install(ss.SpecStrategy(s["spec"], name=s["name"], label_prefix="Team")))
    for s in Store(settings.db_path).strategies(kinds=["ai"]):
        names.append(ss.install(ss.SpecStrategy(s["spec"], name=s["name"], label_prefix="AI")))
    return names


def catalog(settings: Settings, kinds: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Everything testable, with default parameters. Builtins are always current."""
    out: List[Dict[str, Any]] = []
    if not kinds or "builtin" in kinds:
        out += builtin()
    if not kinds or "team" in kinds:
        out += [{k: v for k, v in s.items() if k != "spec"} for s in team_specs()]
    if not kinds or "ai" in kinds:
        for s in Store(settings.db_path).strategies(kinds=["ai"]):
            out.append({"name": s["name"], "kind": "ai", "description": s["spec"]["description"],
                        "params": s["params"], "rationale": s["rationale"], "created_at": s["created_at"]})
    return out


def defaults(settings: Settings, name: str) -> Dict[str, Any]:
    for s in catalog(settings):
        if s["name"] == name:
            return dict(s["params"] or {})
    raise KeyError(f"unknown strategy {name}")
