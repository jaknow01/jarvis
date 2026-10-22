"""Build a LlamaIndex sub-agent from an MCP server's tools and tool guide."""

from __future__ import annotations

import logging
from typing import Optional

from llama_index.core.agent.workflow import FunctionAgent

from lib.core import AGENTS, LLM_BY_AGENT, McpToolset, agents_decorator, register_mcp_toolset
from lib.agents.mcp_subagents.config import SubagentConfig, load_subagents

logger = logging.getLogger(__name__)

# Tools a server offers to people's clients but not to Jarvis's sub-agents.
EXCLUDED_TOOLS = frozenset({"whoami", "list_issued_keys", "revoke_issued_key"})

MCP_SUBAGENTS: dict[str, SubagentConfig] = {}


def toolset_for(name: str) -> Optional[McpToolset]:
    from lib.core import MCP_TOOLSETS

    return MCP_TOOLSETS.get(name)


def _system_prompt(cfg: SubagentConfig, toolset: McpToolset) -> str:
    parts = [f"You are Jarvis's {cfg.name} specialist: {cfg.description}"]
    if toolset.instructions:
        parts.append(toolset.instructions.strip())
    if cfg.persona:
        parts.append(cfg.persona)
    return "\n\n".join(parts)


def create_mcp_subagent(name: str) -> Optional[FunctionAgent]:
    """The sub-agent for ``name``, or None while its server has never been listed."""
    cfg = MCP_SUBAGENTS[name]
    toolset = toolset_for(name)
    tools = toolset.tools() if toolset else None
    if not tools:
        return None
    return FunctionAgent(name=name, description=cfg.description, system_prompt=_system_prompt(cfg, toolset),
                         tools=tools, llm=LLM_BY_AGENT[name]())


def register_mcp_subagents(configs: Optional[list[SubagentConfig]] = None) -> list[str]:
    """Register toolsets + agent factories for every configured sub-agent (idempotent)."""
    configs = load_subagents() if configs is None else configs
    for cfg in configs:
        MCP_SUBAGENTS[cfg.name] = cfg
        register_mcp_toolset(cfg.name, url_env=cfg.url_env, key_env=cfg.key_env, exclude=EXCLUDED_TOOLS,
                             identity=cfg.identity, budgets=cfg.max_calls_per_turn)
        if cfg.name not in AGENTS:
            agents_decorator(cfg.name)(lambda _n=cfg.name: create_mcp_subagent(_n))
    if configs:
        logger.info("MCP sub-agents configured: %s", ", ".join(c.name for c in configs))
    return [c.name for c in configs]


def mcp_subagent_catalog() -> list[tuple[str, callable, str]]:
    """(name, factory, description) for every configured sub-agent, in config order."""
    return [(name, AGENTS[name], cfg.description) for name, cfg in MCP_SUBAGENTS.items()]
