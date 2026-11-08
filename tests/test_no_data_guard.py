"""Tests for the coordinator's forced-tool-choice support: the `no_data_needed` escape
tool, the engine guard that replaces the coordinator's answer with a fixed one on a no-data turn, and the
Anthropic `tool_choice` overlay."""
import asyncio

import pytest

from llama_index.core.tools import FunctionTool

from lib.agents.coordinator.no_data import no_data_needed
from lib.core import Ctx, bind_app_context, reset_app_context
from lib.core.llm import ToolChoiceAnthropic
from lib.runtime.engine import _guard_no_data_turn, scheduled_reminder_text


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


# ── scheduled turns: a plain reminder needs no data, so the escape tool is expected ──


def test_scheduled_no_data_turn_delivers_the_task_not_jasne():
    turn = Ctx()
    turn.no_data_kind = "acknowledgement"
    out = _guard_no_data_turn(turn, "Jasne.", scheduled_task="Przypomnij mi o zadaniach na platformie na studia.")
    assert out == "Przypominam o zadaniach na platformie na studia."


def test_scheduled_turn_with_subagent_keeps_the_answer():
    turn = Ctx()
    turn.no_data_kind = "acknowledgement"
    turn.subagent_calls.append("weather_agent")
    assert _guard_no_data_turn(turn, "Dziś 12°C.", scheduled_task="Podaj pogodę.") == "Dziś 12°C."


def test_scheduled_turn_without_escape_tool_keeps_the_answer():
    assert _guard_no_data_turn(Ctx(), "Pamiętaj o studiach!", scheduled_task="Przypomnij mi o studiach.") \
        == "Pamiętaj o studiach!"


@pytest.mark.parametrize("task, expected", [
    ("Przypomnij mi o spotkaniu z Anią.", "Przypominam o spotkaniu z Anią."),
    ("przypomnij mi, że jutro jest egzamin", "Przypominam, że jutro jest egzamin"),
    ("Zadania na platformie na studia", "Przypomnienie: Zadania na platformie na studia"),
    ("Przypomnij mi", "Przypomnienie: Przypomnij mi"),
])
def test_scheduled_reminder_text(task, expected):
    assert scheduled_reminder_text(task) == expected
