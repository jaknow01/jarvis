"""Compatibility shim — moved to lib.core.llm (factories) and lib.core.registry
(LLM_BY_AGENT/llm_usage). Kept during the lib/ refactor so existing imports keep
working; remove once all callers import from lib.core."""
from lib.core.registry import LLM_BY_AGENT, llm_usage  # noqa: F401
from lib.core.llm import (  # noqa: F401
    open_ai_llm,
    open_ai_coordinator_llm,
    open_ai_reasoning_llm,
)
