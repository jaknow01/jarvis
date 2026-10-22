"""agents layer — the coordinator and every specialized subagent.

Public surface for the runtime: import `create_coordinator_agent` and the `AGENTS`
registry from here. Importing this package imports every feature subpackage, which runs
their `@tool_ownership` / `@agents_decorator` side effects and fully populates the
`TOOLS_BY_AGENT` / `AGENTS` / `LLM_BY_AGENT` registries in `lib.core`.

Layering (imports go up only): runtime -> agents -> {domain, core}.
"""

from lib.core import AGENTS, agent_enabled

# Import every feature package for its registration side effects. The coordinator also
# imports the leaf agents, but listing them here keeps registration explicit and
# independent of the coordinator's internals.
from lib.agents.iot_operator import create_iot_agent
from lib.agents.weather_agent import create_weather_agent
from lib.agents.finance_agent import create_finance_agent
from lib.agents.news_agent import create_news_agent
from lib.agents.maps_agent import create_maps_agent
from lib.agents.memory_operator import create_memory_agent
from lib.agents.scheduler_agent import create_scheduler_agent
from lib.agents.fpl_agent import (
    create_fpl_agent,
    FplIdentity,
    current_fpl_identity,
    using_fpl_identity,
)
from lib.agents.math_agent import create_math_agent
from lib.agents.calendar_agent import create_calendar_agent
from lib.agents.composer import create_composer_agent
from lib.agents.coordinator import create_coordinator_agent, subagent_names

__all__ = [
    "AGENTS",
    "agent_enabled",
    "create_coordinator_agent",
    "subagent_names",
    "create_composer_agent",
    "create_iot_agent",
    "create_weather_agent",
    "create_finance_agent",
    "create_news_agent",
    "create_maps_agent",
    "create_memory_agent",
    "create_scheduler_agent",
    "create_fpl_agent",
    "FplIdentity",
    "current_fpl_identity",
    "using_fpl_identity",
    "create_math_agent",
    "create_calendar_agent",
]
