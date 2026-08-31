"""Unit tests for the watch authoring pipeline (lib/agents/scheduler_agent/authoring.py)
and the prepare_watch / create_watch tools. No network/model: the repair LLM and the
source fetch are faked/injected; validation uses the in-process stub evaluator."""
import asyncio

import pytest

import lib.agents.scheduler_agent.authoring as authoring
import lib.agents.scheduler_agent.tools as tmod
from lib.agents.scheduler_agent.authoring import (
    author_watch,
    normalize,
    smoke_test,
    validate_and_repair,
)
from lib.agents.scheduler_agent.tools import create_watch, prepare_watch
from lib.agents.scheduler_agent.watch_evaluator import StubEvaluator
from lib.core import Ctx, bind_app_context, reset_app_context


# -- normalizer ---------------------------------------------------------------
def test_normalize_strips_markdown_fence():
    code = "```python\ndef check(data):\n    return True\n```"
    assert normalize(code) == "def check(data):\n    return True"


def test_normalize_strips_bare_fence():
    assert normalize("```\ndef check(data): return True\n```") == "def check(data): return True"


def test_normalize_drops_noop_f_prefix():
    assert normalize('def check(data):\n    return data["s"] == f"HT"') == \
        'def check(data):\n    return data["s"] == "HT"'


def test_normalize_keeps_real_fstring():
    src = 'def check(data):\n    return f"{data}" == "x"'
    assert normalize(src) == src  # has a placeholder -> left for the repair model


def test_normalize_trims_and_handles_none():
    assert normalize("   x = 1   ") == "x = 1"
    assert normalize(None) == ""


# -- validate + repair loop ---------------------------------------------------
class FakeEval:
    """Scripted validate() results; evaluate_checked always ok(True)."""
    def __init__(self, results):
        self._results = list(results)
        self.calls = 0

    async def validate(self, code):
        r = self._results[self.calls]
        self.calls += 1
        return r

    async def evaluate_checked(self, code, data):
        return True, None


def test_repair_converges_within_cap():
    async def repair(code, err, intent):
        return "def check(data): return True"
    ev = FakeEval([(False, "syntax"), (True, None)])
    res = asyncio.run(validate_and_repair("bad", "intent", ev, repair=repair, attempts_cap=3))
    assert res.ok and res.attempts == 1 and res.code == "def check(data): return True"


def test_repair_fails_closed_after_cap():
    async def repair(code, err, intent):
        return "still bad"
    ev = FakeEval([(False, "e")] * 10)
    res = asyncio.run(validate_and_repair("bad", "i", ev, repair=repair, attempts_cap=2))
    assert not res.ok and res.attempts == 2 and res.error == "e"


def test_repair_exception_fails_closed():
    async def repair(code, err, intent):
        raise RuntimeError("model down")
    ev = FakeEval([(False, "e"), (True, None)])
    res = asyncio.run(validate_and_repair("bad", "i", ev, repair=repair, attempts_cap=3))
    assert not res.ok and "repair failed" in res.error


def test_valid_code_needs_no_repair():
    async def repair(code, err, intent):
        raise AssertionError("repair must not be called")
    ev = FakeEval([(True, None)])
    res = asyncio.run(validate_and_repair("ok", "i", ev, repair=repair))
    assert res.ok and res.attempts == 0


# -- smoke-test ---------------------------------------------------------------
def test_smoke_ok(monkeypatch):
    async def fake_fetch(source, params):
        return "ok", {"rate": 3.5}
    monkeypatch.setattr(authoring, "fetch_source", fake_fetch)
    state, result, err, sample = asyncio.run(
        smoke_test("finance.fx", {}, "def check(data):\n    return data['rate'] < 3.9", StubEvaluator())
    )
    assert state == "ok" and result is True and err is None
    assert sample == {"rate": 3.5}


def test_smoke_unknown_source(monkeypatch):
    async def fake_fetch(source, params):
        return "unknown", None
    monkeypatch.setattr(authoring, "fetch_source", fake_fetch)
    state, result, err, sample = asyncio.run(smoke_test("finance.fx", {}, "def check(data): return True", StubEvaluator()))
    assert state == "unknown" and result is None and err and sample is None


# -- author_watch (validate+repair then smoke) --------------------------------
def test_author_watch_ok(monkeypatch):
    async def fake_fetch(source, params):
        return "ok", {"rate": 4.2}

    async def fake_explain(code, intent, source, params, poll_interval, sample=None):
        return "Powiadomię Cię, gdy kurs USD/PLN spadnie poniżej 3.90."
    monkeypatch.setattr(authoring, "fetch_source", fake_fetch)
    monkeypatch.setattr(authoring, "llm_explain", fake_explain)
    res = asyncio.run(author_watch(
        "def check(data):\n    return data['rate'] < 3.9", "fx below 3.9",
        "finance.fx", {}, StubEvaluator(), poll_interval=300,
    ))
    assert res.ok and res.smoke_state == "ok" and res.smoke_result is False
    assert res.explanation and "3.90" in res.explanation


def test_author_watch_explanation_is_best_effort(monkeypatch):
    """A failing explainer must NOT block a correctly-validated watch (advisory, not a gate)."""
    async def fake_fetch(source, params):
        return "ok", {"rate": 4.2}

    async def boom_explain(code, intent, source, params, poll_interval, sample=None):
        raise RuntimeError("model down")
    monkeypatch.setattr(authoring, "fetch_source", fake_fetch)
    monkeypatch.setattr(authoring, "llm_explain", boom_explain)
    res = asyncio.run(author_watch(
        "def check(data):\n    return data['rate'] < 3.9", "fx below 3.9",
        "finance.fx", {}, StubEvaluator(), poll_interval=300,
    ))
    assert res.ok and res.smoke_result is False and res.explanation is None


# -- tools --------------------------------------------------------------------
def _call(tool, **kw):
    ctx = Ctx()
    ctx.conversation_id = "repl"
    token = bind_app_context(ctx)
    try:
        return asyncio.run(tool.async_fn(None, **kw))
    finally:
        reset_app_context(token)


def test_prepare_watch_happy_path(monkeypatch):
    async def fake_fetch(source, params):
        return "ok", {"rate": 3.5}

    async def fake_explain(code, intent, source, params, poll_interval, sample=None):
        return "Sprawdzam kurs co 300 s; dam znać raz, gdy spadnie poniżej 3.90."
    monkeypatch.setattr(authoring, "fetch_source", fake_fetch)
    monkeypatch.setattr(authoring, "llm_explain", fake_explain)
    res = _call(
        prepare_watch,
        message="Kurs spadł.", source="finance.fx",
        check_code="def check(data):\n    return data['rate'] < 3.9",
        poll_interval=300, intent="USD/PLN below 3.90", params={"foreign_currency": "USD"},
    )
    assert "prepared" in res
    assert res["prepared"]["current_result"] is True
    assert res["prepared"]["check_code"].startswith("def check")
    assert "3.90" in res["prepared"]["explanation"]
    assert "NOT armed" in res["note"]


def test_prepare_watch_unknown_source():
    res = _call(
        prepare_watch, message="m", source="bogus.src",
        check_code="def check(data): return True", poll_interval=60, intent="x",
    )
    assert "Error" in res and "known_sources" in res


def test_prepare_watch_unavailable_source_does_not_arm(monkeypatch):
    async def fake_fetch(source, params):
        return "unknown", None
    monkeypatch.setattr(authoring, "fetch_source", fake_fetch)
    res = _call(
        prepare_watch, message="m", source="finance.fx",
        check_code="def check(data):\n    return data['rate'] < 3.9",
        poll_interval=60, intent="x",
    )
    assert "Error" in res and "validated_check_code" in res


def test_create_watch_rejects_invalid_code(monkeypatch):
    captured = []
    monkeypatch.setattr(tmod.scheduler_store, "add", lambda job: captured.append(job))
    res = _call(
        create_watch, message="m", source="finance.fx",
        check_code="def check(data): return (unclosed",  # syntax error
        poll_interval=60, params={},
    )
    assert "Error" in res and captured == []


def test_create_watch_arms_valid_code(monkeypatch):
    captured = []
    monkeypatch.setattr(tmod.scheduler_store, "add", lambda job: captured.append(job) or job)
    code = "def check(data):\n    return data['rate'] < 3.9"
    res = _call(create_watch, message="m", source="finance.fx", check_code=code,
                poll_interval=300, params={"foreign_currency": "USD"})
    assert "watch" in res and len(captured) == 1
    assert captured[0]["check_code"] == code


# -- watch_coder is separately configurable -----------------------------------
def test_watch_coder_model_is_configurable(monkeypatch):
    from lib.core.llm import _resolve_model
    monkeypatch.setenv("AGENT_WATCH_CODER_MODEL", "anthropic/claude-x")
    assert _resolve_model("watch_coder") == "anthropic/claude-x"
