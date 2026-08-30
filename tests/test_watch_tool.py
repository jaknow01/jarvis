"""Unit tests for the create_watch tool (lib/agents/scheduler_agent/tools.py).

Binds a shared Ctx (the tool resolves the delivery channel from it) and stubs the store's
add so nothing is written to disk. No network/model."""
import asyncio

import lib.agents.scheduler_agent.tools as tmod
from lib.agents.scheduler_agent.tools import create_watch
from lib.core import Ctx, bind_app_context, reset_app_context


def _call(**kw):
    ctx = Ctx()
    ctx.conversation_id = "repl"
    token = bind_app_context(ctx)
    try:
        return asyncio.run(create_watch.async_fn(None, **kw))
    finally:
        reset_app_context(token)


def _valid_kw(**over):
    kw = dict(
        message="Kurs USD/PLN spadł poniżej 3,90.",
        source="finance.fx",
        check_code="def check(data):\n    return data['rate'] < 3.9",
        poll_interval=300,
        params={"foreign_currency": "USD"},
    )
    kw.update(over)
    return kw


def test_unknown_source_returns_error(monkeypatch):
    captured = []
    monkeypatch.setattr(tmod.scheduler_store, "add", lambda job: captured.append(job))
    res = _call(**_valid_kw(source="totally.bogus"))
    assert "Error" in res and "known_sources" in res
    assert captured == []  # nothing stored on rejection


def test_valid_watch_is_stored_with_verbatim_predicate(monkeypatch):
    captured = []
    monkeypatch.setattr(tmod.scheduler_store, "add", lambda job: captured.append(job) or job)
    code = "def check(data):\n    return data['rate'] < 3.9"
    res = _call(**_valid_kw(check_code=code))
    assert "watch" in res
    assert len(captured) == 1
    job = captured[0]
    assert job["kind"] == "watch" and job["source"] == "finance.fx"
    assert job["check_code"] == code            # stored verbatim
    assert job["params"] == {"foreign_currency": "USD"}
    # public view never leaks the predicate/params
    assert "check_code" not in res["watch"] and "params" not in res["watch"]


def test_invalid_poll_interval_returns_error(monkeypatch):
    monkeypatch.setattr(tmod.scheduler_store, "add", lambda job: job)
    res = _call(**_valid_kw(poll_interval=0))
    assert "Error" in res
