"""Unit tests for the public predicate-sandbox client (lib/agents/scheduler_agent/watch_sandbox_client.py).

Drives the client against a fake in-process unix-socket server implementing the wire
protocol — no real sandbox, no container. Socket paths live under /tmp to stay within the
AF_UNIX path-length limit on macOS."""
import asyncio
import json
import os
import tempfile

import pytest

from lib.agents.scheduler_agent.watch_evaluator import StubEvaluator
from lib.agents.scheduler_agent.watch_sandbox_client import (
    SandboxClient,
    SandboxError,
    resolve_evaluator,
)


def _sock() -> str:
    return os.path.join(tempfile.mkdtemp(dir="/tmp"), "s.sock")


async def _call(sock_path, responder, method, *args):
    """Start a one-shot fake server that replies via `responder(request)->response`,
    then invoke `method` on a client pointed at it."""
    async def handle(reader, writer):
        raw = await reader.readline()
        req = json.loads(raw.decode("utf-8"))
        writer.write((json.dumps(responder(req)) + "\n").encode("utf-8"))
        await writer.drain()
        writer.close()

    server = await asyncio.start_unix_server(handle, path=sock_path)
    try:
        client = SandboxClient(sock_path, timeout=2.0)
        return await getattr(client, method)(*args)
    finally:
        server.close()
        await server.wait_closed()


# -- runtime face -------------------------------------------------------------
def test_evaluate_returns_bool():
    r = asyncio.run(_call(_sock(), lambda req: {"ok": True, "result": True}, "evaluate", "c", {}))
    assert r is True


def test_evaluate_false():
    r = asyncio.run(_call(_sock(), lambda req: {"ok": True, "result": False}, "evaluate", "c", {}))
    assert r is False


def test_evaluate_raises_on_error_response():
    with pytest.raises(SandboxError):
        asyncio.run(_call(_sock(), lambda req: {"ok": False, "error": "boom"}, "evaluate", "c", {}))


def test_evaluate_raises_when_unreachable():
    missing = os.path.join(tempfile.mkdtemp(dir="/tmp"), "nope.sock")
    with pytest.raises(SandboxError):
        asyncio.run(SandboxClient(missing, timeout=0.5).evaluate("c", {}))


def test_request_carries_mode_code_data():
    seen = {}

    def responder(req):
        seen.update(req)
        return {"ok": True, "result": True}
    asyncio.run(_call(_sock(), responder, "evaluate", "CODE", {"a": 1}))
    assert seen["mode"] == "evaluate" and seen["code"] == "CODE" and seen["data"] == {"a": 1}


# -- authoring face -----------------------------------------------------------
def test_validate_ok():
    ok, err = asyncio.run(_call(_sock(), lambda req: {"ok": True, "result": None}, "validate", "code"))
    assert ok is True and err is None


def test_validate_error_carries_text():
    ok, err = asyncio.run(_call(_sock(), lambda req: {"ok": False, "error": "syntax at 1"}, "validate", "bad"))
    assert ok is False and "syntax" in err


def test_validate_unreachable_returns_false_not_raise():
    missing = os.path.join(tempfile.mkdtemp(dir="/tmp"), "none.sock")
    ok, err = asyncio.run(SandboxClient(missing, timeout=0.5).validate("code"))
    assert ok is False and "unreachable" in err


def test_evaluate_checked_ok():
    res, err = asyncio.run(_call(_sock(), lambda req: {"ok": True, "result": False}, "evaluate_checked", "c", {}))
    assert res is False and err is None


def test_evaluate_checked_error_carries_text():
    res, err = asyncio.run(_call(_sock(), lambda req: {"ok": False, "error": "runtime nope"}, "evaluate_checked", "c", {}))
    assert res is None and "nope" in err


# -- evaluator resolution -----------------------------------------------------
def test_resolve_evaluator_stub_when_unset(monkeypatch):
    monkeypatch.delenv("WATCH_SANDBOX_SOCKET", raising=False)
    assert isinstance(resolve_evaluator(), StubEvaluator)


def test_resolve_evaluator_client_when_set(monkeypatch):
    monkeypatch.setenv("WATCH_SANDBOX_SOCKET", "/tmp/jarvis-sbx.sock")
    ev = resolve_evaluator()
    assert isinstance(ev, SandboxClient) and ev.socket_path == "/tmp/jarvis-sbx.sock"
