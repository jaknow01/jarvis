"""Core layer — the leaf of the import DAG.

Nothing here imports from `domain`, `agents`, or `runtime`; everything above may
import from here. This `__init__` is the public surface: import core primitives from
`lib.core`, not from the submodules directly.
"""

from lib.core.registry import (
    TOOLS_BY_AGENT,
    AGENTS,
    LLM_BY_AGENT,
    tool_ownership,
    agents_decorator,
    agent_enabled,
    llm_usage,
    bind_default_llm_resolver,
)
from lib.core.cache import (
    Cache,
    Ctx,
    GroupScope,
    group_scope_for,
    invalidate_group_scope,
    app_context,
    bind_app_context,
    reset_app_context,
)
from lib.core.llm import bind_model_overrides, reset_model_overrides
from lib.core.model_profiles import (
    ModelProfile,
    AgentModel,
    ModelProfileError,
    active_profile,
    active_profile_name,
    available_profiles,
    load_profile,
    using_profile,
)
from lib.core.agent_tool import agent_as_tool
from lib.core.mcp_client import (
    ADMIN_META_KEY,
    IDENTITY_MODES,
    McpToolset,
    MCP_ADMIN_TOOLSETS,
    MCP_TOOLSETS,
    RemoteTool,
    admin_toolset,
    bind_mcp_principal,
    register_admin_toolset,
    register_mcp_toolset,
    refresh_mcp_toolsets,
    reset_mcp_principal,
    using_mcp_principal,
)
from lib.core.tooling import function_tool
from lib.core.env import environment_preamble, TIMEZONE, BASE_CURRENCY, LOCALE
from lib.core.logger import Logger
from lib.core.settings import Settings, settings, env_flag, env_int, env_str

# Import for side effect: registers the LLM factories into LLM_BY_AGENT so the map is
# populated whenever `core` is imported.
from lib.core import llm as llm  # noqa: F401

__all__ = [
    "TOOLS_BY_AGENT",
    "AGENTS",
    "LLM_BY_AGENT",
    "tool_ownership",
    "agents_decorator",
    "agent_enabled",
    "llm_usage",
    "bind_default_llm_resolver",
    "Cache",
    "Ctx",
    "GroupScope",
    "group_scope_for",
    "invalidate_group_scope",
    "app_context",
    "bind_app_context",
    "reset_app_context",
    "bind_model_overrides",
    "reset_model_overrides",
    "ModelProfile",
    "AgentModel",
    "ModelProfileError",
    "active_profile",
    "active_profile_name",
    "available_profiles",
    "load_profile",
    "using_profile",
    "agent_as_tool",
    "ADMIN_META_KEY",
    "IDENTITY_MODES",
    "McpToolset",
    "MCP_ADMIN_TOOLSETS",
    "MCP_TOOLSETS",
    "admin_toolset",
    "register_admin_toolset",
    "RemoteTool",
    "bind_mcp_principal",
    "reset_mcp_principal",
    "using_mcp_principal",
    "register_mcp_toolset",
    "refresh_mcp_toolsets",
    "function_tool",
    "environment_preamble",
    "TIMEZONE",
    "BASE_CURRENCY",
    "LOCALE",
    "Logger",
    "Settings",
    "settings",
    "env_flag",
    "env_int",
    "env_str",
]
