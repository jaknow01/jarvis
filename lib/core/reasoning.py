"""Provider-normalized reasoning-effort control.

Callers (agents, config) reason in a single effort vocabulary —
``off``/``minimal``/``low``/``medium``/``high`` plus the existing disable
sentinel (``none``/``off``/``no``/``false``/``disabled``/empty) — and this module
translates that one vocabulary into each provider's native shape:

- **OpenAI (Responses):** ``reasoning_options={"effort": <literal>}``
- **Anthropic (native):** ``thinking_dict={"type":"enabled","budget_tokens": N}``
  (a token *budget*, mapped from the effort via :data:`EFFORT_BUDGET`), together
  with the constraints the API imposes when thinking is on — ``temperature`` at the
  required value and ``max_tokens`` strictly greater than the budget.
- **OpenAI-compatible (OpenRouter/Groq/xAI/local):** ``reasoning_effort=<literal>``

A disabled/``off`` effort omits reasoning options entirely, so the model runs with
its own default behavior. The provider specifics live only here; the LLM builders in
``lib.core.llm`` call these translators and never hand-roll provider params.
"""

# The disable sentinel is shared with the pre-LlamaIndex coordinator behavior: any of
# these (case-insensitive, whitespace-trimmed) — or an unset value — turns reasoning off.
DISABLE_SENTINELS = {"none", "off", "no", "false", "disabled", ""}

# The one effort vocabulary exposed to callers (besides the disable sentinel).
VALID_EFFORTS = ("minimal", "low", "medium", "high")

# Effort → Anthropic thinking token budget. Values pinned during apply (design D4):
# minimal ≈ 1k (the API's floor), low ≈ 4k, medium ≈ 12k, high ≈ 32k. Overridable per
# agent via ``AGENT_<NAME>_THINKING_BUDGET``.
EFFORT_BUDGET: dict[str, int] = {
    "minimal": 1024,
    "low": 4096,
    "medium": 12000,
    "high": 32000,
}

# Anthropic requires temperature == 1 when extended thinking is enabled, and
# max_tokens strictly greater than the thinking budget. We size the response headroom
# above the budget so the model has room to answer after thinking.
ANTHROPIC_THINKING_TEMPERATURE = 1.0
ANTHROPIC_MAX_TOKENS_HEADROOM = 4096


def normalize_effort(raw: str | None) -> str | None:
    """Map a raw effort string to a canonical effort literal, or ``None`` when disabled.

    ``None`` / empty / any disable sentinel → ``None`` (reasoning off). Any other value
    is lower-cased and stripped and returned verbatim (validation of the literal is left
    to the per-provider translators, which only act on known efforts).
    """
    if raw is None:
        return None
    effort = raw.strip().lower()
    if effort in DISABLE_SENTINELS:
        return None
    return effort


def openai_reasoning_kwargs(effort: str | None) -> dict:
    """OpenAI Responses reasoning options for ``effort`` (empty dict when disabled)."""
    if effort is None:
        return {}
    return {"reasoning_options": {"effort": effort}}


def openai_compat_reasoning_kwargs(effort: str | None) -> dict:
    """OpenAI-compatible (OpenRouter/Groq/xAI/local) reasoning field (empty when off)."""
    if effort is None:
        return {}
    return {"reasoning_effort": effort}


def anthropic_budget_for(effort: str | None, override: int | None = None) -> int | None:
    """Resolve the Anthropic thinking budget: explicit ``override`` wins, else the
    :data:`EFFORT_BUDGET` table value for ``effort``. ``None`` when thinking is off or
    the effort has no table entry."""
    if override is not None:
        return override
    if effort is None:
        return None
    return EFFORT_BUDGET.get(effort)


def anthropic_thinking_kwargs(effort: str | None, budget_override: int | None = None) -> dict:
    """Anthropic native-thinking kwargs for ``effort`` (empty dict when disabled).

    Returns ``thinking_dict`` plus the provider constraints (``temperature`` and a
    ``max_tokens`` strictly greater than the budget). ``budget_override`` (from
    ``AGENT_<NAME>_THINKING_BUDGET``) takes precedence over the default table.
    """
    budget = anthropic_budget_for(effort, budget_override)
    if budget is None:
        return {}
    return {
        "thinking_dict": {"type": "enabled", "budget_tokens": budget},
        "temperature": ANTHROPIC_THINKING_TEMPERATURE,
        "max_tokens": budget + ANTHROPIC_MAX_TOKENS_HEADROOM,
    }
