"""Central registries for the agent network.

Single source of truth for the three decorator-backed maps used across the codebase.
They are populated by import-time side effects: importing a module that defines
`@tool_ownership`/`@agents_decorator`/`@llm_usage` decorated objects registers them
here. See CLAUDE.md → "Registration patterns".

This module is a leaf of the `core` layer: it may only import from within `core`
(here: `core.env`), never from `domain`, `agents`, or `runtime`.
"""

import logging
import os

from lib.core.env import environment_preamble

logger = logging.getLogger(__name__)


# ---- tools ----
TOOLS_BY_AGENT: dict[str, list] = {}


def tool_ownership(agent_name: str):
    """Register a `@function_tool` as belonging to `agent_name`. Place ABOVE
    `@function_tool` (outermost). An agent picks up its tools via
    `TOOLS_BY_AGENT[name]`."""
    def wrapper(function_tool):
        TOOLS_BY_AGENT.setdefault(agent_name, []).append(function_tool)
        return function_tool
    return wrapper


# ---- agents ----
AGENTS: dict = {}


def agents_decorator(name: str):
    """Register an agent factory under `name`. The wrapped factory, when called,
    prepends the shared environment block (date/time, timezone, currency, locale)
    to string instructions so temporal/locale awareness reaches every agent."""
    def wrapper(func):
        def build(*args, **kwargs):
            agent = func(*args, **kwargs)
            if isinstance(agent.instructions, str):
                agent.instructions = environment_preamble() + agent.instructions
            return agent
        AGENTS[name] = build
        return build
    return wrapper


def agent_enabled(name: str) -> bool:
    """Whether a subagent is exposed to the coordinator. Controlled by the env var
    AGENT_<NAME>_ENABLED — enabled by default; set to a falsy value (0/false/no/off)
    to hide it as a coordinator tool. Read at coordinator-build time."""
    raw = os.getenv(f"AGENT_{name.upper()}_ENABLED")
    if raw is None or raw.strip() == "":
        return True
    return raw.strip().lower() not in ("0", "false", "no", "off")


# ---- llm ----
LLM_BY_AGENT: dict = {}


def llm_usage(agent_names: list[str]):
    """Map each agent name to a model-factory returning {"model_name", "settings"}.
    Agents read it via `LLM_BY_AGENT[name]()`."""
    def wrapper(func):
        for agent in agent_names:
            LLM_BY_AGENT[agent] = func
        return func
    return wrapper
