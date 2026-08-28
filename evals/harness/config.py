"""Evaluation configuration: role -> (model, reasoning effort), applied via env overrides.

A configuration is applied exactly the way the app already resolves models: by setting
``AGENT_<ROLE>_MODEL`` and ``AGENT_<ROLE>_REASONING_EFFORT`` in the environment before the
agent is built. ``engine.handle_message`` builds a fresh coordinator (and composer) each
turn and ``LLM_BY_AGENT`` reads these vars at build time, so no production code changes are
needed to switch models. ``apply_overrides`` is a context manager that saves and restores
the touched variables so one grid point never leaks into the next.
"""

from __future__ import annotations

import contextlib
import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class RoleModel:
    """A single role's model + reasoning effort for one run."""

    role: str
    model: str  # provider/model string, e.g. "openai/gpt-5-mini" or "openrouter/deepseek/..."
    reasoning_effort: str | None = None  # off/minimal/low/medium/high (project vocabulary)


@dataclass
class EvalConfig:
    """A complete configuration: the overrides to apply for one evaluation run.

    ``overrides`` maps role -> RoleModel. Roles not listed keep whatever the ambient
    environment / ``DEFAULT_MODEL`` resolves them to (the fixed baseline in a sweep).
    ``label`` names the config in reports.
    """

    label: str
    overrides: dict[str, RoleModel] = field(default_factory=dict)

    def env_pairs(self) -> dict[str, str]:
        """The concrete ``AGENT_<ROLE>_*`` env vars this config sets."""
        pairs: dict[str, str] = {}
        for role, rm in self.overrides.items():
            up = role.upper()
            pairs[f"AGENT_{up}_MODEL"] = rm.model
            if rm.reasoning_effort is not None:
                pairs[f"AGENT_{up}_REASONING_EFFORT"] = rm.reasoning_effort
        return pairs


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
    """Apply a config's env overrides for the duration, then restore prior values exactly.

    Saves each touched variable's prior value (including "was unset") and restores it on
    exit, so grid points are isolated even within a single process.
    """
    pairs = config.env_pairs()
    saved: dict[str, str | None] = {k: os.environ.get(k) for k in pairs}
    try:
        os.environ.update(pairs)
        yield
    finally:
        for k, prior in saved.items():
            if prior is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = prior
