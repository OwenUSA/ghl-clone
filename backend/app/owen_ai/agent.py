"""One Claude Code turn for Owen AI, through the Claude Agent SDK (2026-11-02).

`run_turn` is the only place the SDK (and so the Claude Code CLI) is started. The runner passes
it in, and tests pass a fake: no test ever starts the CLI or reaches a model.

Claude runs in the workspace (`config.workspace()`: CLAUDE.md, knowledge/, .claude/skills/) with
ONLY Read/Glob/Grep/Skill and our MCP tools (`tools.TOOL_NAMES`); `dontAsk` refuses anything
else without asking — no shell, no file writes, no web.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from . import config, tools

BUILTIN_TOOLS = ["Read", "Glob", "Grep", "Skill"]
STEP_RESULT_MAX = 600


@dataclass
class TurnResult:
    text: str
    session_id: str | None
    steps: list[dict] = field(default_factory=list)
    model: str | None = None
    cost_usd: float | None = None
    duration_ms: int | None = None
    num_turns: int | None = None
    error: str | None = None


def context_note(kind: str, title: str, job_uid: str | None, job_number: str | None,
                 today: str) -> str:
    lines = ["Today is %s (America/New_York)." % today]
    if kind == "job" and job_uid:
        lines.append("This conversation is about ONE Zuper job: #%s, job_uid %s. Read the job "
                     "fresh from Zuper before answering about it." % (job_number or "?", job_uid))
    elif kind == "daily":
        lines.append("This is the owner's DAILY conversation for %s." % today)
    else:
        lines.append("Conversation: %s." % title)
    return "\n".join(lines)


def _excerpt(content) -> str:
    if isinstance(content, list):
        content = " ".join(str(c.get("text", "")) if isinstance(c, dict) else str(c)
                           for c in content)
    s = str(content or "")
    return s if len(s) <= STEP_RESULT_MAX else s[:STEP_RESULT_MAX] + "…"


async def _run(prompt: str, session_id: str | None, note: str) -> TurnResult:
    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        ResultMessage,
        TextBlock,
        ToolResultBlock,
        ToolUseBlock,
        UserMessage,
        query,
    )

    # job_photos saves pictures here; Read may open them (and nothing else outside the
    # workspace). The CLI wants the folder to exist.
    photo_dir = config.photo_dir()
    photo_dir.mkdir(parents=True, exist_ok=True)
    options = ClaudeAgentOptions(
        cwd=str(config.workspace()),
        add_dirs=[str(photo_dir)],
        setting_sources=["project"],               # the workspace's CLAUDE.md and skills
        system_prompt={"type": "preset", "preset": "claude_code", "append": note},
        tools=BUILTIN_TOOLS,
        allowed_tools=BUILTIN_TOOLS + list(tools.TOOL_NAMES),
        permission_mode="dontAsk",
        mcp_servers={tools.SERVER_NAME: tools.server()},
        model=config.model(),
        cli_path=config.cli_path(),
        max_turns=config.MAX_TURNS,
        resume=session_id,
    )
    steps: list[dict] = []
    by_id: dict[str, dict] = {}
    texts: list[str] = []
    out = TurnResult(text="", session_id=session_id)
    async for msg in query(prompt=prompt, options=options):
        if isinstance(msg, AssistantMessage):
            out.model = msg.model or out.model
            for block in msg.content:
                if isinstance(block, ToolUseBlock):
                    step = {"tool": block.name.removeprefix("mcp__%s__" % tools.SERVER_NAME),
                            "input": block.input}
                    by_id[block.id] = step
                    steps.append(step)
                elif isinstance(block, TextBlock) and block.text.strip():
                    texts.append(block.text)
        elif isinstance(msg, UserMessage) and isinstance(msg.content, list):
            for block in msg.content:
                if isinstance(block, ToolResultBlock) and block.tool_use_id in by_id:
                    by_id[block.tool_use_id]["result"] = _excerpt(block.content)
                    if block.is_error:
                        by_id[block.tool_use_id]["error"] = True
        elif isinstance(msg, ResultMessage):
            out.session_id = msg.session_id or out.session_id
            out.cost_usd = msg.total_cost_usd
            out.duration_ms = msg.duration_ms
            out.num_turns = msg.num_turns
            out.text = (msg.result or "").strip() or (texts[-1] if texts else "")
            if msg.is_error:
                out.error = "; ".join(msg.errors or []) or msg.subtype or "error"
    out.steps = steps
    if not out.text and texts:
        out.text = texts[-1]
    return out


def run_turn(prompt: str, session_id: str | None, note: str) -> TurnResult:
    """Blocking: one whole turn (all its tool calls), or a TurnResult carrying the error."""
    try:
        return asyncio.run(asyncio.wait_for(_run(prompt, session_id, note),
                                            config.TURN_TIMEOUT_SECONDS))
    except TimeoutError:
        return TurnResult(text="", session_id=session_id,
                          error="No answer after %d minutes; stopped."
                                % (config.TURN_TIMEOUT_SECONDS // 60))
    except Exception as exc:  # noqa: BLE001 - the CLI failed to start, lost its login, ...
        return TurnResult(text="", session_id=session_id,
                          error="%s: %s" % (type(exc).__name__, str(exc)[:500]))
