## Context

The LlamaIndex migration left a single, narrow seam for models: `lib/core/llm.py` is
the only place that constructs LLM objects. Three hand-written factories all return
`OpenAIResponses`, registered into `LLM_BY_AGENT` via `@llm_usage`; every agent reads
`llm = LLM_BY_AGENT[name]()` and passes it to its `FunctionAgent`. Reasoning effort is
OpenAI-specific (`reasoning_options={"effort": …}`) with a disable sentinel, driven by
`COORDINATOR_MODEL` / `COORDINATOR_REASONING_EFFORT` / `OPENAI_DEFAULT_MODEL`.

Because the seam is a single file and the call convention (`LLM_BY_AGENT[name]()`) is
already keyed by agent, making the assistant model-agnostic is a contained change to
that file plus a small reasoning-normalization module and the config surface. Agents,
the engine, tools, and history stay untouched.

The four core decisions below were confirmed with the owner during exploration.

## Goals / Non-Goals

**Goals:**

- Select an LLM provider **per agent** from configuration, using a `provider/model`
  prefix, with `openai` as the default provider when no prefix is given.
- Support OpenAI (via `OpenAIResponses`, preserving the working reasoning+tools path),
  **native** Anthropic (extended thinking), and any OpenAI-compatible endpoint
  (OpenRouter, Groq, xAI, local) via `OpenAILike`.
- Provide one provider-agnostic reasoning-effort control, translated per provider.
- Preserve the `LLM_BY_AGENT` registry and the 3-registry symmetry.
- Keep existing `.env` working during the transition (read old vars as fallbacks).

**Non-Goals:**

- Changing agent behavior, prompts, the engine, tools, or conversation history.
- A universal adapter (LiteLLM) — rejected in favor of native classes for tool-calling
  fidelity (see D1).
- Runtime/dynamic model switching mid-conversation, or per-request model overrides from
  the user — config is resolved at agent-build time (per turn), as today.
- Streaming, cost accounting, or automatic fallback/routing between providers.

## Decisions

### D1. Native LlamaIndex LLM classes behind a provider registry (not LiteLLM)

A `PROVIDER_BUILDERS` map keys a provider string to a builder that returns the correct
native LlamaIndex LLM:

```
provider       builder →                         notes
────────────   ──────────────────────────────    ─────────────────────────────────────
openai         OpenAIResponses(model, …)          keeps /responses (tools + reasoning)
anthropic      Anthropic(model, thinking=…)        native extended thinking
openrouter     OpenAILike(base_url=openrouter)     OpenAI-compatible
groq           OpenAILike(base_url=groq)           OpenAI-compatible
xai            OpenAILike(base_url=xai)             OpenAI-compatible
openai-compat  OpenAILike(base_url=env)            generic escape hatch (local/vLLM/Ollama)
```

- **Why:** native classes give best-in-class tool-calling per provider and let us keep
  the already-validated `OpenAIResponses` path. LiteLLM would collapse this to one class
  but its LlamaIndex wrapper has historically weaker tool-calling/streaming and no
  Responses support — an unacceptable regression risk for a network whose whole value is
  parallel tool-calling.
- **Alternatives:** LiteLLM universal adapter (rejected: fidelity/regression risk);
  OpenAI-compatible-only via `base_url` swap (rejected: loses native Anthropic thinking,
  which the owner wants).

### D2. Prefix-based, per-agent config; `LLM_BY_AGENT` preserved

Model strings carry the provider: `AGENT_<NAME>_MODEL=anthropic/claude-sonnet-4-20250514`.
Parsing splits on the **first** `/` only (OpenRouter model ids themselves contain
slashes, e.g. `openrouter/deepseek/deepseek-r1` → provider `openrouter`, model
`deepseek/deepseek-r1`). A bare model (no `/`) resolves to the default provider `openai`.

`LLM_BY_AGENT` stays the public surface. A generic resolver `build_llm(agent_name)`
reads that agent's config (model + effort, falling back to defaults), parses the prefix,
and dispatches through `PROVIDER_BUILDERS`. Every agent is bound to this resolver by
default; `@llm_usage` remains for explicit, code-level overrides. Binding the default is
done where agents are registered so `LLM_BY_AGENT[name]()` never misses (see Open
Questions for the exact hook).

- **Why:** one self-describing string per agent; consistent with `AGENT_<NAME>_ENABLED`;
  keeps the registry symmetry tests and docs rely on.
- **Alternatives:** separate `AGENT_<NAME>_PROVIDER` var (more verbose); a models
  config file (heavier new artifact) — deferred.

### D3. Unified env naming with backward-compatible fallbacks

Per agent: `AGENT_<NAME>_MODEL`, `AGENT_<NAME>_REASONING_EFFORT`, and optional
`AGENT_<NAME>_THINKING_BUDGET`. Global fallbacks: `DEFAULT_MODEL` and
`DEFAULT_REASONING_EFFORT`. Resolution order for an agent: agent-specific var → global
default → provider default.

Old vars are honored as fallbacks so existing deployments keep working:
`AGENT_COORDINATOR_MODEL` ← `COORDINATOR_MODEL`; `AGENT_COORDINATOR_REASONING_EFFORT` ←
`COORDINATOR_REASONING_EFFORT`; `DEFAULT_MODEL` ← `OPENAI_DEFAULT_MODEL` (a bare value
is treated as `openai/<value>`). This is a **config** breaking change (new canonical
names) softened by fallbacks; `.env_template` documents the new names.

- **Why:** consistency across all agents; zero-downtime transition.
- **Alternatives:** hard cutover (rejected: needlessly breaks the owner's running
  config).

### D4. Reasoning normalization: one effort enum, per-provider translation

A single effort vocabulary — `off`/`minimal`/`low`/`medium`/`high` plus the existing
disable sentinel (`none`/`off`/`no`/`false`/`disabled`/empty) — is translated per
provider by a small normalization module:

```
effort     OpenAI (Responses)          Anthropic (native)            OpenAI-compatible
────────   ─────────────────────────   ───────────────────────────   ─────────────────────
off        omit reasoning_options      thinking disabled              omit
minimal    {"effort":"minimal"}        budget = table[minimal]        reasoning_effort=minimal
low        {"effort":"low"}            budget = table[low]            reasoning_effort=low
medium     {"effort":"medium"}         budget = table[medium]         reasoning_effort=medium
high       {"effort":"high"}           budget = table[high]           reasoning_effort=high
```

Anthropic uses a token **budget**, not an effort literal, so an `EFFORT_BUDGET` default
table maps effort → `budget_tokens` (e.g. minimal≈1k, low≈4k, medium≈12k, high≈32k —
values pinned during apply). `AGENT_<NAME>_THINKING_BUDGET` overrides the table for a
given agent. When Anthropic thinking is enabled the builder enforces the API's
constraints: `temperature=1` and `max_tokens` > `budget_tokens`.

- **Why:** callers reason in one vocabulary; provider specifics stay in one module.
- **Alternatives:** expose raw provider params per agent (leaky, provider-coupled config);
  LiteLLM's normalization (tied to D1 rejection).

## Risks / Trade-offs

- **[Anthropic thinking + tools under FunctionAgent]** Native extended thinking combined
  with parallel tool-calls is the highest-risk unknown — the direct analogue of the
  `/responses`-for-tools discovery in the last migration. → **Spike first**: one agent on
  `anthropic/claude-*` with thinking, exercising a multi-tool turn, before any config
  porting. If it regresses, fall back to Claude via OpenRouter for that agent and note
  the limitation.
- **[OpenRouter tool-calling varies by model]** Some OpenRouter-served models don't
  support function calling. → Document that tool-using agents need a tools-capable model;
  the OpenAI-compatible builder passes tools through unchanged and surfaces provider
  errors to the composer (already graceful).
- **[Provider param constraints]** Reasoning models want `temperature=1`; Anthropic
  thinking needs `max_tokens` > budget. → Builders set these invariants centrally so no
  agent config can violate them.
- **[Mixed-provider turn]** A single turn may fan out across OpenAI + Anthropic +
  OpenRouter concurrently (different latency/cost). → Acceptable; runs are independent.
  `num_workers=4` concurrency cap is unchanged.
- **[Lockfile churn]** Two new LlamaIndex integration packages move the lockfile. →
  Contained to this change; pin the versions actually used; optionally drop unused
  `litellm`.
- **[Config drift]** More env knobs per agent. → Sensible global defaults mean an agent
  needs zero config to work; only overrides are explicit.

## Migration Plan

1. Add `llama-index-llms-anthropic` + `llama-index-llms-openai-like`; `poetry lock`.
2. **Spike:** native Anthropic + thinking + `FunctionAgent` + parallel tools end-to-end;
   lock the builder pattern (or record the OpenRouter fallback).
3. Add the reasoning-normalization module (effort enum + per-provider translation +
   `EFFORT_BUDGET` table).
4. Rewrite `lib/core/llm.py`: prefix parser, `PROVIDER_BUILDERS`, `build_llm(agent_name)`
   config resolution with backward-compatible fallbacks; bind the default resolver for
   every agent; keep `@llm_usage` for overrides.
5. Update tests (config resolution, prefix parsing, per-provider builder selection,
   reasoning translation) — no live API required (assert on constructed LLM type/params).
6. Update `.env_template` and `CLAUDE.md` (new naming, prefix convention, reasoning
   table, provider list).

**Rollback:** the work lives on a feature branch; revert by not merging. Old env vars
remain honored, so a rollback needs no config changes.

## Open Questions

- **Where to bind the default resolver** so `LLM_BY_AGENT[name]()` resolves for every
  agent without an explicit `@llm_usage`: extend `agents_decorator` to `setdefault` the
  resolver at agent registration, vs. a `__missing__`/defaultdict on `LLM_BY_AGENT`, vs.
  populating from the `AGENTS` keys. Pin during apply (smallest, least surprising hook).
- **Exact `EFFORT_BUDGET` values** and default `max_tokens` for Anthropic — pin during
  the spike against the target Claude model.
- **Whether to drop `litellm`** from dependencies now (unused) or leave it to avoid extra
  lockfile churn — decide during apply.
- **`OpenAILike` capability flags** (`is_chat_model`, `is_function_calling_model`) may
  need setting for some endpoints so `FunctionAgent` accepts them — verify per provider
  during apply.
