## Context

Jarvis is a multi-agent personal assistant. A coordinator agent routes each request to
specialized sub-agents and, today, hands off to a composer agent that writes the final
Polish reply. The stack is the OpenAI Agents SDK (`agents.Agent`, `Runner.run`,
`RunConfig`, `OpenAIProvider(use_responses=True)`), with conversation continuity kept by
storing `previous_response_id` in Redis per `conversation_id` (`lib/runtime/engine.py`).

Current structure (already package-based; `CLAUDE.md` is stale and describes an older flat
layout):

- `lib/core/` — `registry.py` (the `AGENTS` / `TOOLS_BY_AGENT` / `LLM_BY_AGENT` decorator
  registries, `agent_enabled()`), `llm.py` (per-agent model factories), `cache.py`
  (`Ctx` shared context + Redis `Cache`), `env.py` (`environment_preamble()`).
- `lib/agents/<name>/` — one package per agent, each with `agent.py` (factory decorated
  with `@agents_decorator`) and, where relevant, `tools.py` (function-tools decorated with
  `@tool_ownership` above `@function_tool`, signature `ctx: RunContextWrapper[Ctx]`).
- `lib/runtime/` — `engine.py` (transport-agnostic `handle_message`), `run_config.py`,
  `hooks.py`, `tracing.py` (MLflow), `chatbot.py` (REPL), `messenger.py`.
- `app/db/` — `connection.py`, `schema.py`, `migrate.py`, and repos (`memory_repo.py`,
  `scheduler_repo.py`, `fpl_repo.py`) that select Postgres when `DATABASE_URL` is set and
  fall back to on-disk JSON otherwise.

The migration is a coordinated swap of the agent runtime plus a forced change of ownership
of conversation history (because `previous_response_id` dies with the Agents SDK). This was
explored and the four core decisions below were confirmed with the owner.

## Goals / Non-Goals

**Goals:**

- Run the whole agent network on LlamaIndex (`FunctionAgent` + `agent.run()`), preserving the
  "sub-agents are the coordinator's tools" pattern and the three decorator registries.
- Provide an `agent_as_tool()` helper that reproduces `Agent.as_tool()` ergonomics.
- Make the composer a deterministic final step owned by `engine.handle_message`.
- Port the shared `Ctx` onto the LlamaIndex workflow `Context` so every tool keeps its
  scratch state and Redis cache.
- Own conversation history in Postgres (`conversations` + `messages`), replaying the full
  history as `chat_history` each turn, with an MLflow trace id on assistant rows.
- Keep per-agent models + reasoning settings working via `LLM_BY_AGENT`.

**Non-Goals:**

- Advanced history processing (summarization, truncation, token-budgeting,
  fact-extraction, unifying with `memory_operator`) — deferred to a later change.
- LiteLLM / multi-provider (Grok/Groq) — deferred.
- Response streaming to the user — deferred.
- Changing sub-agent behavior, prompts, or the set of external APIs beyond what the runtime
  swap requires.

## Decisions

### D1. Sub-agents as tools via an `agent_as_tool()` helper

Wrap each sub-agent's `run()` in an async closure exposed as a LlamaIndex `FunctionTool`,
rather than adopting `AgentWorkflow` handoffs. The closure signature carries the workflow
`Context` (auto-injected by annotation) and a single `prompt: str`, mirroring
`Agent.as_tool()`'s single-string input.

```python
def agent_as_tool(agent, tool_name, tool_description) -> FunctionTool:
    async def _run(ctx: Context, prompt: str) -> str:
        result = await agent.run(user_msg=prompt, ctx=ctx)  # ctx sharing per D3
        return str(result)
    _run.__name__ = tool_name
    _run.__doc__ = tool_description
    return FunctionTool.from_defaults(fn=_run, name=tool_name, description=tool_description)
```

- **Why:** keeps our centralized-coordinator model and the existing registries; matches the
  "docstring is the tool spec" convention.
- **Alternatives:** `AgentWorkflow` handoffs (Pattern 1) — rejected: different mental model,
  weaker fit for a single routing brain and harder to keep the composer deterministic.
  Custom planner (Pattern 3) — rejected: too much orchestration to own now.

### D2. Composer as a deterministic final step in the engine

`engine.handle_message` runs the coordinator to gather results, then explicitly calls
`composer.run(gathered)` and returns its output. The coordinator's instructions say it only
gathers; it has no composer tool and no handoff.

- **Why:** the composer must always run and always own the final Polish reply; making it a
  code step (not an LLM decision) removes a whole class of "coordinator answered directly"
  failures and fits our move toward owning the control flow.
- **Alternatives:** composer as another agent-tool (LLM may skip it or paraphrase);
  `AgentWorkflow` handoff (reintroduces framework-controlled control flow we want to own).
- **Open detail:** exact hand-off payload from coordinator to composer — the coordinator's
  final text vs. a structured gathered-results object. Start with passing the coordinator's
  gathered output as text; revisit if the composer needs more structure.

### D3. `Ctx` rides on the LlamaIndex workflow `Context`

Keep the `Ctx` dataclass (devices, `time_date_now`, `known_adresses`, `conversation_id`,
Redis `Cache`) as the shared per-run state, made reachable through the LlamaIndex workflow
`Context`. Tool signatures change from `ctx: RunContextWrapper[Ctx]` to the LlamaIndex
`Context`, and tools read/write our state through it. The same `Context` instance is passed
into nested sub-agent runs (see D1) so state is shared across the turn.

- **Why:** minimizes churn to tool bodies (only the accessor changes), preserves the
  server-side-handles convention (model sees names/aliases, never raw device keys/addresses).
- **Decision to confirm during apply:** whether to store `Ctx` fields directly in
  `ctx.store` (LlamaIndex-native, serializable state) or to hang our `Ctx` object on the
  Context as a single opaque handle. Prefer the opaque-handle approach first (smallest diff,
  no serialization constraints on `Ctx`), migrating individual fields into `ctx.store` only
  if a specific tool needs LlamaIndex state features. Validate `Context` auto-injection by
  annotation for closures produced by `agent_as_tool()`.

### D4. Own conversation history in Postgres; replay full history as `chat_history`

New tables, following the existing repo pattern (`app/db/conversation_repo.py`, tables in
`schema.py`, wired into `migrate.py`, JSON fallback when `DATABASE_URL` unset):

```
conversations                          messages
  id             uuid PK                 id               uuid PK
  channel_key    text UNIQUE             conversation_id  uuid FK -> conversations(id)
  created_at     timestamptz             role             text  CHECK in ('user','assistant')
  last_active_at timestamptz             content          text
                                         turn_index       int
                                         created_at       timestamptz
                                         mlflow_trace_id  text NULL
```

Per turn, `engine.handle_message`:
1. resolves/creates the conversation by `channel_key` (today's `conversation_id`),
2. loads all prior messages ordered by `turn_index` and maps them to LlamaIndex
   `ChatMessage`s,
3. runs the coordinator with `chat_history=...` (we are the source of truth; **no** LlamaIndex
   `Memory` object),
4. runs the composer,
5. persists the user message and the assistant reply (two rows), stamping the assistant row
   with the MLflow trace id when tracing is active.

- **Why:** vendor-neutral continuity we control; trivial to inspect; direct path to the
  deferred advanced-processing change.
- **Alternatives:** LlamaIndex `Memory`/`ChatMemoryBuffer` as source of truth — rejected for
  now (we want maximum control and an explicit schema); Redis-only continuity — rejected
  (not durable, and `previous_response_id` is Responses-specific).
- **Trace id retrieval:** read the active trace id from the MLflow tracing client after the
  run when a tracking URI is configured; when tracing is off, persist an empty value. Exact
  MLflow API call to be pinned during apply (small spike).

### D5. Per-agent models via `LLM_BY_AGENT`, now returning LlamaIndex LLMs

`LLM_BY_AGENT` factories return LlamaIndex LLM instances (OpenAI integration) configured per
agent, including reasoning effort for the coordinator and `news_agent`, and
`allow_parallel_tool_calls` where the coordinator needs fan-out. `RunConfig`/`OpenAIProvider`
and `use_responses=True` are removed.

- **Why:** preserves the existing per-agent model story with the smallest conceptual change.
- **Note:** the LlamaIndex OpenAI LLM exposes reasoning-effort configuration; map the current
  env-driven `COORDINATOR_MODEL` / `COORDINATOR_REASONING_EFFORT` (and the disable sentinel)
  onto it during apply.

## Risks / Trade-offs

- **[Concurrency cap: `num_workers=4`]** The built-in `AgentWorkflow` `call_tool` step uses a
  bare `@step` (default `num_workers=4`), so at most 4 tool calls run concurrently per step,
  with no public override short of a custom workflow. → Acceptable for the coordinator's
  typical fan-out; document it. If a turn ever needs >4 truly-parallel calls, they batch in
  waves of 4 (still concurrent). Revisit with a custom workflow only if profiling demands it.
- **[Parallel-tool-call fidelity]** Concurrency depends on `allow_parallel_tool_calls=True`
  (default), async tools (already true), and the LLM actually emitting multiple calls in one
  step. → Verify empirically on a multi-domain query during apply (guards against a latency
  regression vs. the current SDK behavior).
- **[`Context` injection into closures]** `agent_as_tool()` relies on LlamaIndex injecting the
  workflow `Context` by parameter annotation into a generated closure. → Validate early with a
  one-agent spike (e.g. `weather_agent`) before porting all ten agents.
- **[Blast radius on tool signatures]** Every function-tool signature changes. → Port one
  agent end-to-end first, lock the pattern, then apply it mechanically; keep tool bodies
  otherwise untouched to limit risk.
- **[Lockfile churn]** Adding LlamaIndex and removing the Agents SDK will move the lockfile,
  which the project treats cautiously. → Contain dependency changes to this migration; pin
  the LlamaIndex packages actually used.
- **[Continuity gap during cutover]** Old `previous_response_id` state in Redis becomes
  meaningless. → No data migration needed (it was ephemeral continuity, not user data); the
  first post-migration turn simply starts a fresh persisted history per channel.
- **[Composer payload shape]** Passing only the coordinator's text to the composer may lose
  structure that the composer would benefit from. → Start with text; escalate to a structured
  payload only if reply quality regresses.

## Migration Plan

1. Add LlamaIndex dependencies; introduce `agent_as_tool()` and the workflow `Context`
   wiring behind the existing helpers.
2. Port one sub-agent (`weather_agent`) end-to-end — factory, tools, `agent_as_tool` — and
   validate `Context` injection + a live tool call.
3. Port the remaining sub-agents and the coordinator; make the composer a deterministic step
   in `engine.handle_message`.
4. Add `conversations`/`messages` schema + `conversation_repo.py` (Postgres + JSON fallback);
   wire history load/persist and the MLflow trace id into the engine; remove
   `previous_response_id` and `use_responses=True`.
5. Update the registry/decorator tests to the new APIs; add tests for `agent_as_tool()` and
   the conversation repo.
6. Update `CLAUDE.md` to the current package layout and the LlamaIndex runtime.

**Rollback:** the work lives on the `llamaindex-migration` branch; revert by not merging.
No production data migration is performed, so rollback is a branch-level decision.

## Open Questions

- Exact MLflow API to fetch the active trace id at runtime (D4) — pin during apply.
- `ctx.store`-native state vs. opaque `Ctx` handle on the Context (D3) — default to the
  opaque handle; revisit per-tool only if needed.
- Does the LlamaIndex OpenAI LLM expose everything `COORDINATOR_REASONING_EFFORT`'s disable
  sentinel needs (reasoning fully off on a non-reasoning model)? Confirm during apply.
- After removing `previous_response_id`, is Redis still needed for anything in `Ctx.cache`?
  Audit remaining cache users; if none, note Redis as optional (do not remove in this change).
