## Why

Jarvis today runs on the OpenAI Agents SDK and leans on the OpenAI Responses API
(`previous_response_id`) to keep conversations continuous. That ties the whole
network to one vendor's runtime, hides conversation state inside OpenAI, and blocks
the roadmap goal of per-agent multi-provider models. Migrating to LlamaIndex gives
us an explicit, event-driven agent runtime we control — and because dropping the
Agents SDK also drops `previous_response_id`, we must simultaneously take ownership
of conversation history in our own Postgres store.

## What Changes

- **Framework swap to LlamaIndex.** Replace `agents.Agent`/`Runner.run` with LlamaIndex
  `FunctionAgent`/`agent.run()`. Each agent keeps its own model + reasoning settings,
  resolved per-agent from the existing `LLM_BY_AGENT` registry.
- **`agent_as_tool()` helper** replaces `Agent.as_tool()`: a small factory that wraps a
  sub-agent's `run()` as a LlamaIndex `FunctionTool`, preserving the "sub-agents are the
  coordinator's tools" pattern (Pattern 2). The three decorator registries
  (`AGENTS` / `TOOLS_BY_AGENT` / `LLM_BY_AGENT`), `environment_preamble()` injection, and
  `agent_enabled()` toggles are preserved.
- **Composer becomes a deterministic final step.** **BREAKING** (internal): the coordinator
  no longer hands off to the composer via the SDK `handoffs` mechanism. Instead
  `engine.handle_message` calls `composer.run(gathered)` explicitly as the last step. The
  coordinator gathers only and never writes the user-facing reply; the composer writes the
  final Polish answer.
- **Shared `Ctx` migrates to the LlamaIndex workflow `Context`.** Every function-tool
  signature (`ctx: RunContextWrapper[Ctx]`) is ported to the LlamaIndex equivalent; tool
  scratch state (`devices`, `time_date_now`, `known_adresses`, `conversation_id`) and the
  Redis cache remain reachable from tools.
- **Persistent conversation history in Postgres.** **BREAKING**: `previous_response_id`
  (Redis) and `use_responses=True` are removed. New `conversations` + `messages` tables
  store only the user message and the final assistant reply per turn. The full history of a
  conversation is loaded and passed as `chat_history` to the coordinator each turn; we are
  the source of truth (no LlamaIndex `Memory` object). An MLflow trace id is recorded on the
  assistant message row for observability linkage.
- **Out of scope (deferred):** advanced history processing (summarization, truncation,
  fact-extraction, unifying with `memory_operator`), LiteLLM/multi-provider (Grok/Groq),
  and response streaming.

## Capabilities

### New Capabilities
- `agent-orchestration`: How the coordinator delegates to specialized sub-agents exposed as
  tools, runs them concurrently, and composes a single final Polish reply via a deterministic
  composer step — on the LlamaIndex runtime.
- `conversation-history`: How per-channel conversations and their user/assistant messages are
  persisted in Postgres and replayed as context on each turn, replacing the Responses API
  continuity mechanism.

### Modified Capabilities
<!-- None: no existing specs in openspec/specs/. -->

## Impact

- **Dependencies:** add `llama-index-core` (+ the LlamaIndex OpenAI LLM integration); remove
  the `openai-agents` (`agents`) SDK usage. Lockfile churn is unavoidable here — contained to
  this migration.
- **Code — rewritten:** `lib/agents/*/agent.py` (all 10 agents incl. coordinator + composer),
  `lib/agents/*/tools.py` (every function-tool signature), `lib/core/registry.py`,
  `lib/core/llm.py`, `lib/core/cache.py` (`Ctx`), `lib/runtime/engine.py`,
  `lib/runtime/run_config.py`, `lib/runtime/hooks.py`.
- **Code — new:** `agent_as_tool()` helper; `app/db/conversation_repo.py`; conversation +
  message tables in `app/db/schema.py`; migration wiring in `app/db/migrate.py`.
- **Runtime behavior:** conversation continuity now flows through Postgres, not Redis; Redis
  is left only as the `Ctx` cache (its continuity role is removed — its remaining necessity is
  to be verified).
- **Known limits:** LlamaIndex's built-in `AgentWorkflow` caps concurrent tool execution at
  `num_workers=4` per step (no public override without a custom workflow) — acceptable for the
  coordinator's typical fan-out.
- **Docs:** `CLAUDE.md` is stale (describes the old flat `lib/agents.py`/`lib/tools.py`); update
  it to the current package layout and the new runtime as part of this change.
- **Tests:** the registry/decorator tests must follow the new APIs; add coverage for
  `agent_as_tool()` and the conversation repo.
