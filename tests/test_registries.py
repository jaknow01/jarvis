"""Registry wiring: harness agents keep in-repo tools; tool sub-agents come from config."""
import lib.agents  # noqa: F401  (registration side effects)
from lib.agents import MCP_SUBAGENTS
from lib.core import AGENTS, LLM_BY_AGENT, TOOLS_BY_AGENT

TOOL_SUBAGENTS = {"iot_operator", "weather_agent", "finance_agent", "maps_agent", "news_agent",
                  "fpl_agent", "math_agent", "calendar_agent"}


def test_tool_subagents_are_configured_not_in_process():
    assert TOOL_SUBAGENTS <= set(MCP_SUBAGENTS)
    for name in TOOL_SUBAGENTS:
        assert name in AGENTS, name
        assert name not in TOOLS_BY_AGENT, f"{name} still has in-process tools"


def test_harness_agents_keep_their_tools():
    assert {"memory_operator", "scheduler_agent"} <= set(TOOLS_BY_AGENT)


def test_every_agent_has_a_model_mapping():
    for agent in [*TOOLS_BY_AGENT, *MCP_SUBAGENTS]:
        assert agent in LLM_BY_AGENT, f"{agent} has no model mapping in LLM_BY_AGENT"
    assert "coordinator" in LLM_BY_AGENT


def test_composer_has_a_model_mapping():
    assert "composer" in LLM_BY_AGENT
