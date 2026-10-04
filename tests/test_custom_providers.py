"""Tests for named model servers (config/providers.yaml)."""
import pytest

from lib.core.custom_providers import (
    PLACEHOLDER_API_KEY, ProvidersConfigError, load_providers, parse_providers,
)

BUILTIN = {"openai", "anthropic", "openrouter", "groq", "xai", "openai-compat"}


def test_parse_valid():
    servers = parse_providers({
        "gpu-server": {"type": "openai-compat", "base_url": "http://x:8000/v1",
                       "api_key_env": "K", "description": "d"},
    }, BUILTIN)
    s = servers["gpu-server"]
    assert (s.type, s.base_url, s.api_key_env, s.description) == ("openai-compat", "http://x:8000/v1", "K", "d")


def test_api_key_from_env_or_placeholder(monkeypatch):
    s = parse_providers({"a": {"type": "openai-compat", "base_url": "http://x", "api_key_env": "K"}}, BUILTIN)["a"]
    monkeypatch.delenv("K", raising=False)
    assert s.api_key() == PLACEHOLDER_API_KEY
    monkeypatch.setenv("K", "secret")
    assert s.api_key() == "secret"


@pytest.mark.parametrize("bad", [
    {"openrouter": {"type": "openai-compat", "base_url": "http://x"}},   # built-in name
    {"Gpu/1": {"type": "openai-compat", "base_url": "http://x"}},        # '/' or uppercase
    {"a": {"type": "anthropic", "base_url": "http://x"}},                # unsupported type
    {"a": {"type": "openai-compat"}},                                    # missing base_url
    {"a": {"type": "openai-compat", "base_url": "http://x", "url": 1}},  # unknown key
    {"a": "http://x"},                                                   # not a mapping
    ["a"],
])
def test_invalid(bad):
    with pytest.raises(ProvidersConfigError):
        parse_providers(bad, BUILTIN)


def test_missing_file_means_no_servers(tmp_path, monkeypatch):
    monkeypatch.setenv("PROVIDERS_CONFIG", str(tmp_path / "absent.yaml"))
    assert load_providers(BUILTIN) == {}


def test_shipped_file_is_valid(monkeypatch):
    monkeypatch.setenv("PROVIDERS_CONFIG", "config/providers.yaml")
    assert "gpu-server" in load_providers(BUILTIN)
