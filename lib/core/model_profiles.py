"""Model profiles: named, file-based sets of per-agent models (``config/models/*.yaml``).

The single source of truth for which LLM every agent runs on. A profile is a YAML file
``config/models/<name>.yaml`` (directory overridable with ``MODEL_PROFILES_DIR``); the
active one is chosen at startup with ``MODEL_PROFILE`` (default ``prod``). Switching the
whole network — e.g. to fully local models — is a one-variable change instead of editing
a handful of ``AGENT_<NAME>_MODEL`` env vars. Per-principal overrides (``agent_config`` /
``config/principals.yaml``) still win over the profile for their user (see ``llm.py``).

File format::

    description: Whole network on the local vLLM server
    default:                          # every agent not listed under `agents`
      model: gpu-server/RedHatAI/Qwen3.8-27B-INT4   # a named server from config/providers.yaml
      reasoning: off                  # off/minimal/low/medium/high
    agents:
      coordinator: { model: openai/gpt-5.6-luna, reasoning: off }
      composer: openai/gpt-4.1-mini   # shorthand: model only (reasoning from `default`)
      news_agent: { reasoning: low }  # override one field, inherit the rest
      watch_coder: { model: anthropic/claude-sonnet-4-20250514, reasoning: medium, thinking_budget: 8000 }

Agent entries merge field by field over ``default``. A profile names models only: where a
self-hosted model runs is the model string's provider prefix — a named server defined once
in ``config/providers.yaml`` (see ``lib.core.custom_providers``). Provider API keys never
live here — they stay in ``.env``. Profiles are re-read when the file changes (mtime), so editing the
active profile takes effect on the next turn without a restart.
"""

from __future__ import annotations

import contextlib
import contextvars
import logging
import os
from dataclasses import dataclass, field, replace
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

DEFAULT_PROFILES_DIR = Path("config/models")
DEFAULT_PROFILE = "prod"

_AGENT_KEYS = {"model", "reasoning", "thinking_budget"}
_TOP_KEYS = {"description", "default", "agents"}


class ModelProfileError(ValueError):
    """A missing or malformed model profile."""


@dataclass(frozen=True)
class AgentModel:
    """One agent's model settings; ``None`` fields inherit from the profile default."""

    model: str | None = None
    reasoning: str | None = None
    thinking_budget: int | None = None

    def over(self, base: "AgentModel") -> "AgentModel":
        """This entry merged field by field over ``base`` (set fields win)."""
        return AgentModel(
            model=self.model if self.model is not None else base.model,
            reasoning=self.reasoning if self.reasoning is not None else base.reasoning,
            thinking_budget=(self.thinking_budget if self.thinking_budget is not None
                             else base.thinking_budget),
        )


@dataclass(frozen=True)
class ModelProfile:
    name: str
    description: str = ""
    default: AgentModel = field(default_factory=AgentModel)
    agents: dict[str, AgentModel] = field(default_factory=dict)

    def for_agent(self, agent_name: str) -> AgentModel:
        """The effective settings for ``agent_name`` (its entry merged over ``default``)."""
        entry = self.agents.get(agent_name)
        return entry.over(self.default) if entry else self.default

    def with_overrides(self, overrides: dict, name: str | None = None) -> "ModelProfile":
        """A copy with ``overrides`` ({agent | "default": entry}) merged on top — the
        programmatic counterpart of editing the file (used by the eval harness)."""
        default = self.default
        agents = dict(self.agents)
        for agent, raw in overrides.items():
            entry = raw if isinstance(raw, AgentModel) else _parse_entry(raw, f"override {agent}")
            if agent == "default":
                default = entry.over(default)
            else:
                agents[agent] = entry.over(agents.get(agent, AgentModel()))
        return replace(self, name=name or self.name, default=default, agents=agents)


# ------------------------------------------------------------------------- parsing
def _parse_entry(raw, where: str) -> AgentModel:
    if raw is None:
        return AgentModel()
    if isinstance(raw, str):
        return AgentModel(model=raw.strip() or None)
    if not isinstance(raw, dict):
        raise ModelProfileError(f"{where}: expected a model string or a mapping, got {type(raw).__name__}")
    unknown = set(raw) - _AGENT_KEYS
    if unknown:
        raise ModelProfileError(f"{where}: unknown key(s) {sorted(unknown)}; allowed: {sorted(_AGENT_KEYS)}")
    budget = raw.get("thinking_budget")
    if budget is not None and not isinstance(budget, int):
        raise ModelProfileError(f"{where}: thinking_budget must be an integer, got {budget!r}")
    reasoning = raw.get("reasoning")
    if reasoning is False:  # YAML `off`/`no` parse as False
        reasoning = "off"
    return AgentModel(
        model=(str(raw["model"]).strip() or None) if raw.get("model") is not None else None,
        reasoning=str(reasoning).strip() if reasoning is not None else None,
        thinking_budget=budget,
    )


def parse_profile(name: str, data: dict) -> ModelProfile:
    """Validate and build a profile from its parsed YAML mapping."""
    if not isinstance(data, dict):
        raise ModelProfileError(f"model profile '{name}': top level must be a mapping")
    unknown = set(data) - _TOP_KEYS
    if unknown:
        raise ModelProfileError(f"model profile '{name}': unknown key(s) {sorted(unknown)}; "
                                f"allowed: {sorted(_TOP_KEYS)}")
    agents_raw = data.get("agents") or {}
    if not isinstance(agents_raw, dict):
        raise ModelProfileError(f"model profile '{name}': `agents` must be a mapping")
    return ModelProfile(
        name=name,
        description=str(data.get("description") or ""),
        default=_parse_entry(data.get("default"), f"model profile '{name}' default"),
        agents={a: _parse_entry(v, f"model profile '{name}' agents.{a}") for a, v in agents_raw.items()},
    )


# ------------------------------------------------------------------------- loading
def profiles_dir() -> Path:
    raw = os.getenv("MODEL_PROFILES_DIR")
    return Path(raw.strip()) if raw and raw.strip() else DEFAULT_PROFILES_DIR


def active_profile_name() -> str:
    raw = os.getenv("MODEL_PROFILE")
    return raw.strip() if raw and raw.strip() else DEFAULT_PROFILE


def available_profiles() -> list[str]:
    d = profiles_dir()
    return sorted(p.stem for p in d.glob("*.yaml")) if d.is_dir() else []


# (path, mtime) -> parsed profile; re-parsed only when the file changes.
_CACHE: dict[Path, tuple[float, ModelProfile]] = {}


def load_profile(name: str) -> ModelProfile:
    """Load ``<profiles_dir>/<name>.yaml`` (cached by mtime)."""
    path = profiles_dir() / f"{name}.yaml"
    try:
        mtime = path.stat().st_mtime
    except FileNotFoundError:
        avail = ", ".join(available_profiles()) or "none"
        raise ModelProfileError(
            f"Model profile '{name}' not found at {path} (set MODEL_PROFILE; available: {avail})."
        ) from None
    cached = _CACHE.get(path)
    if cached and cached[0] == mtime:
        return cached[1]
    with path.open() as f:
        profile = parse_profile(name, yaml.safe_load(f) or {})
    _CACHE[path] = (mtime, profile)
    logger.info("Loaded model profile '%s' from %s", name, path)
    return profile


# A profile forced for the current context (evals); None -> the MODEL_PROFILE file.
_FORCED: "contextvars.ContextVar[ModelProfile | None]" = contextvars.ContextVar(
    "jarvis_forced_model_profile", default=None
)


def active_profile() -> ModelProfile:
    """The profile in force: a context-forced one (see :func:`using_profile`), else the
    ``MODEL_PROFILE`` file."""
    forced = _FORCED.get()
    return forced if forced is not None else load_profile(active_profile_name())


@contextlib.contextmanager
def using_profile(profile: ModelProfile):
    """Force ``profile`` for everything run in this context (and tasks spawned from it)."""
    token = _FORCED.set(profile)
    try:
        yield profile
    finally:
        _FORCED.reset(token)
