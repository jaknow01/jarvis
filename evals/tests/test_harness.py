"""Unit tests for the eval harness — pure helpers only (no network, no API keys).

Covers the parts most likely to silently break a comparison: provider-agnostic token
extraction, price-table lookup (including the reported-without-provider case), cost math,
env-override isolation, and usage/tool aggregation.
"""

import os

from evals.harness.instrument import UsageCapture, LlmCall, ToolInvocation, _extract_tokens
from evals.harness.cost import PriceTable, ModelPrice, cost_from_usage, total_usd
from evals.harness.config import EvalConfig, RoleModel, apply_overrides


# --------------------------------------------------------------- token extraction
def test_extract_tokens_openai_chat_flat():
    assert _extract_tokens({"prompt_tokens": 10, "completion_tokens": 3}) == (10, 3)


def test_extract_tokens_responses_nested_usage():
    class Usage:  # mimics ResponseUsage
        input_tokens = 869
        output_tokens = 15

    assert _extract_tokens({"usage": Usage()}) == (869, 15)


def test_extract_tokens_flat_responses_style():
    assert _extract_tokens({"input_tokens": 5, "output_tokens": 7}) == (5, 7)


def test_extract_tokens_missing_is_zero():
    assert _extract_tokens({}) == (0, 0)
    assert _extract_tokens({"other": 1}) == (0, 0)


# --------------------------------------------------------------- price table
def _table():
    return PriceTable(
        {
            "openai/gpt-4o-mini": ModelPrice(0.15, 0.60),
            "openrouter/deepseek/deepseek-v4-flash-0731": ModelPrice(0.14, 0.28),
        }
    )


def test_price_exact_and_bare_openai():
    t = _table()
    # bare model resolves to openai/<model>
    rc = t.price_tokens("weather_agent", "gpt-4o-mini", 1_000_000, 1_000_000)
    assert rc.input_cost_usd == 0.15 and rc.output_cost_usd == 0.60


def test_price_reported_without_provider_prefix():
    # OpenAILike reports "deepseek/deepseek-v4-flash-0731" (no leading openrouter/).
    t = _table()
    rc = t.price_tokens("weather_agent", "deepseek/deepseek-v4-flash-0731", 2_000_000, 0)
    assert rc.input_cost_usd == 0.28 and not rc.unpriced


def test_price_unknown_is_unpriced_not_zero():
    t = _table()
    rc = t.price_tokens("composer", "some/unknown-model", 1000, 1000)
    assert rc.unpriced and rc.total_cost_usd is None


def test_cost_from_usage_and_total():
    t = _table()
    usage = {("composer", "gpt-4o-mini"): {"prompt_tokens": 1_000_000, "completion_tokens": 0}}
    costs = cost_from_usage(usage, t)
    assert len(costs) == 1 and costs[0].input_cost_usd == 0.15
    assert total_usd(costs) == 0.15


def test_total_usd_all_unpriced_is_none():
    t = _table()
    usage = {("x", "unknown"): {"prompt_tokens": 10, "completion_tokens": 10}}
    assert total_usd(cost_from_usage(usage, t)) is None


# --------------------------------------------------------------- config overrides
def test_env_pairs():
    cfg = EvalConfig(
        label="t",
        overrides={"composer": RoleModel("composer", "openai/gpt-4.1-mini", "low")},
    )
    pairs = cfg.env_pairs()
    assert pairs["AGENT_COMPOSER_MODEL"] == "openai/gpt-4.1-mini"
    assert pairs["AGENT_COMPOSER_REASONING_EFFORT"] == "low"


def test_apply_overrides_isolated(monkeypatch):
    monkeypatch.delenv("AGENT_COMPOSER_MODEL", raising=False)
    cfg = EvalConfig(label="t", overrides={"composer": RoleModel("composer", "openai/x")})
    with apply_overrides(cfg):
        assert os.environ["AGENT_COMPOSER_MODEL"] == "openai/x"
    # restored to "unset" after the block
    assert "AGENT_COMPOSER_MODEL" not in os.environ


def test_apply_overrides_restores_prior_value(monkeypatch):
    monkeypatch.setenv("AGENT_COMPOSER_MODEL", "openai/prior")
    cfg = EvalConfig(label="t", overrides={"composer": RoleModel("composer", "openai/new")})
    with apply_overrides(cfg):
        assert os.environ["AGENT_COMPOSER_MODEL"] == "openai/new"
    assert os.environ["AGENT_COMPOSER_MODEL"] == "openai/prior"


# --------------------------------------------------------------- capture aggregation
def test_mock_tools_swaps_and_restores():
    from lib.core import TOOLS_BY_AGENT
    from evals.harness.mocks import mock_tools

    before = TOOLS_BY_AGENT["weather_agent"]
    before_names = [t.metadata.name for t in before]
    with mock_tools():
        during = TOOLS_BY_AGENT["weather_agent"]
        # same tools (names/schema preserved) but different (mocked) objects
        assert [t.metadata.name for t in during] == before_names
        assert during is not before
    # restored to the exact original list object on exit
    assert TOOLS_BY_AGENT["weather_agent"] is before


def test_mock_fixture_lookup():
    from evals.harness.mocks import _fixture_for

    assert _fixture_for("current_weather")["city"] == "Warszawa"
    # unknown tool -> generic confirmation, never a crash
    assert _fixture_for("some_unknown_tool") == {"status": "ok", "_mock": "some_unknown_tool"}


def test_unwrap_simulated_input():
    from evals.harness.simulator import _unwrap_simulated_input

    # JSON-wrapped opening turn -> plain text
    assert _unwrap_simulated_input('{"simulated_input": "Jaka pogoda?"}') == "Jaka pogoda?"
    # already-plain text is untouched
    assert _unwrap_simulated_input("Jaka pogoda?") == "Jaka pogoda?"
    # malformed json -> returned as-is, never crashes
    assert _unwrap_simulated_input('{simulated_input oops') == '{simulated_input oops'


def test_usage_and_tool_aggregation():
    cap = UsageCapture()
    cap.calls = [
        LlmCall("coordinator", "m", "p", 100, 10),
        LlmCall("coordinator", "m", "p", 50, 5),
        LlmCall("weather_agent", "m2", "p", 200, 20),
    ]
    cap.tools = [
        ToolInvocation("coordinator", "weather_agent"),
        ToolInvocation("weather_agent", "current_weather"),
        ToolInvocation("weather_agent", "weather_forecast"),
    ]
    by_role = cap.by_role()
    assert by_role["coordinator"] == {"prompt_tokens": 150, "completion_tokens": 15, "calls": 2}
    assert cap.tools_by_caller()["weather_agent"] == ["current_weather", "weather_forecast"]
