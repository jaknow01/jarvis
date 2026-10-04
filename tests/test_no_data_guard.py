"""Tests for the coordinator's forced-tool-choice support: the `no_data_needed` escape
tool, the engine guard that replaces the coordinator's answer with a fixed one on a no-data turn, and the
Anthropic `tool_choice` overlay."""
import asyncio

from llama_index.core.tools import FunctionTool

from lib.agents.coordinator.no_data import no_data_needed
from lib.core import Ctx, bind_app_context, reset_app_context
from lib.core.llm import ToolChoiceAnthropic
from lib.runtime.engine import _guard_no_data_turn


def test_no_data_tool_marks_turn():
    turn = Ctx()
    token = bind_app_context(turn)
    try:
        out = asyncio.run(no_data_needed(ctx=None, kind="thanks"))
    finally:
        reset_app_context(token)
    assert turn.no_data_kind == "thanks"
    assert out.startswith("OK")


def test_guard_replaces_answer_when_only_escape_tool_used():
    turn = Ctx()
    turn.no_data_kind = "thanks"
    assert _guard_no_data_turn(turn, "Nie ma za co! WIG20: 3408 pkt (+1,2%)") == "Nie ma za co!"


def test_guard_unknown_kind_gets_generic_answer():
    turn = Ctx()
    turn.no_data_kind = "something_else"
    out = _guard_no_data_turn(turn, "EUR/PLN 4.70")
    assert "4.70" not in out and out


def test_guard_keeps_summary_when_a_subagent_ran():
    turn = Ctx()
    turn.no_data_kind = "thanks"
    turn.subagent_calls.append("finance_agent")
    assert _guard_no_data_turn(turn, "EUR/PLN 4.3775") == "EUR/PLN 4.3775"


def test_guard_keeps_summary_on_normal_turn():
    assert _guard_no_data_turn(Ctx(), "EUR/PLN 4.3775") == "EUR/PLN 4.3775"


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
