"""agents layer — the coordinator, the composer, the harness sub-agents (memory, scheduler)
and the tool sub-agents defined in ``config/subagents.yaml`` and backed by MCP servers.

Public surface for the runtime: import `create_coordinator_agent` and the `AGENTS`
registry from here. Importing this package registers every agent factory (the harness
packages by decorator side effect, the MCP sub-agents from configuration).

Layering (imports go up only): runtime -> agents -> {domain, core}.
"""

from lib.core import AGENTS, agent_enabled

from lib.agents.memory_operator import create_memory_agent
from lib.agents.scheduler_agent import create_scheduler_agent
from lib.agents.mcp_subagents import (
    MCP_SUBAGENTS,
    create_mcp_subagent,
    mcp_subagent_catalog,
    toolset_for,
)
from lib.agents.composer import create_composer_agent
from lib.agents.coordinator import create_coordinator_agent, subagent_names

__all__ = [
    "AGENTS",
    "MCP_SUBAGENTS",
    "agent_enabled",
    "create_coordinator_agent",
    "create_composer_agent",
    "create_mcp_subagent",
    "create_memory_agent",
    "create_scheduler_agent",
    "mcp_subagent_catalog",
    "subagent_names",
    "toolset_for",
]
