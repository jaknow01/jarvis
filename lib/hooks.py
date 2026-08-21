"""Run-level lifecycle hooks for observability.

Logs which tools/subagents the network actually invokes during a run, plus handoffs,
so the conversation logs show the full tool-usage trail — not just agent construction.
Because RunHooks are global to the whole run, this captures both the coordinator's
subagent-as-tool calls and the function-tool calls the subagents make internally.
"""

import logging

from agents import RunHooks

logger = logging.getLogger(__name__)

_PREVIEW = 200


class LoggingRunHooks(RunHooks):
    """Emit an INFO line for every tool start/end and every handoff during a run."""

    async def on_tool_start(self, context, agent, tool) -> None:
        logger.info(f"[tool-start] {agent.name} -> {getattr(tool, 'name', tool)}")

    async def on_tool_end(self, context, agent, tool, result) -> None:
        text = result or ""
        preview = text[:_PREVIEW].replace("\n", " ")
        suffix = "..." if len(text) > _PREVIEW else ""
        logger.info(
            f"[tool-end]   {agent.name} <- {getattr(tool, 'name', tool)}: {preview}{suffix}"
        )

    async def on_handoff(self, context, from_agent, to_agent) -> None:
        logger.info(f"[handoff]    {from_agent.name} -> {to_agent.name}")
