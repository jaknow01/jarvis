"""Evaluation configuration: role -> (model, reasoning effort), applied via model profiles.

A configuration is applied exactly the way the app resolves models: through the active
model profile (``config/models/<name>.yaml``, see ``lib.core.model_profiles``). An
``EvalConfig`` names a base profile (default: the ambient ``MODEL_PROFILE``) and per-role
overrides on top; ``apply_overrides`` forces that merged profile for the duration via
``using_profile`` (a contextvar, so it reaches every agent built inside the run).
``engine.handle_message`` builds a fresh coordinator (and composer) each turn and
``LLM_BY_AGENT`` resolves at build time, so no production code changes are needed to
switch models, and one grid point can never leak into the next.
"""

from __future__ import annotations

import contextlib
import os
from dataclasses import dataclass, field

from lib.core.model_profiles import (
    AgentModel,
    ModelProfile,
    active_profile_name,
    load_profile,
    using_profile,
)


@dataclass(frozen=True)
class RoleModel:
    """A single role's model + reasoning effort for one run."""

    role: str
    model: str  # provider/model string, e.g. "openai/gpt-5-mini" or "openrouter/deepseek/..."
    reasoning_effort: str | None = None  # off/minimal/low/medium/high (project vocabulary)


@dataclass
class EvalConfig:
    """A complete configuration: a base model profile plus per-role overrides.

    ``base_profile`` names a file under ``config/models/`` (None → ``MODEL_PROFILE``, i.e.
    whatever the app would run). ``overrides`` maps role -> RoleModel; the pseudo-role
    ``"default"`` overrides the profile's ``default`` entry (every agent without its own
    entry — in practice all sub-agents). Roles not listed keep the profile's models (the
    fixed baseline in a sweep). ``label`` names the config in reports.
    """

    label: str
    overrides: dict[str, RoleModel] = field(default_factory=dict)
    base_profile: str | None = None

    def profile(self) -> ModelProfile:
        """The merged profile this config runs under."""
        base = load_profile(self.base_profile or active_profile_name())
        return base.with_overrides(
            {role: AgentModel(model=rm.model, reasoning=rm.reasoning_effort)
             for role, rm in self.overrides.items()},
            name=f"{base.name}+{self.label}" if self.overrides else base.name,
        )


@dataclass(frozen=True)
class JudgeConfig:
    """The constant LLM-as-judge used for all metrics across a whole sweep.

    ``model`` is a ``provider/model`` string kept fixed so quality scores are comparable
    between candidates. A bare id (no ``/``) is deepeval's native OpenAI path; a
    provider-prefixed id (e.g. ``openrouter/deepseek/...``) is wrapped via the adapter so any
    provider can judge. Read from ``EVAL_JUDGE_MODEL`` with a sensible default; the judge's own
    token cost is accounted separately from agent cost.
    """

    model: str = field(default_factory=lambda: os.getenv("EVAL_JUDGE_MODEL", "gpt-4o"))

    def deepeval_model(self):
        """The value to pass as a metric's ``model=``: a plain string for a bare OpenAI id,
        or a provider-agnostic adapter for a ``provider/model`` id. Lazy-imported so this
        low-level module stays free of the deepeval/lib import cost until needed."""
        from evals.harness.deepeval_model import deepeval_model_for

        return deepeval_model_for(self.model)


@contextlib.contextmanager
def apply_overrides(config: EvalConfig):
    """Run under the config's merged model profile for the duration (contextvar-scoped,
    so grid points are isolated even within a single process)."""
    with using_profile(config.profile()):
        yield
