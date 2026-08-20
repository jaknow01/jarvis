"""Tests for env-controlled subagent enable/disable and coordinator wiring."""
import lib.agents as agents


def test_agent_enabled_defaults_true_when_unset(monkeypatch):
    monkeypatch.delenv("AGENT_NEWS_AGENT_ENABLED", raising=False)
    assert agents.agent_enabled("news_agent") is True


def test_agent_enabled_falsy_values_disable(monkeypatch):
    for v in ["false", "0", "no", "off", "False", "OFF"]:
        monkeypatch.setenv("AGENT_NEWS_AGENT_ENABLED", v)
        assert agents.agent_enabled("news_agent") is False, v


def test_agent_enabled_truthy_or_blank_stay_enabled(monkeypatch):
    for v in ["true", "1", "yes", "on", ""]:
        monkeypatch.setenv("AGENT_NEWS_AGENT_ENABLED", v)
        assert agents.agent_enabled("news_agent") is True, repr(v)


def test_coordinator_excludes_disabled_subagents(monkeypatch):
    monkeypatch.setenv("OPENAI_DEFAULT_MODEL", "gpt-4.1-mini")
    monkeypatch.setenv("AGENT_NEWS_AGENT_ENABLED", "false")
    monkeypatch.setenv("AGENT_IOT_OPERATOR_ENABLED", "off")
    coord = agents.create_coordinator_agent()
    names = {getattr(t, "name", "") for t in coord.tools}
    assert "news_agent" not in names
    assert "iot_operator" not in names
    # untouched agents remain exposed
    assert {"weather_agent", "finance_agent", "maps_agent", "memory_operator"} <= names


def test_coordinator_has_all_subagents_by_default(monkeypatch):
    monkeypatch.setenv("OPENAI_DEFAULT_MODEL", "gpt-4.1-mini")
    for a in ["IOT_OPERATOR", "WEATHER_AGENT", "FINANCE_AGENT", "MAPS_AGENT", "NEWS_AGENT", "MEMORY_OPERATOR"]:
        monkeypatch.delenv(f"AGENT_{a}_ENABLED", raising=False)
    coord = agents.create_coordinator_agent()
    names = {getattr(t, "name", "") for t in coord.tools}
    assert names == {"iot_operator", "weather_agent", "finance_agent", "maps_agent", "news_agent", "memory_operator"}
