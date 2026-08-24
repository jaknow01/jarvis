## Why

After the LlamaIndex migration every agent's LLM is hardcoded to `OpenAIResponses`
in one place (`lib/core/llm.py`), locking the whole assistant to a single vendor. The
roadmap goal — plug a different model provider per agent (OpenAI, Anthropic,
OpenRouter, …) so routing/reasoning quality and cost can be tuned per role — needs
that one seam generalized into a provider-aware, config-driven layer.

## What Changes

- **Per-agent provider selection via a model prefix.** An agent's model is written
  `provider/model` (e.g. `anthropic/claude-sonnet-4-20250514`, `openrouter/deepseek/deepseek-r1`,
  `openai/gpt-5.6-luna`). The provider is parsed from the prefix; **no prefix means
  `openai`** (backward-compatible with today's bare model names).
- **A provider registry in `lib/core/llm.py`** maps each provider to a builder that
  returns the right native LlamaIndex LLM: `openai` → `OpenAIResponses` (keeps the
  working reasoning+tools path), `anthropic` → native `Anthropic` (extended thinking),
  and `openrouter`/`groq`/`xai`/local → `OpenAILike` against an OpenAI-compatible
  `base_url`.
- **Unified per-agent env config.** `AGENT_<NAME>_MODEL` and
  `AGENT_<NAME>_REASONING_EFFORT` for **every** agent (coordinator, composer, and each
  sub-agent), consistent with the existing `AGENT_<NAME>_ENABLED`. A global
  `DEFAULT_MODEL` (and optional `DEFAULT_REASONING_EFFORT`) provides the fallback.
  **BREAKING** (config only): `COORDINATOR_MODEL` / `COORDINATOR_REASONING_EFFORT` /
  `OPENAI_DEFAULT_MODEL` are superseded by the new names (read as fallbacks for a
  smooth transition; see design).
- **A reasoning-normalization layer.** A shared effort enum
  (`off`/`minimal`/`low`/`medium`/`high`, keeping the existing disable sentinel) is
  translated per provider: OpenAI `reasoning_options={"effort": …}`, Anthropic
  `thinking={"type":"enabled","budget_tokens": N}`, OpenRouter/compat `reasoning_effort`.
  An `effort → budget_tokens` default table drives Anthropic, overridable per agent via
  `AGENT_<NAME>_THINKING_BUDGET`.
- **The `LLM_BY_AGENT` registry is preserved.** The 3-registry symmetry stays: every
  agent resolves its LLM through `LLM_BY_AGENT[name]()`, which now defaults to a generic
  config-driven resolver; `@llm_usage` remains available for explicit overrides.
- **De-risking spike first.** Before porting config, validate native Anthropic with
  `thinking` enabled running under a `FunctionAgent` with parallel tool calls end-to-end
  (the analogue of the `OpenAIResponses`-for-tools discovery in the last migration).

## Capabilities

### New Capabilities
- `model-providers`: How each agent's LLM is resolved from a per-agent, prefix-based
  configuration across multiple providers (OpenAI, Anthropic native, and any
  OpenAI-compatible endpoint), including a provider-normalized reasoning-effort control.

### Modified Capabilities
<!-- None synced in openspec/specs/ yet; the migrate-to-llamaindex "agent-orchestration"
     spec is not archived, so per-agent-model behavior is extended here as a new capability
     rather than a delta. -->

## Impact

- **Dependencies:** add `llama-index-llms-anthropic` and `llama-index-llms-openai-like`
  (pinned). New env keys: `ANTHROPIC_API_KEY`, `OPENROUTER_API_KEY` (`XAI_API_KEY`
  already exists). The unused `litellm` dependency may be dropped opportunistically.
- **Code — rewritten:** `lib/core/llm.py` (provider registry + builders + config
  resolution). **New:** a reasoning-normalization module in `lib/core/`.
- **Code — unchanged:** agent factories (`FunctionAgent(llm=…)`), the engine, tools,
  and conversation history are untouched — this is a narrow LLM-layer change.
- **Config/docs:** `.env_template` and `CLAUDE.md` gain the new per-agent naming, the
  provider-prefix convention, and the reasoning table.
- **Known limits:** OpenRouter tool-calling depends on the chosen model supporting
  tools; Anthropic `thinking` requires `temperature=1` and `max_tokens` > budget — the
  builders enforce these. Concurrency still capped at LlamaIndex `num_workers=4`.
