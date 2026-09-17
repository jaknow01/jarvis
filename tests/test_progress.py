"""Tests for the interim "still working" progress ping.

Covers the pure presentation/config layer (`lib/runtime/progress.py`) and the engine's
orchestration helper `_gather_with_progress_ping` — the latter with a fake coordinator
handler so the timing behavior (fires when slow, silent when fast, carries the collected
subagent labels) is exercised on the real code path, no network or LLM involved.
"""

import asyncio

from llama_index.core.agent.workflow import ToolCallResult
from llama_index.core.tools import ToolOutput

from lib.runtime import progress
from lib.runtime.engine import _gather_with_progress_ping


# ---- pure helpers ----------------------------------------------------------------

def test_pl_join_variants():
    assert progress.pl_join([]) == ""
    assert progress.pl_join(["pogodę"]) == "pogodę"
    assert progress.pl_join(["pogodę", "trasę"]) == "pogodę i trasę"
    assert progress.pl_join(["pogodę", "trasę", "newsy"]) == "pogodę, trasę i newsy"


def test_progress_message_with_and_without_done():
    assert progress.progress_message([]) == "Chwila, jeszcze nad tym pracuję…"
    msg = progress.progress_message(["pogodę", "trasę"])
    assert "sprawdziłem już pogodę i trasę" in msg


def test_label_for_known_and_unknown():
    assert progress.label_for("weather_agent") == "pogodę"
    assert progress.label_for("nonexistent_agent") is None


def test_ping_enabled_default_and_falsy(monkeypatch):
    monkeypatch.delenv("PROGRESS_PING_ENABLED", raising=False)
    assert progress.ping_enabled() is True
    for val in ("0", "false", "no", "off", "disabled"):
        monkeypatch.setenv("PROGRESS_PING_ENABLED", val)
        assert progress.ping_enabled() is False
    monkeypatch.setenv("PROGRESS_PING_ENABLED", "1")
    assert progress.ping_enabled() is True


def test_ping_after_seconds_default_and_override(monkeypatch):
    monkeypatch.delenv("PROGRESS_PING_AFTER_SECONDS", raising=False)
    assert progress.ping_after_seconds() == 20.0
    monkeypatch.setenv("PROGRESS_PING_AFTER_SECONDS", "2.5")
    assert progress.ping_after_seconds() == 2.5
    monkeypatch.setenv("PROGRESS_PING_AFTER_SECONDS", "not-a-number")
    assert progress.ping_after_seconds() == 20.0


# ---- engine orchestration --------------------------------------------------------

def _tool_result(name: str) -> ToolCallResult:
    return ToolCallResult(
        tool_name=name,
        tool_kwargs={},
        tool_id=name,
        tool_output=ToolOutput(content="x", tool_name=name, raw_input={}, raw_output="x"),
        return_direct=False,
    )


class _FakeHandler:
    """Stands in for a LlamaIndex WorkflowHandler: awaitable + `stream_events()`.

    `run_delay` is how long the coordinator "takes"; `events` is a list of
    (at_seconds, tool_name) emitted on the stream before completion.
    """

    def __init__(self, result: str, run_delay: float, events):
        self._result = result
        self._run_delay = run_delay
        self._events = events

    def __await__(self):
        async def _run():
            await asyncio.sleep(self._run_delay)
            return self._result
        return _run().__await__()

    async def stream_events(self):
        for at, name in self._events:
            await asyncio.sleep(at)
            yield _tool_result(name)


def test_ping_fires_when_slow_with_collected_labels(monkeypatch):
    monkeypatch.setenv("PROGRESS_PING_AFTER_SECONDS", "0.05")
    sent: list[str] = []

    async def notify(msg: str) -> None:
        sent.append(msg)

    async def scenario():
        # weather returns before the 0.05s ping threshold; the run finishes well after.
        handler = _FakeHandler("gathered", run_delay=0.2, events=[(0.01, "weather_agent")])
        return await _gather_with_progress_ping(handler, notify, want_ping=True)

    result = asyncio.run(scenario())
    assert result == "gathered"
    assert len(sent) == 1
    assert "sprawdziłem już pogodę" in sent[0]


def test_no_ping_when_turn_finishes_before_timeout(monkeypatch):
    monkeypatch.setenv("PROGRESS_PING_AFTER_SECONDS", "0.3")
    sent: list[str] = []

    async def notify(msg: str) -> None:
        sent.append(msg)

    async def scenario():
        handler = _FakeHandler("fast", run_delay=0.02, events=[])
        result = await _gather_with_progress_ping(handler, notify, want_ping=True)
        # Give any stray scheduled ping a chance to (wrongly) fire before asserting.
        await asyncio.sleep(0.35)
        return result

    result = asyncio.run(scenario())
    assert result == "fast"
    assert sent == []


def test_want_ping_false_skips_notify(monkeypatch):
    monkeypatch.setenv("PROGRESS_PING_AFTER_SECONDS", "0.01")
    sent: list[str] = []

    async def notify(msg: str) -> None:
        sent.append(msg)

    async def scenario():
        handler = _FakeHandler("plain", run_delay=0.1, events=[(0.005, "weather_agent")])
        result = await _gather_with_progress_ping(handler, notify, want_ping=False)
        await asyncio.sleep(0.05)
        return result

    result = asyncio.run(scenario())
    assert result == "plain"
    assert sent == []


class _SlowCancellableHandler(_FakeHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.cancelled = False

    async def cancel_run(self):
        self.cancelled = True


def test_coordinator_timeout_returns_partial_results(monkeypatch):
    async def notify(msg: str) -> None:
        pass

    async def scenario():
        handler = _SlowCancellableHandler(
            "never", run_delay=5.0, events=[(0.01, "weather_agent")]
        )
        # Fractional timeout: wait_for accepts floats, the env helper only ints.
        result = await _gather_with_progress_ping(handler, notify, want_ping=False, timeout=0.1)
        return handler, result

    handler, result = asyncio.run(scenario())
    assert handler.cancelled is True
    assert "przekroczyło limit" in result
    assert "[weather_agent]" in result


def test_coordinator_finishes_before_timeout(monkeypatch):
    async def notify(msg: str) -> None:
        pass

    async def scenario():
        handler = _SlowCancellableHandler("gathered", run_delay=0.01, events=[])
        return handler, await _gather_with_progress_ping(handler, notify, want_ping=False, timeout=5)

    handler, result = asyncio.run(scenario())
    assert result == "gathered"
    assert handler.cancelled is False
