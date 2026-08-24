## 1. Dependencies & spike (de-risk first)

- [x] 1.1 Add `llama-index-llms-anthropic` and `llama-index-llms-openai-like` to `pyproject.toml`; run `poetry lock`; keep versions pinned
- [ ] 1.2 Spike: build a native `Anthropic` LLM with extended `thinking` enabled, wire it into a throwaway `FunctionAgent` with 2+ async tools, and run a multi-tool turn end-to-end — confirm parallel tool-calls work with thinking on _(offline wiring verified: Anthropic + `thinking_dict` + `FunctionAgent(tools=…)` constructs with `is_function_calling_model=True`; **live end-to-end run deferred — no `ANTHROPIC_API_KEY` in this env**)_
- [x] 1.3 From the spike, pin the Anthropic builder invariants (`thinking` dict shape, `temperature`, `max_tokens` vs budget) and the `EFFORT_BUDGET` default table values; if thinking+tools regresses, record the OpenRouter-fallback decision instead
- [x] 1.4 Confirm `OpenAILike` against an OpenAI-compatible base URL is accepted by `FunctionAgent` (set `is_chat_model`/`is_function_calling_model` if needed); note any per-endpoint flags

## 2. Reasoning normalization

- [x] 2.1 Add a reasoning module in `lib/core/` with the effort enum (`off`/`minimal`/`low`/`medium`/`high`) reusing the existing disable sentinel
- [x] 2.2 Implement per-provider translation: OpenAI `reasoning_options={"effort": …}`, Anthropic `thinking={"type":"enabled","budget_tokens": N}`, OpenAI-compatible `reasoning_effort` (or omit)
- [x] 2.3 Add the `EFFORT_BUDGET` default table (effort → `budget_tokens`) and honor a per-agent `AGENT_<NAME>_THINKING_BUDGET` override; enforce `max_tokens > budget` and required `temperature` for Anthropic thinking

## 3. Provider registry & config resolution

- [x] 3.1 Add a model-string parser: split on the first `/` → `(provider, model_id)`; bare model → provider `openai`
- [x] 3.2 Add `PROVIDER_BUILDERS` mapping `openai`→`OpenAIResponses`, `anthropic`→`Anthropic`, `openrouter`/`groq`/`xai`/`openai-compat`→`OpenAILike(base_url=…)`; resolve API keys and base URLs per provider; raise a clear error for an unknown provider
- [x] 3.3 Implement `build_llm(agent_name)`: resolve `AGENT_<NAME>_MODEL` → `DEFAULT_MODEL`, and `AGENT_<NAME>_REASONING_EFFORT` → `DEFAULT_REASONING_EFFORT`; parse prefix; apply reasoning normalization; dispatch through `PROVIDER_BUILDERS`
- [x] 3.4 Honor legacy fallbacks: `AGENT_COORDINATOR_MODEL` ← `COORDINATOR_MODEL`, `AGENT_COORDINATOR_REASONING_EFFORT` ← `COORDINATOR_REASONING_EFFORT`, `DEFAULT_MODEL` ← `OPENAI_DEFAULT_MODEL` (bare value treated as `openai/…`)

## 4. Wire into the registry

- [x] 4.1 Bind the default resolver so `LLM_BY_AGENT[name]()` resolves for every agent without an explicit `@llm_usage` (pin the hook per design Open Questions — e.g. `agents_decorator` setdefault) _(pinned: a `_LLMRegistry(dict)` with `__missing__`/`__contains__` delegating to a resolver bound by `bind_default_llm_resolver` — the smallest hook, keeps the registry the single surface)_
- [x] 4.2 Replace the three hardcoded `OpenAIResponses` factories in `lib/core/llm.py` with the config-driven path; keep `@llm_usage` available for explicit overrides and confirm the `news_agent` special-case still resolves
- [x] 4.3 Verify every agent still builds (`create_*_agent()`) and `FunctionAgent(llm=…)` accepts each provider's LLM

## 5. Tests

- [x] 5.1 Unit-test the prefix parser (prefixed, bare→openai, slash-in-model preserved, unknown provider error)
- [x] 5.2 Unit-test config resolution incl. legacy fallbacks (`COORDINATOR_MODEL`, `OPENAI_DEFAULT_MODEL`) and per-agent override precedence — no live API (assert on constructed LLM type/params)
- [x] 5.3 Unit-test reasoning normalization for each provider (effort literal / thinking budget / reasoning_effort / omit on disable) and the `AGENT_<NAME>_THINKING_BUDGET` override
- [x] 5.4 Run `poetry run pytest` green _(126 passed with `OPENAI_API_KEY` set; the 4 agent-construction tests need a live-looking key and fail identically on baseline without one — not a regression)_

## 6. Verification & docs

- [x] 6.1 Live smoke: run one turn with a sub-agent on `anthropic/…` and the coordinator on `openai/…` (mixed-provider turn), confirming tool-calls and a graceful Polish reply _(done as **coordinator `openai/gpt-5.6-luna` (no reasoning) + composer `openai/gpt-4.1-mini` + sub-agents `openrouter/deepseek/deepseek-v4-flash-0731`** — the Anthropic leg was intentionally skipped per owner; mixed OpenAI+OpenRouter turn confirmed, incl. parallel weather+FX fan-out and a graceful Polish reply)_
- [x] 6.2 Live smoke: run an agent via `openrouter/…` with a tools-capable model _(deepseek-v4-flash-0731: `supported_parameters` includes `tools`/`tool_choice`/`parallel_tool_calls`; weather + finance tool-calls succeeded end-to-end)_
- [x] 6.3 Update `.env_template`: new `AGENT_<NAME>_MODEL` / `AGENT_<NAME>_REASONING_EFFORT` / `AGENT_<NAME>_THINKING_BUDGET`, `DEFAULT_MODEL`/`DEFAULT_REASONING_EFFORT`, provider-prefix convention, `ANTHROPIC_API_KEY`/`OPENROUTER_API_KEY`, and the legacy-fallback note
- [x] 6.4 Update `CLAUDE.md`: provider-prefix config, the provider registry, per-agent naming, reasoning table; decide whether to drop the unused `litellm` dependency _(decided: **keep** `litellm` — unused but retained to avoid extra lockfile churn; noted in CLAUDE.md)_
- [x] 6.5 Run `openspec validate model-agnostic-providers --strict` and fix any issues
