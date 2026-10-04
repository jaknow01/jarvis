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
    to the agent's system prompt so temporal/locale awareness reaches every agent.

    Under LlamaIndex an agent's string instructions live on `system_prompt`
    (the old OpenAI Agents SDK used `instructions`)."""
    def wrapper(func):
        def build(*args, **kwargs):
            agent = func(*args, **kwargs)
            if isinstance(getattr(agent, "system_prompt", None), str):
                agent.system_prompt = environment_preamble() + agent.system_prompt
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
class _LLMRegistry(dict):
    """Maps an agent name to a zero-arg LLM factory (`LLM_BY_AGENT[name]()`).

    Explicit `@llm_usage` entries are stored directly and win. For any agent WITHOUT
    an explicit entry, a config-driven default resolver — bound once by
    `lib.core.llm` via `bind_default_llm_resolver` — is used, so every agent resolves
    a model from the active model profile (config/models/*.yaml) without needing a hand-written
    factory. This is the "least surprising hook" chosen in the design's Open Questions:
    the registry stays the single public surface and no agent lookup ever misses.
    """

    _default_resolver = None

    def __missing__(self, name):
        resolver = _LLMRegistry._default_resolver
        if resolver is None:
            raise KeyError(name)
        return lambda: resolver(name)

    def __contains__(self, key):
        # Membership is True for any agent once a default resolver is bound — callers
        # (and the registry symmetry tests) treat every agent as having a model.
        return super().__contains__(key) or _LLMRegistry._default_resolver is not None


LLM_BY_AGENT: _LLMRegistry = _LLMRegistry()


def bind_default_llm_resolver(resolver) -> None:
    """Bind the config-driven fallback used for agents without an explicit `@llm_usage`.
    `resolver` takes an agent name and returns a ready LlamaIndex LLM. Called once, at
    import time, from `lib.core.llm`."""
    _LLMRegistry._default_resolver = resolver


def llm_usage(agent_names: list[str]):
    """Map each agent name to a model-factory returning a ready LlamaIndex LLM. This is
    an explicit, code-level override; agents without one fall back to the config-driven
    resolver bound via `bind_default_llm_resolver`. Agents read it via
    `LLM_BY_AGENT[name]()`."""
    def wrapper(func):
        for agent in agent_names:
            LLM_BY_AGENT[agent] = func
        return func
    return wrapper
