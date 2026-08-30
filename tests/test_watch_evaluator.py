"""Unit tests for the in-process STUB predicate evaluator (lib/agents/scheduler_agent/watch_evaluator.py).

The stub is dev/test-only (not a security boundary); these tests cover the calling
convention (`def check(data)`), the injected `get` helper, and bool coercion."""
import asyncio

import pytest

from lib.agents.scheduler_agent.watch_evaluator import StubEvaluator, _get


def _eval(code, data):
    return asyncio.run(StubEvaluator().evaluate(code, data))


def test_def_check_true():
    assert _eval("def check(data):\n    return data['status'] == 'HT'", {"status": "HT"}) is True


def test_def_check_false():
    assert _eval("def check(data):\n    return data['x'] > 5", {"x": 3}) is False


def test_injected_get_helper():
    code = "def check(data):\n    return get(data, 'a.b', 0) == 2"
    assert _eval(code, {"a": {"b": 2}}) is True


def test_helper_default_for_missing_path():
    code = "def check(data):\n    return get(data, 'a.missing', 'x') == 'x'"
    assert _eval(code, {"a": {}}) is True


def test_result_is_coerced_to_bool():
    # truthy non-bool return still yields a real bool
    assert _eval("def check(data):\n    return data['items']", {"items": [1]}) is True


def test_missing_check_raises():
    with pytest.raises(Exception):
        _eval("x = 1", {})


def test_get_helper_paths_and_lists():
    assert _get({"a": {"b": [10, 20]}}, "a.b.1") == 20
    assert _get({"a": 1}, "a.b.c", "default") == "default"
    assert _get({"a": {"b": 5}}, "a.b") == 5
