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


# ---- timeout / graceful failure ---------------------------------------------------
import asyncio


class _FakeRunHandler:
    """Awaitable stand-in for a WorkflowHandler with `cancel_run()`."""

    def __init__(self, delay: float, result="done", error: Exception | None = None):
        self._delay, self._result, self._error = delay, result, error
        self.cancelled = False

    def __await__(self):
        async def _run():
            await asyncio.sleep(self._delay)
            if self._error:
                raise self._error
            return self._result
        return _run().__await__()

    async def cancel_run(self):
        self.cancelled = True


class _FakeAgent:
    def __init__(self, handler):
        self.handler = handler

    def run(self, user_msg, ctx):
        return self.handler


def _call(tool, monkeypatch, timeout: str):
    monkeypatch.setenv("SUBAGENT_TIMEOUT_SECONDS", timeout)
    # Context construction needs a real workflow; the fake agent ignores it.
    monkeypatch.setattr("lib.core.agent_tool.Context", lambda agent: None)
    return asyncio.run(tool.async_fn(ctx=None, prompt="hi"))


def test_returns_result_within_timeout(monkeypatch):
    handler = _FakeRunHandler(delay=0.01, result="ok")
    out = _call(agent_as_tool(_FakeAgent(handler), "finance_agent", "d"), monkeypatch, "5")
    assert out == "ok"
    assert handler.cancelled is False


def test_timeout_cancels_run_and_returns_guidance(monkeypatch):
    monkeypatch.setattr("lib.core.agent_tool.subagent_timeout_seconds", lambda: 0.05)
    monkeypatch.setattr("lib.core.agent_tool.Context", lambda agent: None)
    handler = _FakeRunHandler(delay=1.0)
    tool = agent_as_tool(_FakeAgent(handler), "finance_agent", "d")
    out = asyncio.run(tool.async_fn(ctx=None, prompt="hi"))
    assert out.startswith("ERROR:")
    assert "finance_agent" in out and "Do NOT call it again" in out
    assert handler.cancelled is True


def test_failure_is_returned_not_raised(monkeypatch):
    handler = _FakeRunHandler(delay=0, error=RuntimeError("boom"))
    out = _call(agent_as_tool(_FakeAgent(handler), "fpl_agent", "d"), monkeypatch, "5")
    assert out.startswith("ERROR:") and "boom" in out
