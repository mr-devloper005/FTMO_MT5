"""The research agent: a separate Claude Agent SDK process with research tools only.

Safety is layered, so no single setting is load-bearing:

1. ``tools=[]``             no built-in tools exist in the session — no Bash, Read,
                            Write, Edit, WebFetch, Task, ...
2. ``strict_mcp_config``    only the in-process ``research`` server is loaded; the
   + ``setting_sources=[]`` project's own MCP servers (broker hub, FTMO MCP) and
                            any user/project settings, hooks or allow-rules are not.
3. PreToolUse hook          denies any tool name outside ``tools.ALLOWED``.
4. ``can_use_tool``         deny-by-default: allows ``tools.ALLOWED`` only. Nothing is
                            pre-approved via ``allowed_tools``, so this callback is
                            never shadowed and sees every call.
5. The tools themselves     contain no broker/order/execution code; this package's
                            import boundary is enforced by a test.

``cwd`` is an empty per-run folder, and turns and dollars are capped.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from research_agent import catalog, prompts, tools
from research_agent.config import Settings
from research_agent.pipeline import new_run_id
from research_agent.store import Store, now

DEFAULT_MODEL = "sonnet"          # CLI alias: always the latest Sonnet

# Named as well, so even a future SDK default that re-enabled built-ins stays blocked.
DISALLOWED = ["Bash", "BashOutput", "KillShell", "Read", "Write", "Edit", "MultiEdit", "NotebookEdit",
              "Glob", "Grep", "WebFetch", "WebSearch", "Task", "Agent", "TodoWrite", "Skill",
              "SlashCommand", "ExitPlanMode", "ListMcpResourcesTool", "ReadMcpResourceTool"]


def build_options(ctx: tools.ToolContext, model: str, max_turns: int, max_budget_usd: float,
                  sandbox_dir: Path, denied: list, system_prompt: Optional[str] = None):
    from claude_agent_sdk import ClaudeAgentOptions, HookMatcher, PermissionResultAllow, PermissionResultDeny

    allowed = tools.allowed_names(ctx)

    async def can_use_tool(name: str, tool_input: Dict[str, Any], context) -> Any:
        if name in allowed:
            return PermissionResultAllow()
        denied.append({"at": now(), "tool": name, "by": "can_use_tool"})
        return PermissionResultDeny(message=f"{name} is not a research tool. This agent cannot use it.",
                                    interrupt=False)

    async def pre_tool_use(hook_input: Dict[str, Any], tool_use_id: Optional[str], context) -> Dict[str, Any]:
        name = hook_input.get("tool_name", "")
        if name in allowed:
            return {}                                    # no decision: can_use_tool still runs
        denied.append({"at": now(), "tool": name, "by": "pre_tool_use"})
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                       "permissionDecisionReason": f"{name} is outside the research allowlist"}}

    return ClaudeAgentOptions(
        model=model,
        system_prompt=system_prompt or prompts.SYSTEM,
        tools=[],
        allowed_tools=[],
        disallowed_tools=list(DISALLOWED),
        mcp_servers={tools.SERVER: tools.build_server(ctx)},
        strict_mcp_config=True,
        setting_sources=[],
        permission_mode="default",
        can_use_tool=can_use_tool,
        hooks={"PreToolUse": [HookMatcher(matcher=None, hooks=[pre_tool_use])]},
        cwd=str(sandbox_dir),
        max_turns=max_turns,
        max_budget_usd=max_budget_usd,
    )


async def run_agent(settings: Settings, shortlist_size: int = 15, max_new_strategies: int = 4,
                    model: str = DEFAULT_MODEL, max_turns: int = 60, max_budget_usd: float = 5.0,
                    job_budget: int = 12_000, focus: str = "", echo=print,
                    on_event: Optional[Callable[[Dict[str, Any]], None]] = None,
                    run_id: Optional[str] = None, symbols: Optional[list] = None) -> Dict[str, Any]:
    """One research cycle. ``on_event`` receives every transcript entry as it is
    written (the background worker turns these into live progress). Cancelling
    the task stops the Claude session and still records the run as cancelled."""
    from claude_agent_sdk import AssistantMessage, ClaudeSDKClient, ResultMessage, TextBlock, ToolUseBlock

    settings.ensure_dirs()
    run_id = run_id or new_run_id("agent")
    run_dir = settings.runs_dir / run_id
    sandbox = run_dir / "cwd"
    sandbox.mkdir(parents=True, exist_ok=True)
    transcript = run_dir / "transcript.jsonl"
    store = Store(settings.db_path)
    store.start_run(run_id, "agent")
    catalog.sync(settings)

    def log(entry: Dict[str, Any]) -> None:
        entry = {"at": now(), "run_id": run_id, **entry}
        with transcript.open("a") as fh:
            fh.write(json.dumps(entry, default=str) + "\n")
        if on_event:
            try:
                on_event(entry)
            except Exception:                                    # noqa: BLE001 — telemetry must never kill a run
                pass

    fixed = [s.strip().upper() for s in symbols] if symbols else None
    ctx = tools.ToolContext(settings=settings, run_id=run_id, job_budget=job_budget,
                            max_new_strategies=max_new_strategies, log=log, fixed_symbols=fixed)
    denied: list = []
    options = build_options(ctx, model, max_turns, max_budget_usd, sandbox, denied,
                            system_prompt=prompts.FIXED_SYSTEM if fixed else prompts.SYSTEM)
    task = prompts.fixed_task(fixed, max_new_strategies, focus) if fixed else \
        prompts.task(shortlist_size, max_new_strategies, focus)
    log({"type": "start", "model": model, "max_turns": max_turns, "max_budget_usd": max_budget_usd,
         "job_budget": job_budget, "allowed_tools": sorted(tools.allowed_names(ctx)), "task": task,
         "fixed_symbols": fixed, "timeframes": list(settings.timeframes)})

    t0, result, texts, outcome = time.time(), {}, [], "completed"
    try:
        await _converse(ClaudeSDKClient(options=options), task, result, texts, log, echo,
                        (AssistantMessage, ResultMessage, TextBlock, ToolUseBlock))
    except asyncio.CancelledError:
        outcome = "cancelled"
        result["error"] = "cancelled by the operator (Stop)"
    except Exception as e:                                       # noqa: BLE001
        outcome = "failed"
        result["error"] = f"{type(e).__name__}: {str(e)[:400]}"
    finally:
        summary = _summarise(settings, run_id, result, texts, ctx, denied, transcript, t0, outcome)
        log({"type": "summary", **summary})
        store.finish_run(run_id, summary)
    if outcome == "cancelled":
        raise asyncio.CancelledError
    return summary


async def _converse(client, task, result, texts, log, echo, types) -> None:
    AssistantMessage, ResultMessage, TextBlock, ToolUseBlock = types
    async with client:
        await client.query(task)
        async for msg in client.receive_response():
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock) and block.text.strip():
                        texts.append(block.text)
                        log({"type": "text", "text": block.text})
                        echo(f"\n[agent] {block.text.strip()[:1200]}")
                    elif isinstance(block, ToolUseBlock):
                        log({"type": "tool_use", "tool": block.name, "input": block.input})
                        echo(f"[tool] {block.name.replace('mcp__research__', '')} "
                             f"{json.dumps(block.input, default=str)[:160]}")
            elif isinstance(msg, ResultMessage):
                r = {"subtype": msg.subtype, "turns": msg.num_turns, "cost_usd": msg.total_cost_usd,
                     "is_error": msg.is_error, "duration_s": round(msg.duration_ms / 1000, 1),
                     "stop_reason": msg.stop_reason, "permission_denials": msg.permission_denials or []}
                result.update(r)
                log({"type": "result", **r})


def _summarise(settings, run_id, result, texts, ctx, denied, transcript, t0, outcome) -> Dict[str, Any]:
    # The CLI reports an auth/API failure as a one-turn "success" whose only text is the error.
    api_error = next((x.strip() for x in texts if x.lstrip().startswith(("API Error", "Failed to authenticate"))
                      or "API Error:" in x[:200]), None)
    if (api_error or result.get("is_error")) and not result.get("error"):
        result["error"] = api_error or result.get("subtype")
        if api_error and ("authenticate" in api_error.lower() or "401" in api_error):
            result["fix"] = "Log the Claude CLI in again: run `claude auth login` in a terminal (or set ANTHROPIC_API_KEY)."
    used = sorted({c["tool"] for c in ctx.calls})
    return {"run_id": run_id, "outcome": "failed" if result.get("error") and outcome == "completed" else outcome,
            **result, "seconds": round(time.time() - t0, 1),
            "tool_calls": len(ctx.calls), "tools_used": used, "denied": denied,
            "backtests_charged": ctx.jobs_used, "strategies_proposed": ctx.strategies_proposed,
            "fixed_symbols": ctx.fixed_symbols, "stats": ctx.stats,
            "transcript": str(transcript),
            "reports": sorted(str(p) for p in settings.reports_dir.glob(f"{run_id}-*.md")),
            "exports": sorted(str(p) for p in settings.exports_dir.glob(f"leaderboard-{run_id}*"))
            + sorted(str(p) for p in settings.exports_dir.glob(f"research-{run_id}*"))}


def run(settings: Settings, **kw) -> Dict[str, Any]:
    return asyncio.run(run_agent(settings, **kw))
