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
)
from lib.core.cache import Cache, Ctx
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
    "Cache",
    "Ctx",
    "environment_preamble",
    "TIMEZONE",
    "BASE_CURRENCY",
    "LOCALE",
    "Logger",
]
