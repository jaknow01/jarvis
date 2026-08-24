"""Per-agent LLM factories.

Each factory returns a ready-to-use LlamaIndex LLM configured for the agent(s) it
is registered against (model + reasoning effort). Agents read their LLM via
``LLM_BY_AGENT[name]()``; there is no single model pinned for the whole run, so
the coordinator's dedicated reasoning model and each sub-agent's model are honored
independently.

We use the OpenAI **Responses** API LLM (``OpenAIResponses``): the owner's models
are reasoning models, and only the Responses endpoint supports function tools
together with reasoning (the chat-completions endpoint rejects the combination).
This is the LLM-endpoint analogue of the Agents SDK's old ``use_responses=True``;
it is unrelated to conversation continuity — we no longer chain
``previous_response_id`` and instead replay our own history each turn
(``track_previous_responses`` stays False).

Parallel tool-calling is a property of the *agent* under LlamaIndex
(``FunctionAgent(allow_parallel_tool_calls=...)``, default True), not of the LLM,
so it is set on the agents rather than here.
"""

import os

from llama_index.llms.openai import OpenAIResponses

from lib.core.registry import llm_usage, LLM_BY_AGENT


def _api_key() -> str | None:
    return os.getenv("OPENAI_API_KEY")


def _responses_llm(model: str | None, effort: str | None = None) -> OpenAIResponses:
    """Build an ``OpenAIResponses`` LLM, optionally with an explicit reasoning effort.

    When ``effort`` is None the model runs with its own default reasoning. A
    reasoning model gets ``temperature=1.0`` automatically inside the LLM.
    """
    kwargs = {"model": model, "api_key": _api_key()}
    if effort is not None:
        kwargs["reasoning_options"] = {"effort": effort}
    return OpenAIResponses(**kwargs)


@llm_usage(["composer", "iot_operator", "maps_agent", "memory_operator", "finance_agent", "weather_agent", "fpl_agent", "scheduler_agent"])
def open_ai_llm() -> OpenAIResponses:
    return _responses_llm(os.getenv("OPENAI_DEFAULT_MODEL"))


@llm_usage(["coordinator"])
def open_ai_coordinator_llm() -> OpenAIResponses:
    # The coordinator does the heaviest thinking of the network: it routes each
    # request to the right subagent(s), decides what to run in parallel, and
    # reconciles multi-domain results before the composer writes the reply. It runs
    # on a dedicated, "smarter" reasoning model with explicit reasoning effort so
    # routing quality wins over latency (a deliberate trade-off).
    #
    # The env is the source of truth: COORDINATOR_MODEL picks the smart model
    # (falling back to OPENAI_DEFAULT_MODEL when unset) and
    # COORDINATOR_REASONING_EFFORT sets the reasoning effort (minimal/low/medium/
    # high; defaults to medium).
    #
    # Reasoning can be turned OFF entirely (it is costly) by setting
    # COORDINATOR_REASONING_EFFORT to a disable sentinel (none/off/no/false/disabled,
    # or empty) — we then omit reasoning options so the model runs with its own
    # default reasoning. With reasoning omitted the coordinator should run on a
    # NON-reasoning model (e.g. gpt-4.1/gpt-4o via COORDINATOR_MODEL or
    # OPENAI_DEFAULT_MODEL).
    model_name = os.getenv("COORDINATOR_MODEL") or os.getenv("OPENAI_DEFAULT_MODEL")
    effort = (os.getenv("COORDINATOR_REASONING_EFFORT") or "medium").strip().lower()

    _DISABLE = {"none", "off", "no", "false", "disabled", ""}
    return _responses_llm(model_name, effort=None if effort in _DISABLE else effort)


@llm_usage(["news_agent"])
def open_ai_reasoning_llm() -> OpenAIResponses:
    return _responses_llm("gpt-5-mini", effort="low")
