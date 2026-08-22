"""Tests for the decorator-backed registries that wire tools/models to agents."""
from lib import tools, llm


def _tool_names(agent_name):
    return {getattr(t, "name", "") for t in tools.TOOLS_BY_AGENT[agent_name]}


def test_expected_agents_have_tools_registered():
    registered = set(tools.TOOLS_BY_AGENT.keys())
    for agent in {"iot_operator", "maps_agent", "weather_agent", "finance_agent", "news_agent"}:
        assert agent in registered


def test_finance_agent_has_fx_and_stock_tools():
    names = _tool_names("finance_agent")
    assert "get_exchange_rate" in names
    assert "get_stock_quote" in names


def test_news_agent_has_search_tool():
    assert "search_news" in _tool_names("news_agent")


def test_fpl_agent_has_its_tools():
    names = _tool_names("fpl_agent")
    assert {
        "get_fpl_fixtures",
        "get_pl_teams",
        "get_my_fpl_squad",
        "get_my_fpl_leagues",
        "get_fpl_league_standings",
    } <= names


def test_every_agent_has_a_model_mapping():
    for agent in tools.TOOLS_BY_AGENT:
        assert agent in llm.LLM_BY_AGENT, f"{agent} has no model mapping in LLM_BY_AGENT"
    assert "coordinator" in llm.LLM_BY_AGENT


def test_composer_has_a_model_mapping():
    # The composer is a tool-less handoff target, so it won't appear in TOOLS_BY_AGENT;
    # it still needs a model mapping to be constructible.
    assert "composer" in llm.LLM_BY_AGENT
