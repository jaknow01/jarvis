"""Tests for the model-agnostic provider layer: prefix parsing, config resolution
(incl. legacy fallbacks), provider dispatch, and reasoning normalization.

No network / API keys are exercised — we set dummy keys so the LLM objects construct,
then assert on their type and params. Constructing a LlamaIndex LLM does not call out.
"""
import pytest

from llama_index.llms.openai import OpenAIResponses
from llama_index.llms.anthropic import Anthropic
from llama_index.llms.openai_like import OpenAILike

from lib.core import llm, reasoning


# Env vars that could leak from the shell/.env and skew resolution.
_MODEL_ENV = [
    "DEFAULT_MODEL",
    "OPENAI_DEFAULT_MODEL",
    "DEFAULT_REASONING_EFFORT",
    "COORDINATOR_MODEL",
    "COORDINATOR_REASONING_EFFORT",
]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Dummy provider keys (so construction works) and a clean model config surface."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-anthropic-test")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-openrouter-test")
    monkeypatch.setenv("XAI_API_KEY", "sk-xai-test")
    monkeypatch.setenv("GROQ_API_KEY", "sk-groq-test")
    for var in _MODEL_ENV:
        monkeypatch.delenv(var, raising=False)
    # Any AGENT_*_MODEL / _REASONING_EFFORT / _THINKING_BUDGET from the environment.
    for key in list(__import__("os").environ):
        if key.startswith("AGENT_") and (
            key.endswith("_MODEL") or key.endswith("_REASONING_EFFORT") or key.endswith("_THINKING_BUDGET")
        ):
            monkeypatch.delenv(key, raising=False)


# ------------------------------------------------------------------ 5.1 prefix parser
def test_parse_prefixed_model():
    assert llm.parse_model_string("anthropic/claude-sonnet-4-20250514") == (
        "anthropic",
        "claude-sonnet-4-20250514",
    )


def test_parse_bare_model_defaults_to_openai():
    assert llm.parse_model_string("gpt-5.6-luna") == ("openai", "gpt-5.6-luna")


def test_parse_preserves_slash_in_model_id():
    assert llm.parse_model_string("openrouter/deepseek/deepseek-r1") == (
        "openrouter",
        "deepseek/deepseek-r1",
    )


def test_unknown_provider_raises(monkeypatch):
    monkeypatch.setenv("AGENT_TESTAGENT_MODEL", "bogusprovider/some-model")
    with pytest.raises(ValueError, match="Unknown model provider 'bogusprovider'"):
        llm.build_llm("testagent")


def test_missing_model_raises(monkeypatch):
    with pytest.raises(ValueError, match="No model configured"):
        llm.build_llm("testagent")


# ---------------------------------------------------------- 5.2 config resolution
def test_agent_specific_model_overrides_default(monkeypatch):
    monkeypatch.setenv("DEFAULT_MODEL", "openai/gpt-4.1-mini")
    monkeypatch.setenv("AGENT_NEWS_AGENT_MODEL", "anthropic/claude-sonnet-4-20250514")
    news = llm.build_llm("news_agent")
    weather = llm.build_llm("weather_agent")
    assert isinstance(news, Anthropic)
    assert isinstance(weather, OpenAIResponses)


def test_unset_agent_model_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("DEFAULT_MODEL", "openai/gpt-4.1-mini")
    built = llm.build_llm("weather_agent")
    assert isinstance(built, OpenAIResponses)
    assert built.model == "gpt-4.1-mini"


def test_legacy_coordinator_model_still_applies(monkeypatch):
    monkeypatch.setenv("COORDINATOR_MODEL", "gpt-5.6-luna")
    built = llm.build_llm("coordinator")
    assert isinstance(built, OpenAIResponses)
    assert built.model == "gpt-5.6-luna"


def test_legacy_openai_default_model_applies_as_openai(monkeypatch):
    monkeypatch.setenv("OPENAI_DEFAULT_MODEL", "gpt-5.6-luna")
    built = llm.build_llm("weather_agent")
    assert isinstance(built, OpenAIResponses)
    assert built.model == "gpt-5.6-luna"


def test_agent_model_beats_legacy_coordinator(monkeypatch):
    monkeypatch.setenv("COORDINATOR_MODEL", "gpt-legacy")
    monkeypatch.setenv("AGENT_COORDINATOR_MODEL", "openai/gpt-new")
    built = llm.build_llm("coordinator")
    assert built.model == "gpt-new"


def test_openrouter_uses_base_url_and_key(monkeypatch):
    monkeypatch.setenv("AGENT_TESTAGENT_MODEL", "openrouter/deepseek/deepseek-r1")
    built = llm.build_llm("testagent")
    assert isinstance(built, OpenAILike)
    assert built.api_base == "https://openrouter.ai/api/v1"
    assert built.metadata.is_function_calling_model is True


# ------------------------------------------------------- 5.3 reasoning normalization
@pytest.mark.parametrize("sentinel", ["off", "none", "no", "false", "disabled", ""])
def test_normalize_effort_disable_sentinels(sentinel):
    assert reasoning.normalize_effort(sentinel) is None


def test_normalize_effort_none_is_none():
    assert reasoning.normalize_effort(None) is None


@pytest.mark.parametrize("effort", ["minimal", "low", "medium", "high"])
def test_normalize_effort_passthrough(effort):
    assert reasoning.normalize_effort(f"  {effort.upper()} ") == effort


def test_openai_effort_literal(monkeypatch):
    monkeypatch.setenv("AGENT_TESTAGENT_MODEL", "openai/gpt-5-reasoning")
    monkeypatch.setenv("AGENT_TESTAGENT_REASONING_EFFORT", "high")
    built = llm.build_llm("testagent")
    assert built.reasoning_options["effort"] == "high"


def test_anthropic_thinking_budget(monkeypatch):
    monkeypatch.setenv("AGENT_TESTAGENT_MODEL", "anthropic/claude-sonnet-4-20250514")
    monkeypatch.setenv("AGENT_TESTAGENT_REASONING_EFFORT", "medium")
    built = llm.build_llm("testagent")
    assert built.thinking_dict == {"type": "enabled", "budget_tokens": reasoning.EFFORT_BUDGET["medium"]}
    assert built.temperature == reasoning.ANTHROPIC_THINKING_TEMPERATURE
    assert built.max_tokens > reasoning.EFFORT_BUDGET["medium"]


def test_anthropic_thinking_budget_override(monkeypatch):
    monkeypatch.setenv("AGENT_TESTAGENT_MODEL", "anthropic/claude-sonnet-4-20250514")
    monkeypatch.setenv("AGENT_TESTAGENT_REASONING_EFFORT", "medium")
    monkeypatch.setenv("AGENT_TESTAGENT_THINKING_BUDGET", "5000")
    built = llm.build_llm("testagent")
    assert built.thinking_dict["budget_tokens"] == 5000
    assert built.max_tokens > 5000


def test_openai_compat_reasoning_effort(monkeypatch):
    monkeypatch.setenv("AGENT_TESTAGENT_MODEL", "openrouter/deepseek/deepseek-r1")
    monkeypatch.setenv("AGENT_TESTAGENT_REASONING_EFFORT", "low")
    built = llm.build_llm("testagent")
    assert built.reasoning_effort == "low"


def test_disabled_effort_omits_reasoning_openai(monkeypatch):
    monkeypatch.setenv("AGENT_TESTAGENT_MODEL", "openai/gpt-4.1")
    monkeypatch.setenv("AGENT_TESTAGENT_REASONING_EFFORT", "off")
    built = llm.build_llm("testagent")
    assert built.reasoning_options is None


def test_disabled_effort_omits_thinking_anthropic(monkeypatch):
    monkeypatch.setenv("AGENT_TESTAGENT_MODEL", "anthropic/claude-sonnet-4-20250514")
    monkeypatch.setenv("AGENT_TESTAGENT_REASONING_EFFORT", "off")
    built = llm.build_llm("testagent")
    assert built.thinking_dict is None


def test_coordinator_defaults_to_medium_effort(monkeypatch):
    monkeypatch.setenv("AGENT_COORDINATOR_MODEL", "openai/gpt-5-reasoning")
    built = llm.build_llm("coordinator")
    assert built.reasoning_options["effort"] == "medium"


def test_coordinator_reasoning_can_be_disabled(monkeypatch):
    monkeypatch.setenv("AGENT_COORDINATOR_MODEL", "openai/gpt-4.1")
    monkeypatch.setenv("COORDINATOR_REASONING_EFFORT", "off")
    built = llm.build_llm("coordinator")
    assert built.reasoning_options is None
