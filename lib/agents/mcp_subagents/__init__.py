"""Tool sub-agents defined by configuration and backed by MCP servers (docs/MCP.md).

Importing this package reads ``config/subagents.yaml`` (path: ``SUBAGENTS_CONFIG``),
registers one :class:`~lib.core.McpToolset` per entry and one agent factory in ``AGENTS``.
"""

from lib.agents.mcp_subagents.config import (
    SubagentConfig,
    SubagentsConfigError,
    load_subagents,
    subagents_path,
)
from lib.agents.mcp_subagents.factory import (
    MCP_SUBAGENTS,
    create_mcp_subagent,
    mcp_subagent_catalog,
    register_mcp_subagents,
    toolset_for,
)

register_mcp_subagents()

__all__ = [
    "MCP_SUBAGENTS",
    "SubagentConfig",
    "SubagentsConfigError",
    "create_mcp_subagent",
    "load_subagents",
    "mcp_subagent_catalog",
    "register_mcp_subagents",
    "subagents_path",
    "toolset_for",
]
