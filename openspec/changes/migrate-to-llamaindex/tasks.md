## 1. Dependencies & scaffolding

- [x] 1.1 Add `llama-index-core` and the LlamaIndex OpenAI LLM integration to `pyproject.toml`; run `poetry lock`; keep versions pinned
- [x] 1.2 Confirm the app still imports and `poetry run pytest` collects (baseline) before behavioral changes

## 2. Core runtime primitives

- [x] 2.1 Add `agent_as_tool(agent, tool_name, tool_description) -> FunctionTool` helper (wraps `agent.run()` in an async closure carrying the workflow `Context` + `prompt: str`)
- [x] 2.2 Adapt `lib/core/registry.py` so `@agents_decorator` / `@tool_ownership` / `@llm_usage` build LlamaIndex `FunctionAgent`s and tools; keep `environment_preamble()` prefix and `agent_enabled()`
- [x] 2.3 Update `lib/core/llm.py` factories to return LlamaIndex LLM instances per agent (model + reasoning effort; coordinator reasoning + disable sentinel; `news_agent` reasoning); wire `allow_parallel_tool_calls` for the coordinator
- [x] 2.4 Decide `Ctx` carriage on the workflow `Context` (default: opaque handle) in `lib/core/cache.py`; expose an accessor tools use

## 3. Spike: one agent end-to-end

- [x] 3.1 Port `weather_agent` (`agent.py` + `tools.py`) to LlamaIndex; change tool signatures from `RunContextWrapper[Ctx]` to the LlamaIndex `Context` accessor
- [x] 3.2 Verify `Context` auto-injection into the `agent_as_tool()` closure and a live tool call works; lock the porting pattern
- [x] 3.3 Verify multi-tool fan-out runs concurrently (e.g. a query hitting two tools) and note the `num_workers=4` cap behavior

## 4. Port remaining agents

- [x] 4.1 Port sub-agents: `finance_agent`, `maps_agent`, `news_agent`, `iot_operator`, `memory_operator`, `fpl_agent`, `scheduler_agent` (each `agent.py` + `tools.py`)
- [x] 4.2 Port the `coordinator` (`lib/agents/coordinator/agent.py`): build tools via `agent_as_tool()`, remove `handoffs`, adjust instructions so it only gathers (no self-authored reply)
- [x] 4.3 Port the `composer` (`lib/agents/composer/agent.py`) to LlamaIndex; keep Polish-reply instructions
- [x] 4.4 Confirm the server-side-handles convention still holds (model sees device names / place aliases only; raw keys/addresses resolved server-side)

## 5. Conversation history store

- [x] 5.1 Add `conversations` + `messages` tables to `app/db/schema.py` (per design: uuid PKs, `channel_key` unique, role check, `turn_index`, `mlflow_trace_id` nullable)
- [x] 5.2 Implement `app/db/conversation_repo.py` (Postgres when `DATABASE_URL` set, JSON fallback otherwise): resolve/create conversation, load ordered history, append user+assistant messages
- [x] 5.3 Wire the new tables into `app/db/migrate.py`; run the migration against a dev DB
- [x] 5.4 Add a JSON store path for conversations under `data/` (gitignored, `.gitkeep`) mirroring memory/scheduler stores

## 6. Engine turn flow

- [x] 6.1 Rewrite `lib/runtime/engine.py` `handle_message`: load history → run coordinator with `chat_history` → run composer deterministically → return reply
- [x] 6.2 Persist user message + final assistant reply per turn via `conversation_repo`; keep per-channel `conversation_id` -> `channel_key` mapping
- [x] 6.3 Capture the MLflow trace id after the run (when tracing enabled) and stamp it on the assistant message row; empty when disabled
- [x] 6.4 Remove `previous_response_id` handling and `use_responses=True`; simplify/retire `lib/runtime/run_config.py`; port `lib/runtime/hooks.py` to the LlamaIndex equivalent (or drop if unused)
- [x] 6.5 Preserve the system-origin (scheduled) turn path (`origin="system"` marker) through the new flow

## 7. Tests

- [x] 7.1 Update registry/decorator tests to the new LlamaIndex-based APIs
- [x] 7.2 Add unit tests for `agent_as_tool()` (name/description/signature; returns a `FunctionTool`)
- [x] 7.3 Add tests for `conversation_repo` (create, ordered load, append user+assistant, JSON-fallback path) without requiring a live DB
- [x] 7.4 Run `poetry run pytest` green

## 8. Verification & docs

- [x] 8.1 Manual smoke test in the REPL: single-domain turn, multi-domain (parallel) turn, and a multi-turn conversation that references earlier context (continuity via Postgres)
- [x] 8.2 Confirm a sub-agent failure still yields a graceful Polish reply via the composer
- [x] 8.3 Audit remaining `Ctx.cache` (Redis) users after removing continuity; note whether Redis is still required
- [x] 8.4 Update `CLAUDE.md`: current package layout (`lib/agents/<name>/`, `lib/core/`, `lib/domain/`, `lib/runtime/`) and the LlamaIndex runtime, `agent_as_tool`, deterministic composer, Postgres-backed history
- [x] 8.5 Run `openspec validate migrate-to-llamaindex --strict` and fix any issues
