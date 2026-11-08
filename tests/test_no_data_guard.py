"""Tests for the coordinator's forced-tool-choice support: the `no_data_needed` escape
tool (the coordinator then writes the reply itself — there are no fixed answers), the
scheduled-turn instructions, and the Anthropic `tool_choice` overlay."""
import asyncio

from llama_index.core.tools import FunctionTool

from lib.agents.coordinator.no_data import no_data_needed
from lib.core import Ctx, bind_app_context, reset_app_context
from lib.core.llm import ToolChoiceAnthropic


def test_no_data_tool_marks_turn():
    turn = Ctx()
    token = bind_app_context(turn)
    try:
        out = asyncio.run(no_data_needed(ctx=None, kind="thanks"))
    finally:
        reset_app_context(token)
    assert turn.no_data_kind == "thanks"
    assert out.startswith("OK")


def _anthropic_prepare(tool_choice):
    llm = ToolChoiceAnthropic(model="claude-sonnet-4-20250514", api_key="test")

    async def ping(prompt: str) -> str:
        """Ping."""
        return prompt

    tool = FunctionTool.from_defaults(async_fn=ping)
    return llm._prepare_chat_with_tools([tool], chat_history=[], tool_choice=tool_choice)


def test_anthropic_overlay_maps_required_to_any():
    assert _anthropic_prepare("required")["tool_choice"]["type"] == "any"


def test_anthropic_overlay_auto_and_named():
    assert _anthropic_prepare(None)["tool_choice"]["type"] == "auto"
    assert _anthropic_prepare("auto")["tool_choice"]["type"] == "auto"
    assert _anthropic_prepare("ping")["tool_choice"] == {"type": "tool", "name": "ping"}


# ── no fixed answers: the coordinator writes the reply, guided by its prompt ──


def test_reminder_is_a_no_data_kind():
    turn = Ctx()
    token = bind_app_context(turn)
    try:
        out = asyncio.run(no_data_needed(ctx=None, kind="reminder"))
    finally:
        reset_app_context(token)
    assert turn.no_data_kind == "reminder" and "reminder text itself" in out


def test_engine_has_no_fixed_answer_override():
    from lib.runtime import engine

    assert not hasattr(engine, "_guard_no_data_turn") and not hasattr(engine, "_NO_DATA_ANSWERS")


def test_scheduled_turn_marker_says_job_exists_and_to_write_the_reminder():
    from lib.runtime.engine import _SYSTEM_PROMPT_MARKER as marker

    assert "JUŻ ISTNIEJE" in marker and "scheduler_agent" in marker
    assert "no_data_needed" in marker and "Przypominam" in marker and "Jasne." in marker


def test_coordinator_prompt_covers_scheduled_reminders():
    from lib.agents import create_coordinator_agent
    from tests.conftest_mcp import listed_subagents

    with listed_subagents():
        prompt = create_coordinator_agent().system_prompt
    assert "scheduled plain reminder" in prompt and "never just 'Jasne.'" in prompt
