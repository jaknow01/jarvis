"""Config-defined MCP sub-agents: config validation, factory, identity forwarding, call
budgets, error handling and MCP-backed watch sources — no servers involved."""

import asyncio
import json

import pytest

from lib.agents import MCP_SUBAGENTS, create_mcp_subagent, toolset_for
from lib.agents.mcp_subagents.config import SubagentsConfigError, parse_subagents
from lib.agents.scheduler_agent.watch_sources import _mcp_fetcher, known_sources
from lib.core import Ctx, McpToolset, bind_app_context, reset_app_context, using_mcp_principal
from tests.conftest_mcp import fake_tool, listed_subagents


def test_shipped_config_declares_identity_modes():
    assert MCP_SUBAGENTS["maps_agent"].identity == "group"
    assert MCP_SUBAGENTS["iot_operator"].identity == "group"
    assert MCP_SUBAGENTS["calendar_agent"].identity == "user"
    assert MCP_SUBAGENTS["fpl_agent"].identity == "none"


@pytest.mark.parametrize("entry, msg", [
    ({"url_env": "U", "key_env": "K"}, "description"),
    ({"description": "d", "url_env": "U", "key_env": "K", "identity": "everyone"}, "identity"),
    ({"description": "d", "url_env": "U", "key_env": "K", "max_calls_per_turn": {"x": 0}}, "positive"),
    ({"description": "d", "url_env": "U", "key_env": "K", "colour": "red"}, "unknown keys"),
])
def test_config_validation(entry, msg):
    with pytest.raises(SubagentsConfigError, match=msg):
        parse_subagents({"subagents": {"x": entry}})


def test_factory_builds_from_listing_and_server_guide():
    assert create_mcp_subagent("news_agent") is None or toolset_for("news_agent").tools()
    with listed_subagents(["news_agent"]):
        agent = create_mcp_subagent("news_agent")
    assert agent is not None
    assert [t.metadata.name for t in agent.tools] == ["news_agent_tool"]
    assert "Guide for news_agent." in agent.system_prompt
    assert "news reporter" in agent.system_prompt  # persona from config


def test_no_listing_means_no_subagent():
    ts = toolset_for("math_agent")
    saved, ts._tools = ts._tools, None
    try:
        assert create_mcp_subagent("math_agent") is None
    finally:
        ts._tools = saved


@pytest.mark.parametrize("identity, expected", [("none", None), ("user", "ala"), ("group", "dom")])
def test_on_behalf_of_header_per_identity_mode(monkeypatch, identity, expected):
    monkeypatch.setenv("T_KEY", "jvk_test")
    ts = McpToolset("t", url_env="T_URL", key_env="T_KEY", identity=identity)
    with using_mcp_principal("ala", "dom"):
        headers = ts._headers()
    assert headers["Authorization"] == "Bearer jvk_test"
    assert headers.get("X-On-Behalf-Of") == expected
    assert "X-On-Behalf-Of" not in ts._headers()  # nothing bound outside a turn


def test_budget_per_turn():
    calls = []
    ts = McpToolset("t", url_env="T_URL", key_env="T_KEY", budgets={"read_page": 2})
    ts.load_snapshot([fake_tool("read_page")], None, call=lambda name, args: calls.append(name) or "ok")
    tool = ts.tools()[0]
    token = bind_app_context(Ctx())
    try:
        outs = [asyncio.run(tool.acall()).raw_output for _ in range(3)]
    finally:
        reset_app_context(token)
    assert outs[:2] == ["ok", "ok"] and outs[2].startswith("ERROR: the read_page budget")
    assert calls == ["read_page", "read_page"]


def test_call_failure_is_an_error_string_without_key(monkeypatch):
    ts = McpToolset("t", url_env="T_URL", key_env="T_KEY")

    async def boom(*a, **k):
        raise ConnectionError("cannot reach http://x/mcp/t/jvk_secretkey")

    monkeypatch.setattr(ts, "_call_once", boom)
    out = asyncio.run(ts.call("anything", {}))
    assert out.startswith("ERROR: the t tool server is unavailable") and "jvk_secretkey" not in out


def test_watch_sources_keep_their_ids():
    assert {"finance.fx", "fpl.live", "weather.current", "news.search", "maps.route"} <= set(known_sources())


def test_watch_fetcher_calls_tool_over_mcp_and_filters_params(monkeypatch):
    ts = toolset_for("finance_agent")
    seen = {}

    async def fake_call(name, args):
        seen["name"], seen["args"] = name, args
        return json.dumps({"rate": 4.1})

    async def no_refresh(*a, **k):
        return None

    with listed_subagents(["finance_agent"]):
        ts._remote = [fake_tool("get_exchange_rate", props={"foreign_currency": {}, "base_currency": {}})]
        monkeypatch.setattr(ts, "call", fake_call)
        monkeypatch.setattr(ts, "refresh", no_refresh)
        data = asyncio.run(_mcp_fetcher("finance_agent", "get_exchange_rate")(
            {"foreign_currency": "USD", "league_id": 7}))
    assert data == {"rate": 4.1}
    assert seen == {"name": "get_exchange_rate", "args": {"foreign_currency": "USD"}}


def test_watch_fetcher_maps_tool_errors_to_error_dict(monkeypatch):
    ts = toolset_for("weather_agent")

    async def fail(name, args):
        return "ERROR: the weather_agent tool server is unavailable (timeout)."

    async def no_refresh(*a, **k):
        return None

    monkeypatch.setattr(ts, "call", fail)
    monkeypatch.setattr(ts, "refresh", no_refresh)
    data = asyncio.run(_mcp_fetcher("weather_agent", "current_weather")({}))
    assert "Error" in data
