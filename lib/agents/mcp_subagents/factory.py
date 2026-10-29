"""Build a LlamaIndex sub-agent from an MCP server's tools and tool guide."""

from __future__ import annotations

import logging
from typing import Optional

from llama_index.core.agent.workflow import FunctionAgent

import os

from lib.core import (AGENTS, LLM_BY_AGENT, McpToolset, admin_toolset, agents_decorator, env_flag,
                      register_admin_toolset, register_mcp_toolset)
from lib.agents.mcp_subagents.config import SubagentConfig, load_subagents

logger = logging.getLogger(__name__)

# Tools a server offers to people's clients but not to Jarvis's sub-agents. Admin-only tools
# need no entry here: the server hides them from non-admin keys and the client drops anything
# marked admin-only (lib/core/mcp_client.py), so a new admin tool can never leak to a model.
EXCLUDED_TOOLS = frozenset({"whoami"})

MCP_SUBAGENTS: dict[str, SubagentConfig] = {}


def toolset_for(name: str) -> Optional[McpToolset]:
    from lib.core import MCP_TOOLSETS

    return MCP_TOOLSETS.get(name)


def admin_toolset_for(name: str) -> Optional[McpToolset]:
    """The toolset with ``name``'s admin key (for `/` commands), or None without one."""
    return admin_toolset(name)


def admin_command_target(command: str) -> Optional[SubagentConfig]:
    """The sub-agent whose server's admin tools `/<command>` calls."""
    return next((c for c in MCP_SUBAGENTS.values() if c.admin_command == command), None)


def is_sensitive(name: str) -> bool:
    cfg = MCP_SUBAGENTS.get(name)
    return bool(cfg and cfg.sensitive)


def _tracing_active() -> bool:
    return env_flag("TRACING_ENABLED", True) and bool((os.getenv("MLFLOW_TRACKING_URI") or "").strip())


def sensitive_allowed(name: str) -> bool:
    """A sensitive sub-agent runs only when explicitly switched on (``AGENT_<NAME>_ENABLED``
    truthy — off by default, unlike other agents) and while tracing is off, so its data can't
    land in traces. Checked on top of the principal's roster."""
    raw = (os.getenv(f"AGENT_{name.upper()}_ENABLED") or "").strip().lower()
    if raw not in ("1", "true", "yes", "on"):
        return False
    if _tracing_active():
        logger.warning("Sensitive sub-agent %s left out: tracing is on", name)
        return False
    return True


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
                             identity=cfg.identity, budgets=cfg.max_calls_per_turn, uds_env=cfg.uds_env)
        if cfg.admin_key_env:
            register_admin_toolset(cfg.name, url_env=cfg.url_env, key_env=cfg.admin_key_env,
                                   identity=cfg.identity, uds_env=cfg.uds_env)
        if cfg.name not in AGENTS:
            agents_decorator(cfg.name)(lambda _n=cfg.name: create_mcp_subagent(_n))
    if configs:
        logger.info("MCP sub-agents configured: %s", ", ".join(c.name for c in configs))
    return [c.name for c in configs]


def mcp_subagent_catalog() -> list[tuple[str, callable, str]]:
    """(name, factory, description) for every configured sub-agent, in config order."""
    return [(name, AGENTS[name], cfg.description) for name, cfg in MCP_SUBAGENTS.items()]
