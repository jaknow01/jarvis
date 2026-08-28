"""Adapter: use ANY provider the app supports as a deepeval model (judge / generator).

deepeval treats a bare model string as OpenAI. To run the judge or the Synthesizer on, say,
an OpenRouter model (deepseek), we wrap the app's own provider-agnostic LLM builder as a
``DeepEvalBaseLLM``. This reuses ``lib/core/llm.py`` (same ``provider/model`` parsing and
``PROVIDER_BUILDERS`` the agents use), so the eval judge/generator can be as capable as the
agents themselves.

Contract mirrors deepeval's ``GPTModel.generate``: ``generate(prompt, schema=None)`` returns
``(str | BaseModel, cost)``. We return cost ``0.0`` because judge/generator token cost is
tracked separately from agent cost (and would otherwise pollute the comparison).
"""

from __future__ import annotations

from pydantic import BaseModel
from deepeval.models.base_model import DeepEvalBaseLLM

from lib.core import llm as _llm  # lib/core/llm.py: parse_model_string + PROVIDER_BUILDERS


def build_llamaindex_llm(model_str: str, effort: str | None = None):
    """Build a LlamaIndex LLM for an arbitrary ``provider/model`` string (bare => openai).

    Uses the app's own parser + provider registry, so OpenRouter/groq/xai/anthropic/openai
    all resolve exactly as they do for the agents.
    """
    provider, model_id = _llm.parse_model_string(model_str)
    builder = _llm.PROVIDER_BUILDERS.get(provider)
    if builder is None:
        raise ValueError(f"Unknown provider '{provider}' in model string '{model_str}'")
    return builder(model_id, effort, None)


class LlamaIndexDeepEvalModel(DeepEvalBaseLLM):
    """Wrap a LlamaIndex LLM (any provider) as a deepeval judge/generator model."""

    def __init__(self, model_str: str, effort: str | None = None):
        self._model_str = model_str
        self._llm = build_llamaindex_llm(model_str, effort)
        super().__init__(model_name=model_str)

    # deepeval interface -----------------------------------------------------
    def load_model(self, *args, **kwargs):
        return self._llm

    def get_model_name(self, *args, **kwargs) -> str:
        return self._model_str

    # deepeval treats a subclassed model as a *custom* (non-native) model and expects
    # generate to return the value directly (the schema instance when a schema is given, or
    # a str otherwise) — NOT the (value, cost) tuple its native models return. Cost is tracked
    # separately here, so there is nothing to report back.
    def generate(self, prompt: str, schema: BaseModel | None = None):
        if schema is not None:
            return self._llm.as_structured_llm(schema).complete(prompt).raw
        return self._llm.complete(prompt).text

    async def a_generate(self, prompt: str, schema: BaseModel | None = None):
        if schema is not None:
            resp = await self._llm.as_structured_llm(schema).acomplete(prompt)
            return resp.raw
        resp = await self._llm.acomplete(prompt)
        return resp.text


def deepeval_model_for(spec: str, effort: str | None = None):
    """Return the right deepeval model for ``spec``.

    A bare model name (no ``/``) is passed through as a plain string so deepeval uses its
    native, well-tuned OpenAI path. A ``provider/model`` string is wrapped in the adapter so
    any provider (OpenRouter/deepseek, anthropic, groq, ...) works as judge/generator.
    """
    if "/" not in spec:
        return spec  # plain OpenAI model id -> deepeval's native handling
    return LlamaIndexDeepEvalModel(spec, effort)
