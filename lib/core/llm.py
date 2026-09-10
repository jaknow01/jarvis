"""Model-agnostic, per-agent LLM resolution.

Every agent resolves its LLM through ``LLM_BY_AGENT[name]()`` (the registry symmetry
the rest of the codebase and the tests rely on). This module makes that resolution
**config-driven and provider-aware** instead of hardcoding ``OpenAIResponses``:

- **Provider from a model prefix.** An agent's model is written ``provider/model`` —
  e.g. ``anthropic/claude-sonnet-4-20250514``, ``openrouter/deepseek/deepseek-r1``,
  ``openai/gpt-5.6-luna``. The provider is the part before the **first** ``/``; a bare
  model (no ``/``) defaults to provider ``openai`` (backward-compatible with today's
  bare model names). Everything after the first ``/`` is the provider's model id
  verbatim, so OpenRouter ids that themselves contain slashes survive.
- **A provider registry** (:data:`PROVIDER_BUILDERS`) maps each provider to a builder
  returning the right native LlamaIndex LLM: ``openai`` → ``OpenAIResponses`` (keeps the
  ``/responses`` path where tools + reasoning work together), ``anthropic`` → native
  ``Anthropic`` (extended thinking), and ``openrouter``/``groq``/``xai``/``openai-compat``
  → ``OpenAILike`` against an OpenAI-compatible ``api_base``.
- **Per-agent config with defaults and legacy fallbacks** (see :func:`build_llm`).
- **Reasoning normalized per provider** by :mod:`lib.core.reasoning` (one effort
  vocabulary, translated to OpenAI effort literals / Anthropic thinking budgets /
  OpenAI-compatible ``reasoning_effort``).

The generic resolver :func:`build_llm` is bound as the registry's default
(``bind_default_llm_resolver``), so an agent needs zero code — just config — to run on
any provider. ``@llm_usage`` remains available for explicit, code-level overrides.

Parallel tool-calling is a property of the *agent* under LlamaIndex
(``FunctionAgent(allow_parallel_tool_calls=...)``, default True), not of the LLM, so it
is set on the agents rather than here.
"""

import contextvars
import os

from llama_index.llms.openai import OpenAIResponses
from llama_index.llms.anthropic import Anthropic
from llama_index.llms.openai_like import OpenAILike

from lib.core.registry import bind_default_llm_resolver
from lib.core import reasoning

# The default provider when a model string carries no ``provider/`` prefix.
DEFAULT_PROVIDER = "openai"

# OpenAI-compatible providers: provider name → (base URL, API-key env var). These all
# build through ``OpenAILike`` pointed at the provider's base URL. ``openai-compat`` is
# the generic escape hatch for local/self-hosted OpenAI-compatible servers (vLLM,
# Ollama, …), whose base URL and key come from the environment.
_OPENAI_COMPAT_PROVIDERS: dict[str, tuple[str | None, str]] = {
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY"),
    "xai": ("https://api.x.ai/v1", "XAI_API_KEY"),
    "openai-compat": (os.getenv("OPENAI_COMPAT_BASE_URL"), "OPENAI_COMPAT_API_KEY"),
}


# --------------------------------------------------------------------------- parsing
def parse_model_string(raw: str) -> tuple[str, str]:
    """Split a ``provider/model`` string into ``(provider, model_id)``.

    Splits on the **first** ``/`` only, so ``openrouter/deepseek/deepseek-r1`` →
    ``("openrouter", "deepseek/deepseek-r1")``. A bare model (no ``/``) resolves to the
    default provider ``openai`` with the whole string as the model id.
    """
    raw = raw.strip()
    if "/" in raw:
        provider, model_id = raw.split("/", 1)
        return provider.strip().lower(), model_id.strip()
    return DEFAULT_PROVIDER, raw


# -------------------------------------------------------------------------- builders
def _build_openai(model_id: str, effort: str | None, budget_override: int | None):
    """OpenAI via the Responses API (tools + reasoning together)."""
    kwargs = {"model": model_id, "api_key": os.getenv("OPENAI_API_KEY")}
    kwargs.update(reasoning.openai_reasoning_kwargs(effort))
    return OpenAIResponses(**kwargs)


def _build_anthropic(model_id: str, effort: str | None, budget_override: int | None):
    """Native Anthropic with extended thinking (constraint-safe via the reasoning module)."""
    kwargs = {"model": model_id, "api_key": os.getenv("ANTHROPIC_API_KEY")}
    kwargs.update(reasoning.anthropic_thinking_kwargs(effort, budget_override))
    return Anthropic(**kwargs)


def _make_openai_like_builder(provider: str):
    """Build an ``OpenAILike`` builder bound to one OpenAI-compatible provider."""
    def build(model_id: str, effort: str | None, budget_override: int | None):
        base_url, key_env = _OPENAI_COMPAT_PROVIDERS[provider]
        kwargs = {
            "model": model_id,
            "api_base": base_url,
            "api_key": os.getenv(key_env),
            # FunctionAgent needs these to accept the LLM as a tool-calling chat model;
            # OpenAI-compatible endpoints don't always advertise them.
            "is_chat_model": True,
            "is_function_calling_model": True,
        }
        kwargs.update(reasoning.openai_compat_reasoning_kwargs(effort))
        return OpenAILike(**kwargs)
    return build


# provider string → builder(model_id, effort, budget_override) -> LlamaIndex LLM
PROVIDER_BUILDERS: dict = {
    "openai": _build_openai,
    "anthropic": _build_anthropic,
}
for _p in _OPENAI_COMPAT_PROVIDERS:
    PROVIDER_BUILDERS[_p] = _make_openai_like_builder(_p)


# --------------------------------------------------------- per-principal overrides
# The acting principal's resolved per-agent model/reasoning for the current turn, bound
# on a contextvar by the runtime (engine.handle_message) from the resolved Profile.
# Kept as a plain ``{agent_name: {"model": str|None, "reasoning": str|None}}`` dict so
# ``core`` stays a leaf (it must not import the ``domain`` Profile type). ``build_llm``
# consults it first and falls back to env when a field is absent — so a turn with no
# override (or no per-principal config) resolves exactly as before.
_MODEL_OVERRIDES: "contextvars.ContextVar[dict]" = contextvars.ContextVar(
    "jarvis_model_overrides", default={}
)


def bind_model_overrides(overrides: dict) -> "contextvars.Token":
    """Bind per-agent model/reasoning overrides for the current turn. Returns the token
    to reset. The binding propagates to the coordinator's step-workers and sub-agent runs
    (same contextvar propagation the shared Ctx relies on)."""
    return _MODEL_OVERRIDES.set(overrides or {})


def reset_model_overrides(token: "contextvars.Token") -> None:
    try:
        _MODEL_OVERRIDES.reset(token)
    except (ValueError, LookupError):
        pass


def _override(agent_name: str, field: str) -> str | None:
    row = _MODEL_OVERRIDES.get().get(agent_name)
    val = row.get(field) if row else None
    return val if (val is not None and str(val).strip() != "") else None


# ------------------------------------------------------------------ config resolution
def _agent_env(agent_name: str, suffix: str) -> str | None:
    val = os.getenv(f"AGENT_{agent_name.upper()}_{suffix}")
    return val if (val is not None and val.strip() != "") else None


def _resolve_model(agent_name: str) -> str | None:
    """Resolve an agent's model string: per-principal override → ``AGENT_<NAME>_MODEL`` →
    legacy coordinator var → ``DEFAULT_MODEL`` → legacy ``OPENAI_DEFAULT_MODEL`` (bare →
    treated as ``openai/…``)."""
    override = _override(agent_name, "model")
    if override:
        return override
    val = _agent_env(agent_name, "MODEL")
    if val:
        return val
    if agent_name == "coordinator":
        legacy = os.getenv("COORDINATOR_MODEL")
        if legacy and legacy.strip():
            return legacy.strip()
    default = os.getenv("DEFAULT_MODEL")
    if default and default.strip():
        return default.strip()
    legacy_default = os.getenv("OPENAI_DEFAULT_MODEL")
    if legacy_default and legacy_default.strip():
        return legacy_default.strip()  # bare value → default provider (openai)
    return None


def _resolve_effort_raw(agent_name: str) -> str | None:
    """Resolve an agent's raw reasoning-effort string (pre-normalization):
    ``AGENT_<NAME>_REASONING_EFFORT`` → legacy ``COORDINATOR_REASONING_EFFORT`` (coordinator
    only) → ``DEFAULT_REASONING_EFFORT``. A per-principal override wins over all of these.
    The coordinator keeps its historical ``medium`` default when nothing is configured;
    other agents default to no reasoning."""
    override = _override(agent_name, "reasoning")
    if override:
        return override
    val = _agent_env(agent_name, "REASONING_EFFORT")
    if val:
        return val
    if agent_name == "coordinator":
        legacy = os.getenv("COORDINATOR_REASONING_EFFORT")
        if legacy and legacy.strip():
            return legacy
    default = os.getenv("DEFAULT_REASONING_EFFORT")
    if default and default.strip():
        return default
    if agent_name == "coordinator":
        return "medium"
    return None


def _resolve_budget(agent_name: str) -> int | None:
    """Per-agent Anthropic thinking-budget override (``AGENT_<NAME>_THINKING_BUDGET``)."""
    val = _agent_env(agent_name, "THINKING_BUDGET")
    if val is None:
        return None
    try:
        return int(val)
    except ValueError:
        raise ValueError(
            f"AGENT_{agent_name.upper()}_THINKING_BUDGET must be an integer, got {val!r}"
        )


def build_llm(agent_name: str):
    """Resolve and construct the LlamaIndex LLM for ``agent_name`` from configuration.

    Reads the agent's model + reasoning effort (with the fallbacks in
    :func:`_resolve_model` / :func:`_resolve_effort_raw`), parses the provider prefix,
    normalizes the effort, and dispatches through :data:`PROVIDER_BUILDERS`. Raises a
    clear ``ValueError`` for a missing model or an unknown provider.
    """
    raw_model = _resolve_model(agent_name)
    if not raw_model:
        raise ValueError(
            f"No model configured for agent '{agent_name}': set AGENT_{agent_name.upper()}_MODEL "
            "or a global DEFAULT_MODEL (or legacy OPENAI_DEFAULT_MODEL)."
        )

    provider, model_id = parse_model_string(raw_model)
    builder = PROVIDER_BUILDERS.get(provider)
    if builder is None:
        raise ValueError(
            f"Unknown model provider '{provider}' for agent '{agent_name}' (from "
            f"model string '{raw_model}'). Supported: {', '.join(sorted(PROVIDER_BUILDERS))}."
        )

    effort = reasoning.normalize_effort(_resolve_effort_raw(agent_name))
    budget_override = _resolve_budget(agent_name)
    return builder(model_id, effort, budget_override)


def resolve_llm_config(agent_name: str) -> dict:
    """Report an agent's resolved LLM configuration **without constructing the client**.

    Read-only introspection for diagnostics (the ``/models`` admin command): it never
    instantiates a provider client, so it cannot raise on a missing API key. Returns a
    dict with ``provider`` / ``model`` / ``effort`` / ``budget``, or ``model=None`` when
    no model is configured for the agent.
    """
    raw_model = _resolve_model(agent_name)
    if not raw_model:
        return {"provider": None, "model": None, "effort": None, "budget": None}
    provider, model_id = parse_model_string(raw_model)
    return {
        "provider": provider,
        "model": model_id,
        "effort": reasoning.normalize_effort(_resolve_effort_raw(agent_name)),
        "budget": _resolve_budget(agent_name),
    }


# Bind the config-driven resolver as the registry default, so every agent resolves via
# LLM_BY_AGENT[name]() without an explicit @llm_usage factory. @llm_usage overrides still
# win (they populate the registry directly).
bind_default_llm_resolver(build_llm)
