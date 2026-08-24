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
from lib.core.cache import Cache, Ctx, app_context, bind_app_context, reset_app_context
from lib.core.agent_tool import agent_as_tool
from lib.core.tooling import function_tool
from lib.core.env import environment_preamble, TIMEZONE, BASE_CURRENCY, LOCALE
from lib.core.logger import Logger

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
    "app_context",
    "bind_app_context",
    "reset_app_context",
    "agent_as_tool",
    "function_tool",
    "environment_preamble",
    "TIMEZONE",
    "BASE_CURRENCY",
    "LOCALE",
    "Logger",
]
