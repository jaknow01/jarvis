"""Per-principal profile resolution: roster + per-agent model/reasoning.

Implements the ``agent-roster-resolution`` capability with a single rule. Layers are
ordered ``[env/default, groups sorted by ascending priority, user]`` and, per
``(agent, field)``, the **last non-null value wins**. Consequences that fall out of
this one rule:

- **Opt-in roster**: ``enabled`` starts **OFF**. An agent is in the roster only if some
  applicable layer explicitly sets ``enabled = true`` (a group, the user, or the seeded
  ``default`` group). An ``unset`` layer never activates and never disables; an explicit
  ``enabled = false`` in a later layer removes an agent activated by a lower one. So the
  roster is the **union of agents explicitly enabled** across the user's layers, minus
  explicit disables. (The owner's ``default`` group is seeded with every subagent enabled
  at startup, so the un-configured single-user setup still gets a full roster.)
- **Env kill-switch**: ``AGENT_<NAME>_ENABLED`` set to a falsy value forces an agent OFF
  globally, overriding any layer that enabled it.
- **User wins**: the user layer is applied last.
- **Group priority breaks ties**: groups are applied in ascending ``priority`` order,
  so the higher-priority group is applied later and wins.

For ``model`` / ``reasoning`` the base is ``None`` — meaning "unset", so the LLM
resolver (``lib.core.llm.build_llm``) falls back to the env config exactly as before
this change. A deployment with no DB config rows therefore reproduces today's
env-driven behavior (see the ``model-providers`` modified requirement).

This module lives in ``domain`` and must not import ``agents``; the candidate agent
names are supplied by the caller (the coordinator, which owns its subagent list).
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Optional

from lib.domain.principals import principals
from lib.domain.agent_config import agent_config, DEFAULT_SCOPE, GROUP_SCOPE, USER_SCOPE

logger = logging.getLogger(__name__)

# Provenance label used when a field's winning value came from the env/base rather than a
# DB config row.
ENV_SOURCE = "env"
# Provenance label for the opt-in default: an agent is OFF until a layer enables it.
DEFAULT_OFF_SOURCE = "default-off"

_FALSY = ("0", "false", "no", "off", "disabled")


def _env_kill(agent_name: str) -> bool:
    """True when ``AGENT_<NAME>_ENABLED`` is explicitly falsy — a hard global off switch
    that overrides any layer that enabled the agent."""
    raw = os.getenv(f"AGENT_{agent_name.upper()}_ENABLED")
    return raw is not None and raw.strip().lower() in _FALSY


@dataclass(frozen=True)
class FieldValue:
    """A resolved field plus the layer that set it (for `/profile` introspection)."""
    value: Any
    source: str


@dataclass(frozen=True)
class AgentProfile:
    enabled: FieldValue
    model: FieldValue
    reasoning: FieldValue


@dataclass(frozen=True)
class Profile:
    """A principal's resolved profile over a set of candidate agents."""
    user_id: Optional[str]
    agents: dict[str, AgentProfile]

    def roster(self) -> list[str]:
        """Agent names whose merged ``enabled`` is truthy (order preserved)."""
        return [name for name, ap in self.agents.items() if ap.enabled.value]

    def is_enabled(self, name: str) -> bool:
        ap = self.agents.get(name)
        return bool(ap.enabled.value) if ap else False

    def model(self, name: str) -> Optional[str]:
        ap = self.agents.get(name)
        return ap.model.value if ap else None

    def reasoning(self, name: str) -> Optional[str]:
        ap = self.agents.get(name)
        return ap.reasoning.value if ap else None


def _ordered_layers(user_id: Optional[str]) -> list[tuple[str, dict]]:
    """The (label, {agent: {enabled,model,reasoning}}) layers in resolution order:
    the DB ``default`` layer, then the user's groups ascending by priority, then the
    user layer. ``groups_for_user`` already returns groups sorted by ascending priority.
    """
    layers: list[tuple[str, dict]] = [("default", agent_config.get_layer(DEFAULT_SCOPE, ""))]
    if user_id:
        for g in principals.groups_for_user(user_id):
            layers.append((g["name"], agent_config.get_layer(GROUP_SCOPE, g["id"])))
        layers.append(("user", agent_config.get_layer(USER_SCOPE, user_id)))
    return layers


def resolve_profile(user_id: Optional[str], candidate_agents: list[str]) -> Profile:
    """Resolve ``user_id``'s profile over ``candidate_agents`` (the coordinator's subagents).

    ``user_id`` None (e.g. an unauthenticated context) resolves to the env/default base
    only. The returned :class:`Profile` records, per field, which layer produced the
    winning value.
    """
    layers = _ordered_layers(user_id)
    agents: dict[str, AgentProfile] = {}
    for name in candidate_agents:
        # Opt-in: an agent is OFF until a layer explicitly enables it.
        enabled = FieldValue(False, DEFAULT_OFF_SOURCE)
        model = FieldValue(None, ENV_SOURCE)
        reasoning = FieldValue(None, ENV_SOURCE)
        for label, cfg in layers:
            row = cfg.get(name)
            if not row:
                continue
            if row.get("enabled") is not None:
                enabled = FieldValue(bool(row["enabled"]), label)
            if row.get("model") is not None:
                model = FieldValue(row["model"], label)
            if row.get("reasoning") is not None:
                reasoning = FieldValue(row["reasoning"], label)
        # Hard global kill-switch overrides any layer that enabled the agent.
        if _env_kill(name):
            enabled = FieldValue(False, "env-off")
        agents[name] = AgentProfile(enabled, model, reasoning)
    return Profile(user_id, agents)
