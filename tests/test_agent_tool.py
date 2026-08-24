"""Tests for agent_as_tool(): wrapping a sub-agent as a coordinator FunctionTool."""
from llama_index.core.tools import FunctionTool

from lib.core import agent_as_tool


def test_returns_function_tool_with_name_and_description():
    # The wrapped agent is only used when the tool is *called*; building the tool and
    # inspecting its metadata needs no LLM, so a placeholder object stands in.
    tool = agent_as_tool(object(), "weather_agent", "Checks the weather")
    assert isinstance(tool, FunctionTool)
    assert tool.metadata.name == "weather_agent"
    assert tool.metadata.description == "Checks the weather"


def test_exposes_only_prompt_and_hides_context():
    tool = agent_as_tool(object(), "news_agent", "Summarizes news")
    props = tool.metadata.get_parameters_dict()["properties"]
    # The model sees a single string input; the workflow Context is auto-injected and
    # must not leak into the tool schema.
    assert "prompt" in props
    assert "ctx" not in props


def test_is_async():
    tool = agent_as_tool(object(), "finance_agent", "Financial data")
    assert tool.async_fn is not None
