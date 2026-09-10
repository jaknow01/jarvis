"""Tests for per-principal profile resolution (`lib/domain/profile.py`).

Pure/JSON-backed: the module singletons `principals` / `agent_config` are swapped for
fresh JSON stores in a tmp dir, so nothing here needs a database or API keys. Covers the
one resolution rule (last-non-null-wins over [default, groups by priority, user]) and
its consequences: union, explicit-disable removal, group-priority tiebreak, user
override, unset-does-not-override, and empty-config parity.
"""
import lib.domain.profile as profile_mod
from lib.domain.principals import PrincipalStore, new_user_id, new_group_id
from lib.domain.agent_config import AgentConfigStore, DEFAULT_SCOPE, GROUP_SCOPE, USER_SCOPE
from lib.domain.profile import resolve_profile

AGENTS = ["iot_operator", "news_agent", "finance_agent", "weather_agent"]


def _wire(tmp_path, monkeypatch):
    ps = PrincipalStore(tmp_path / "p.json")
    acs = AgentConfigStore(tmp_path / "ac.json")
    monkeypatch.setattr(profile_mod, "principals", ps)
    monkeypatch.setattr(profile_mod, "agent_config", acs)
    # Neutralize env toggles so agent_enabled() is the True default for our agents.
    for name in AGENTS:
        monkeypatch.delenv(f"AGENT_{name.upper()}_ENABLED", raising=False)
    return ps, acs


def _user_in_groups(ps, groups):
    """groups: list of (name, priority). Returns (user_id, {name: group_id})."""
    uid = new_user_id()
    ps.add_user(uid, "T")
    ids = {}
    for name, prio in groups:
        gid = new_group_id()
        ps.add_group(gid, name, prio)
        ps.add_membership(uid, gid)
        ids[name] = gid
    return uid, ids


def test_empty_config_is_opt_in_off(tmp_path, monkeypatch):
    ps, acs = _wire(tmp_path, monkeypatch)
    uid, _ = _user_in_groups(ps, [("dom", 0)])
    prof = resolve_profile(uid, AGENTS)
    # Opt-in: with no config rows, nothing is enabled; models/reasoning unset (None).
    assert prof.roster() == []
    for name in AGENTS:
        assert prof.is_enabled(name) is False
        assert prof.agents[name].enabled.source == "default-off"
        assert prof.model(name) is None
        assert prof.reasoning(name) is None


def test_agent_enabled_only_when_a_layer_activates_it(tmp_path, monkeypatch):
    ps, acs = _wire(tmp_path, monkeypatch)
    uid, gids = _user_in_groups(ps, [("dom", 0)])
    acs.set(GROUP_SCOPE, gids["dom"], "weather_agent", enabled=True)
    prof = resolve_profile(uid, AGENTS)
    assert prof.roster() == ["weather_agent"]  # only the explicitly-enabled one


def test_union_across_groups(tmp_path, monkeypatch):
    ps, acs = _wire(tmp_path, monkeypatch)
    uid, gids = _user_in_groups(ps, [("dom", 0), ("dzieci", 0)])
    # Group A enables iot explicitly; group B does not mention it → union keeps it.
    acs.set(GROUP_SCOPE, gids["dom"], "iot_operator", enabled=True)
    prof = resolve_profile(uid, AGENTS)
    assert "iot_operator" in prof.roster()


def test_explicit_disable_removes(tmp_path, monkeypatch):
    ps, acs = _wire(tmp_path, monkeypatch)
    uid, gids = _user_in_groups(ps, [("dom", 0)])
    acs.set(GROUP_SCOPE, gids["dom"], "finance_agent", enabled=False)
    prof = resolve_profile(uid, AGENTS)
    assert "finance_agent" not in prof.roster()


def test_group_priority_breaks_tie(tmp_path, monkeypatch):
    ps, acs = _wire(tmp_path, monkeypatch)
    uid, gids = _user_in_groups(ps, [("low", 1), ("high", 10)])
    acs.set(GROUP_SCOPE, gids["low"], "news_agent", model="openai/gpt-5-mini")
    acs.set(GROUP_SCOPE, gids["high"], "news_agent", model="anthropic/claude")
    prof = resolve_profile(uid, AGENTS)
    assert prof.model("news_agent") == "anthropic/claude"
    assert prof.agents["news_agent"].model.source == "high"


def test_user_layer_wins(tmp_path, monkeypatch):
    ps, acs = _wire(tmp_path, monkeypatch)
    uid, gids = _user_in_groups(ps, [("high", 10)])
    acs.set(DEFAULT_SCOPE, "", "news_agent", model="openai/default")
    acs.set(GROUP_SCOPE, gids["high"], "news_agent", model="anthropic/group")
    acs.set(USER_SCOPE, uid, "news_agent", model="xai/user")
    prof = resolve_profile(uid, AGENTS)
    assert prof.model("news_agent") == "xai/user"
    assert prof.agents["news_agent"].model.source == "user"
    # A user explicit disable overrides a group enable, too.
    acs.set(GROUP_SCOPE, gids["high"], "iot_operator", enabled=True)
    acs.set(USER_SCOPE, uid, "iot_operator", enabled=False)
    prof = resolve_profile(uid, AGENTS)
    assert "iot_operator" not in prof.roster()


def test_unset_does_not_override(tmp_path, monkeypatch):
    ps, acs = _wire(tmp_path, monkeypatch)
    uid, gids = _user_in_groups(ps, [("low", 1), ("high", 10)])
    # Lower group sets a model; higher group only sets reasoning (model unset) → model kept.
    acs.set(GROUP_SCOPE, gids["low"], "weather_agent", model="openai/low-model")
    acs.set(GROUP_SCOPE, gids["high"], "weather_agent", reasoning="high")
    prof = resolve_profile(uid, AGENTS)
    assert prof.model("weather_agent") == "openai/low-model"
    assert prof.agents["weather_agent"].model.source == "low"
    assert prof.reasoning("weather_agent") == "high"
    assert prof.agents["weather_agent"].reasoning.source == "high"
