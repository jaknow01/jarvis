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
- **Per-agent config from a named model profile** (``config/models/<MODEL_PROFILE>.yaml``,
  see :mod:`lib.core.model_profiles`), with per-principal overrides on top (see :func:`build_llm`).
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
from lib.core import model_profiles
from lib.core import custom_providers
from lib.core.settings import env_int

# The default provider when a model string carries no ``provider/`` prefix.
DEFAULT_PROVIDER = "openai"

# OpenAI-compatible providers: provider name → (default base URL, API-key env var). These
# all build through ``OpenAILike`` pointed at the provider's base URL. ``openai-compat`` is
# the generic escape hatch for local/self-hosted OpenAI-compatible servers (vLLM, Ollama,
# …) whose URL comes from ``OPENAI_COMPAT_BASE_URL`` — for a quick one-off; a server you
# keep using belongs in ``config/providers.yaml`` as a named provider (see
# :mod:`lib.core.custom_providers`).
_OPENAI_COMPAT_PROVIDERS: dict[str, tuple[str | None, str]] = {
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY"),
    "xai": ("https://api.x.ai/v1", "XAI_API_KEY"),
    "openai-compat": (None, "OPENAI_COMPAT_API_KEY"),
}


def _base_url(provider: str) -> str | None:
    """A built-in OpenAI-compatible provider's base URL (``openai-compat`` from env)."""
    if provider == "openai-compat":
        env = os.getenv("OPENAI_COMPAT_BASE_URL")
        return env.strip() if env and env.strip() else None
    return _OPENAI_COMPAT_PROVIDERS[provider][0]


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
def _client_kwargs() -> dict:
    """Transport limits shared by every provider client: ``LLM_REQUEST_TIMEOUT_SECONDS``
    (default 60) and ``LLM_MAX_RETRIES`` (default 2). Set explicitly because provider
    defaults differ (Anthropic's client has no timeout and retries 10x). These bound a
    single HTTP request only — a streamed response is limited per read, so the hard
    wall-clock caps live in ``agent_as_tool`` / the engine."""
    return {
        "timeout": float(env_int("LLM_REQUEST_TIMEOUT_SECONDS", 60, minimum=1)),
        "max_retries": env_int("LLM_MAX_RETRIES", 2, minimum=0),
    }


def _build_openai(model_id: str, effort: str | None, budget_override: int | None):
    """OpenAI via the Responses API (tools + reasoning together)."""
    kwargs = {"model": model_id, "api_key": os.getenv("OPENAI_API_KEY"), **_client_kwargs()}
    kwargs.update(reasoning.openai_reasoning_kwargs(effort))
    return OpenAIResponses(**kwargs)


class ToolChoiceAnthropic(Anthropic):
    """``Anthropic`` that understands the OpenAI-style ``tool_choice`` kwarg.

    ``FunctionAgent(initial_tool_choice=...)`` passes ``tool_choice`` to the LLM, which the
    OpenAI and OpenAI-compatible classes translate, but the native ``Anthropic`` class only
    knows ``tool_required`` and would forward the raw string to the API (a 400). Map it:
    ``"required"`` → ``tool_required=True`` (Anthropic ``{"type": "any"}``; the base class
    already falls back to ``auto`` while extended thinking is on, as the API demands),
    ``"auto"``/``"none"`` → the default, and a tool name → ``{"type": "tool", "name": …}``.
    """

    def _prepare_chat_with_tools(self, *args, tool_choice=None, **kwargs):
        if tool_choice == "required":
            kwargs["tool_required"] = True
        elif isinstance(tool_choice, str) and tool_choice not in ("auto", "none"):
            kwargs["tool_choice"] = {"type": "tool", "name": tool_choice}
        elif isinstance(tool_choice, dict):
            kwargs["tool_choice"] = tool_choice
        return super()._prepare_chat_with_tools(*args, **kwargs)


def _build_anthropic(model_id: str, effort: str | None, budget_override: int | None):
    """Native Anthropic with extended thinking (constraint-safe via the reasoning module)."""
    kwargs = {"model": model_id, "api_key": os.getenv("ANTHROPIC_API_KEY"), **_client_kwargs()}
    kwargs.update(reasoning.anthropic_thinking_kwargs(effort, budget_override))
    return ToolChoiceAnthropic(**kwargs)


def _make_openai_like_builder(provider: str):
    """Build an ``OpenAILike`` builder bound to one OpenAI-compatible provider."""
    def build(model_id: str, effort: str | None, budget_override: int | None):
        _default_url, key_env = _OPENAI_COMPAT_PROVIDERS[provider]
        base_url = _base_url(provider)
        if not base_url:
            raise ValueError(
                f"No base URL for provider '{provider}': set OPENAI_COMPAT_BASE_URL, or define the "
                "server as a named provider in config/providers.yaml."
            )
        kwargs = {
            "model": model_id,
            "api_base": base_url,
            "api_key": os.getenv(key_env),
            # FunctionAgent needs these to accept the LLM as a tool-calling chat model;
            # OpenAI-compatible endpoints don't always advertise them.
            "is_chat_model": True,
            "is_function_calling_model": True,
            **_client_kwargs(),
        }
        kwargs.update(reasoning.openai_compat_reasoning_kwargs(effort))
        return OpenAILike(**kwargs)
    return build


def _build_custom_openai_compat(server: custom_providers.CustomProvider, model_id: str,
                                effort: str | None, budget_override: int | None):
    """A named OpenAI-compatible server from ``config/providers.yaml``."""
    kwargs = {
        "model": model_id,
        "api_base": server.base_url,
        "api_key": server.api_key(),
        "is_chat_model": True,
        "is_function_calling_model": True,
        **_client_kwargs(),
    }
    kwargs.update(reasoning.openai_compat_reasoning_kwargs(effort))
    return OpenAILike(**kwargs)


# custom-provider type → builder(server, model_id, effort, budget_override)
_CUSTOM_BUILDERS = {"openai-compat": _build_custom_openai_compat}


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
# consults it first and falls back to the active model profile when a field is absent.
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
def _resolve_model(agent_name: str) -> str | None:
    """Resolve an agent's model string: per-principal override → the active model
    profile's entry for the agent → the profile's ``default``."""
    return _override(agent_name, "model") or model_profiles.active_profile().for_agent(agent_name).model


def _resolve_effort_raw(agent_name: str) -> str | None:
    """Resolve an agent's raw reasoning-effort string (pre-normalization): per-principal
    override → the active profile (agent entry → ``default``). Unset means no reasoning."""
    return (_override(agent_name, "reasoning")
            or model_profiles.active_profile().for_agent(agent_name).reasoning)


def _resolve_budget(agent_name: str) -> int | None:
    """Per-agent Anthropic thinking-budget override (``thinking_budget`` in the profile)."""
    return model_profiles.active_profile().for_agent(agent_name).thinking_budget


def custom_provider_names() -> list[str]:
    return sorted(custom_providers.load_providers(set(PROVIDER_BUILDERS)))


def _builder_for(provider: str):
    """The builder for ``provider``: a built-in one, else a named server from
    ``config/providers.yaml``; None when unknown."""
    builder = PROVIDER_BUILDERS.get(provider)
    if builder is not None:
        return builder
    server = custom_providers.load_providers(set(PROVIDER_BUILDERS)).get(provider)
    if server is None:
        return None
    custom = _CUSTOM_BUILDERS[server.type]
    return lambda model_id, effort, budget: custom(server, model_id, effort, budget)


def build_llm(agent_name: str):
    """Resolve and construct the LlamaIndex LLM for ``agent_name`` from configuration.

    Reads the agent's model + reasoning effort (per-principal override, else the active
    model profile — see :mod:`lib.core.model_profiles`), parses the provider prefix,
    normalizes the effort, and dispatches through :data:`PROVIDER_BUILDERS`. Raises a
    clear ``ValueError`` for a missing model or an unknown provider.
    """
    raw_model = _resolve_model(agent_name)
    if not raw_model:
        profile = model_profiles.active_profile()
        raise ValueError(
            f"No model configured for agent '{agent_name}' in model profile '{profile.name}': "
            f"set `default.model` or `agents.{agent_name}.model` there."
        )

    provider, model_id = parse_model_string(raw_model)
    builder = _builder_for(provider)
    if builder is None:
        raise ValueError(
            f"Unknown model provider '{provider}' for agent '{agent_name}' (from "
            f"model string '{raw_model}'). Built-in: {', '.join(sorted(PROVIDER_BUILDERS))}; "
            f"named servers (config/providers.yaml): {', '.join(custom_provider_names()) or 'none'}."
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
