"""Tests for the model-agnostic provider layer: prefix parsing, model-profile resolution
(incl. per-principal overrides), provider dispatch, and reasoning normalization.

No network / API keys are exercised — we set dummy keys so the LLM objects construct,
then assert on their type and params. Constructing a LlamaIndex LLM does not call out.
"""
import pytest

from llama_index.llms.openai import OpenAIResponses
from llama_index.llms.anthropic import Anthropic
from llama_index.llms.openai_like import OpenAILike

from lib.core import llm, reasoning
from lib.core.model_profiles import AgentModel, ModelProfile, using_profile


@pytest.fixture(autouse=True)
def _keys(monkeypatch):
    """Dummy provider keys so construction works."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-anthropic-test")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-openrouter-test")
    monkeypatch.setenv("XAI_API_KEY", "sk-xai-test")
    monkeypatch.setenv("GROQ_API_KEY", "sk-groq-test")
    monkeypatch.delenv("OPENAI_COMPAT_BASE_URL", raising=False)


def _profile(default=None, **agents):
    """A profile from compact args: default/agents entries are a model string or a dict."""
    def entry(v):
        return AgentModel(model=v) if isinstance(v, str) else AgentModel(**v)
    return ModelProfile(
        name="t",
        default=entry(default) if default else AgentModel(),
        agents={k: entry(v) for k, v in agents.items()},
    )


def _build(agent, profile):
    with using_profile(profile):
        return llm.build_llm(agent)


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


def test_unknown_provider_raises():
    with pytest.raises(ValueError, match="Unknown model provider 'bogusprovider'"):
        _build("testagent", _profile(testagent="bogusprovider/some-model"))


def test_missing_model_raises():
    with pytest.raises(ValueError, match="No model configured"):
        _build("testagent", _profile())


# ---------------------------------------------------------- 5.2 config resolution
def test_agent_entry_overrides_default():
    p = _profile(default="openai/gpt-4.1-mini", news_agent="anthropic/claude-sonnet-4-20250514")
    assert isinstance(_build("news_agent", p), Anthropic)
    assert isinstance(_build("weather_agent", p), OpenAIResponses)


def test_unlisted_agent_falls_back_to_default():
    built = _build("weather_agent", _profile(default="openai/gpt-4.1-mini"))
    assert isinstance(built, OpenAIResponses)
    assert built.model == "gpt-4.1-mini"


def test_bare_model_is_openai():
    built = _build("weather_agent", _profile(default="gpt-5.6-luna"))
    assert isinstance(built, OpenAIResponses)
    assert built.model == "gpt-5.6-luna"


def test_agent_entry_merges_field_by_field_over_default():
    p = _profile(default={"model": "openai/gpt-5-reasoning", "reasoning": "low"},
                 news_agent={"reasoning": "high"})
    built = _build("news_agent", p)
    assert built.model == "gpt-5-reasoning"
    assert built.reasoning_options["effort"] == "high"


def test_per_principal_override_beats_profile():
    p = _profile(default="openai/gpt-4.1-mini")
    token = llm.bind_model_overrides({"weather_agent": {"model": "openai/gpt-override", "reasoning": None}})
    try:
        built = _build("weather_agent", p)
    finally:
        llm.reset_model_overrides(token)
    assert built.model == "gpt-override"


def test_openrouter_uses_base_url_and_key():
    built = _build("testagent", _profile(testagent="openrouter/deepseek/deepseek-r1"))
    assert isinstance(built, OpenAILike)
    assert built.api_base == "https://openrouter.ai/api/v1"
    assert built.metadata.is_function_calling_model is True


def test_openai_compat_uses_env_base_url(monkeypatch):
    monkeypatch.setenv("OPENAI_COMPAT_BASE_URL", "http://adhoc:8000/v1")
    built = _build("testagent", _profile(default="openai-compat/local-model"))
    assert isinstance(built, OpenAILike) and built.api_base == "http://adhoc:8000/v1"


def _servers(tmp_path, monkeypatch, text):
    path = tmp_path / "providers.yaml"
    path.write_text(text)
    monkeypatch.setenv("PROVIDERS_CONFIG", str(path))


def test_named_server_builds_openai_like(tmp_path, monkeypatch):
    _servers(tmp_path, monkeypatch,
             "gpu-a: {type: openai-compat, base_url: 'http://a:8000/v1', api_key_env: GPU_A_KEY}\n"
             "gpu-b: {type: openai-compat, base_url: 'http://b:8000/v1'}\n")
    monkeypatch.setenv("GPU_A_KEY", "secret-a")
    p = _profile(default="gpu-a/Org/Qwen-27B", coordinator="gpu-b/meta/Llama-70B")
    sub, coord = _build("weather_agent", p), _build("coordinator", p)
    assert isinstance(sub, OpenAILike) and sub.api_base == "http://a:8000/v1"
    assert sub.model == "Org/Qwen-27B" and sub.api_key == "secret-a"
    assert coord.api_base == "http://b:8000/v1" and coord.model == "meta/Llama-70B"
    assert coord.api_key == "EMPTY"  # no api_key_env → placeholder for an open local server


def test_unknown_provider_error_lists_named_servers(tmp_path, monkeypatch):
    _servers(tmp_path, monkeypatch, "gpu-a: {type: openai-compat, base_url: 'http://a/v1'}\n")
    with pytest.raises(ValueError, match="named servers .*: gpu-a"):
        _build("testagent", _profile(default="gpu-z/m"))


def test_openai_compat_without_url_raises():
    with pytest.raises(ValueError, match="No base URL"):
        _build("testagent", _profile(default="openai-compat/local-model"))


# ------------------------------------------------------- 5.3 reasoning normalization
@pytest.mark.parametrize("sentinel", ["off", "none", "no", "false", "disabled", ""])
def test_normalize_effort_disable_sentinels(sentinel):
    assert reasoning.normalize_effort(sentinel) is None


def test_normalize_effort_none_is_none():
    assert reasoning.normalize_effort(None) is None


@pytest.mark.parametrize("effort", ["minimal", "low", "medium", "high"])
def test_normalize_effort_passthrough(effort):
    assert reasoning.normalize_effort(f"  {effort.upper()} ") == effort


def test_openai_effort_literal():
    built = _build("testagent", _profile(testagent={"model": "openai/gpt-5-reasoning", "reasoning": "high"}))
    assert built.reasoning_options["effort"] == "high"


def test_anthropic_thinking_budget():
    built = _build("testagent", _profile(
        testagent={"model": "anthropic/claude-sonnet-4-20250514", "reasoning": "medium"}))
    assert built.thinking_dict == {"type": "enabled", "budget_tokens": reasoning.EFFORT_BUDGET["medium"]}
    assert built.temperature == reasoning.ANTHROPIC_THINKING_TEMPERATURE
    assert built.max_tokens > reasoning.EFFORT_BUDGET["medium"]


def test_anthropic_thinking_budget_override():
    built = _build("testagent", _profile(testagent={
        "model": "anthropic/claude-sonnet-4-20250514", "reasoning": "medium", "thinking_budget": 5000}))
    assert built.thinking_dict["budget_tokens"] == 5000
    assert built.max_tokens > 5000


def test_openai_compat_reasoning_effort():
    built = _build("testagent", _profile(testagent={"model": "openrouter/deepseek/deepseek-r1", "reasoning": "low"}))
    assert built.reasoning_effort == "low"


def test_disabled_effort_omits_reasoning_openai():
    built = _build("testagent", _profile(testagent={"model": "openai/gpt-4.1", "reasoning": "off"}))
    assert built.reasoning_options is None


def test_disabled_effort_omits_thinking_anthropic():
    built = _build("testagent", _profile(
        testagent={"model": "anthropic/claude-sonnet-4-20250514", "reasoning": "off"}))
    assert built.thinking_dict is None


def test_unset_reasoning_means_none_even_for_coordinator():
    built = _build("coordinator", _profile(coordinator="openai/gpt-5-reasoning"))
    assert built.reasoning_options is None
