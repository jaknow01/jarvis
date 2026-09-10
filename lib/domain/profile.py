"""Per-principal profile resolution: roster + per-agent model/reasoning.

Implements the ``agent-roster-resolution`` capability with a single rule. Layers are
ordered ``[env/default, groups sorted by ascending priority, user]`` and, per
``(agent, field)``, the **last non-null value wins**. Consequences that fall out of
this one rule:

- **Union of agents**: ``enabled`` starts from the env default (``agent_enabled`` —
  True unless ``AGENT_<NAME>_ENABLED`` is falsy). An ``unset`` layer never disables;
  only an explicit ``enabled = false`` in a later layer removes an agent. So the roster
  is the union of agents enabled across the user's groups, minus explicit disables.
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
from dataclasses import dataclass
from typing import Any, Optional

from lib.core import agent_enabled
from lib.domain.principals import principals
from lib.domain.agent_config import agent_config, DEFAULT_SCOPE, GROUP_SCOPE, USER_SCOPE

logger = logging.getLogger(__name__)

# Provenance label used when a field's winning value came from the environment/default
# base rather than a DB config row.
ENV_SOURCE = "env"


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
        enabled = FieldValue(bool(agent_enabled(name)), ENV_SOURCE)
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
        agents[name] = AgentProfile(enabled, model, reasoning)
    return Profile(user_id, agents)
