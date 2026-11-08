"""A scheduled turn: the task reaches the coordinator delimited after the scheduled-job marker,
and is kept in the conversation history with a prefix (so later turns don't read it as typed)."""

import asyncio

import pytest

from lib.core import Ctx, app_context
from lib.runtime import engine


class _Handler:
    def __init__(self, answer):
        self._answer = answer

    def __await__(self):
        async def done():
            return self._answer
        return done().__await__()

    async def stream_events(self):
        if False:
            yield None


@pytest.mark.parametrize("origin", ["system", "user"])
def test_scheduled_task_is_delimited_and_prefixed_in_history(monkeypatch, origin):
    seen, recorded = [], []

    class Coordinator:
        def run(self, user_msg, **kwargs):
            seen.append(user_msg)
            app_context().no_data_kind = "reminder"
            return _Handler("Przypominam o zadaniach na studia.")

    class Composer:
        async def run(self, **kwargs):
            return "Przypominam o zadaniach na studia."

    async def no_history(_cid):
        return []

    async def no_refresh():
        return None

    monkeypatch.setattr(engine, "create_coordinator_agent", lambda profile, allow_sensitive=True: Coordinator())
    monkeypatch.setattr(engine, "create_composer_agent", lambda: Composer())
    monkeypatch.setattr(engine, "build_chat_history", no_history)
    monkeypatch.setattr(engine, "refresh_mcp_toolsets", no_refresh)
    monkeypatch.setattr(engine, "schedule_summary_fold", lambda cid: None)
    monkeypatch.setattr(engine.conversations, "record_turn",
                        lambda cid, user, assistant, trace_id="": recorded.append((user, assistant)))

    task = "Przypomnij mi o zadaniach na studia."
    reply = asyncio.run(engine.handle_message("test:1", task, Ctx(cache=None), origin=origin))

    assert reply == "Przypominam o zadaniach na studia."  # the coordinator's own text, no fixed override
    ((user, _),) = recorded
    if origin == "system":
        assert seen == [engine._SYSTEM_PROMPT_MARKER + "Treść zadania: «" + task + "»"]
        assert user == "[zadanie z harmonogramu] " + task
    else:
        assert seen == [task] and user == task
