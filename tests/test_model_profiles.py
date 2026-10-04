"""Tests for file-based model profiles (config/models/*.yaml)."""
import pytest

from lib.core.model_profiles import (
    AgentModel, ModelProfileError, available_profiles, load_profile, parse_profile,
)


def _write(tmp_path, name, text):
    (tmp_path / f"{name}.yaml").write_text(text)


@pytest.fixture
def profiles(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_PROFILES_DIR", str(tmp_path))
    return tmp_path


def test_parse_shorthand_and_merge():
    p = parse_profile("x", {
        "default": {"model": "openrouter/a", "reasoning": "off"},
        "agents": {"composer": "openai/b", "news_agent": {"reasoning": "low"}},
    })
    assert p.for_agent("composer") == AgentModel(model="openai/b", reasoning="off")
    assert p.for_agent("news_agent") == AgentModel(model="openrouter/a", reasoning="low")
    assert p.for_agent("weather_agent") == AgentModel(model="openrouter/a", reasoning="off")


def test_yaml_off_is_reasoning_off():
    # YAML 1.1 parses a bare `off` as False.
    p = parse_profile("x", {"default": {"model": "m", "reasoning": False}})
    assert p.default.reasoning == "off"


@pytest.mark.parametrize("bad", [
    {"defaults": {}},                                   # typo in a top-level key
    {"endpoints": {"openai-compat": "http://x"}},       # servers belong in config/providers.yaml
    {"agents": {"coordinator": {"modle": "x"}}},        # typo in an agent key
    {"agents": {"coordinator": {"thinking_budget": "lots"}}},
    {"agents": ["coordinator"]},
])
def test_invalid_profiles_are_rejected(bad):
    with pytest.raises(ModelProfileError):
        parse_profile("x", bad)


def test_load_and_select_by_env(profiles, monkeypatch):
    _write(profiles, "prod", "default: openai/gpt-4.1-mini\n")
    _write(profiles, "local", "default: {model: gpu-server/q, reasoning: off}\n")
    assert available_profiles() == ["local", "prod"]
    monkeypatch.setenv("MODEL_PROFILE", "local")
    p = load_profile("local")
    assert p.default.model == "gpu-server/q" and p.default.reasoning == "off"


def test_missing_profile_lists_available(profiles):
    _write(profiles, "prod", "default: openai/gpt-4.1-mini\n")
    with pytest.raises(ModelProfileError, match="available: prod"):
        load_profile("nope")


def test_reloads_when_file_changes(profiles):
    import os
    _write(profiles, "prod", "default: openai/a\n")
    assert load_profile("prod").default.model == "openai/a"
    _write(profiles, "prod", "default: openai/b\n")
    st = (profiles / "prod.yaml").stat()
    os.utime(profiles / "prod.yaml", (st.st_atime, st.st_mtime + 5))
    assert load_profile("prod").default.model == "openai/b"


def test_with_overrides():
    p = parse_profile("base", {"default": "openai/a", "agents": {"coordinator": "openai/c"}})
    q = p.with_overrides({"default": {"reasoning": "low"}, "coordinator": "openai/d"}, name="q")
    assert q.name == "q"
    assert q.for_agent("weather_agent") == AgentModel(model="openai/a", reasoning="low")
    assert q.for_agent("coordinator").model == "openai/d"
    assert p.for_agent("coordinator").model == "openai/c"  # original untouched


@pytest.mark.parametrize("name", ["prod", "local"])
def test_shipped_profiles_are_valid(name, monkeypatch):
    monkeypatch.delenv("MODEL_PROFILES_DIR", raising=False)
    p = load_profile(name)
    assert p.default.model
