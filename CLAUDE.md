# Jarvis — Personal Multi-Agent Assistant

## What this project is

Jarvis is a **personal AI assistant built as a multi-agent network**. A central
**coordinator** agent talks to the user and delegates work to specialized
**sub-agents**, each equipped with real-world tools (smart-home control, maps,
weather, finance, news search). The long-term goal is an assistant the owner can
talk to from their phone, that **remembers preferences and adapts over time**,
and that can **run recurring jobs on its own** (e.g. a morning news brief pushed
without being asked).

**Owner / single user:** this is a personal assistant for one person, not a
multi-tenant product. Defaults are tailored to the owner (Warsaw, Poland; Polish
language for user-facing replies; PLN as base currency).

### Vision (end state we are building toward)
1. **Chat from the phone** — a messaging front-end. Original plan was Telegram;
   preferred target is **Messenger or iMessage**. Today the only interface is a
   terminal REPL.
2. **Long-term memory** — a `memory_operator` agent that stores/refines user
   preferences and habits so the assistant personalizes and anticipates.
3. **Proactive scheduling** — the assistant can create recurring/one-off jobs
   (cron-like) that act as an *alternate entry path* into it: e.g. "every morning
   check the news and send me a press brief." The memory agent is expected to be
   able to edit the scheduler to set reminders.

## Architecture

Built on **LlamaIndex** (`llama-index-core`): every agent is a `FunctionAgent` run
via `agent.run()`, and each sub-agent is exposed to the coordinator as a
`FunctionTool` by the `agent_as_tool()` helper (replacing the old `Agent.as_tool()`).
Per-agent models come from the `LLM_BY_AGENT` registry and are **provider-agnostic**:
each agent's model is a `provider/model` string (bare → `openai`), resolved through a
provider registry in `lib/core/llm.py` (`openai` → `OpenAIResponses`, `anthropic` →
native `Anthropic`, `openrouter`/`groq`/`xai`/`openai-compat` → `OpenAILike`), and which
model each agent uses comes from a named YAML model profile (`config/models/`). See
"Models / providers" below. Conversation continuity is owned by us in Postgres
(`lib/domain/conversation.py`), not by the OpenAI Responses API's `previous_response_id`.
(The project migrated off the OpenAI Agents SDK. The `litellm` dependency is present but
unused — a universal adapter was rejected in favor of native per-provider LLM classes for
tool-calling fidelity; left in place to avoid extra lockfile churn.)

`lib/` is layered as a strict import DAG — imports go **up only**:
`runtime → agents → {domain, core}`, `domain → core`, `core → (nothing in lib)`.
Each layer's `__init__.py` is its public surface: import across layers/features from
the package `__init__`, never from a submodule directly.

**Tools live outside this repo.** Every tool domain is its own MCP server in its own public
repo (`mcp-fpl`, `mcp-finance`, `mcp-weather`, `mcp-news`, `mcp-math`, `mcp-maps`,
`mcp-calendar`, `mcp-home`; `mcp-web` pending), built on the shared `jarvis-mcp-kit`
library. Jarvis is the **harness** and one MCP client among others (Claude Code, friends'
Claude apps). See `docs/MCP.md` and `openspec/changes/extract-tools-to-mcp-servers/`.

```
app/main.py            Entry point: load env, configure logging, run the chatbot loop
app/webhook.py         Messenger webhook + POST /internal/notify (tool servers → owner)

lib/core/              LEAF layer — no imports from elsewhere in lib
  registry.py          The 3 registries (TOOLS_BY_AGENT/AGENTS/LLM_BY_AGENT) + decorators
  cache.py             Ctx (shared run context; incl. per-turn `scratch`) + Cache (Redis; idle) +
                         app_context()/bind_app_context() contextvar accessors
  agent_tool.py        agent_as_tool() — wrap a sub-agent as a coordinator FunctionTool
  tooling.py           function_tool — LlamaIndex analogue of the old @function_tool
  mcp_client.py        McpToolset: a remote MCP server's tools as FunctionTools — cached listing
                         + server instructions, X-On-Behalf-Of per identity mode, per-turn call
                         budgets, ERROR strings on failure; refresh_mcp_toolsets() per turn
  env.py               environment_preamble() — date/time/locale block for every agent
  llm.py               Provider-agnostic LLM resolution: model-prefix parser +
                         PROVIDER_BUILDERS + build_llm(agent_name) config resolution
  model_profiles.py    Named model profiles (config/models/<MODEL_PROFILE>.yaml): the
                         only source of per-agent model/reasoning config
  custom_providers.py  Named model servers (config/providers.yaml) usable as model prefixes
  reasoning.py         Provider-normalized reasoning effort (effort enum → OpenAI
                         effort literal / Anthropic thinking budget / compat field)
  logger.py            Root logger config + custom CONVERSATION log level

lib/domain/            Shared stateful stores (used by agents AND runtime)
  memory.py            Long-term memory store (JSON or Postgres backend; see app/db)
  scheduler.py         Scheduled-jobs store (JSON or Postgres backend)
  conversation.py      Conversation-history store + rolling-window summary (JSON or Postgres)
  principals*.py, profile.py, agent_config.py   multi-user identity, groups, rosters

lib/agents/            Coordinator, composer, harness sub-agents, config-defined tool sub-agents
  __init__.py          Aggregator (registration side effects), public surface
  coordinator/         create_coordinator_agent (wires subagents in as tools; writes the answer content)
  composer/            create_composer_agent (deterministic final step: formats the answer, form only)
  memory_operator/     long-term memory sub-agent (in-repo tools)
  scheduler_agent/     reminders/briefs/conditional watches (in-repo tools; watch_sources.py
                         polls tools on the MCP servers)
  mcp_subagents/       tool sub-agents from config/subagents.yaml: one McpToolset + one
                         FunctionAgent factory per entry; prompt = server instructions + persona

lib/runtime/           TOP layer — wires the network to a transport / runs jobs
  engine.py            transport-agnostic handle_message(conversation_id, text, ctx); binds the
                         turn's user/household for MCP calls
  history_window.py    rolling-window context: last N pairs verbatim + incremental summary
  chatbot.py           REPL loop
  messenger.py         Messenger webhook adapter (signature, send)
  scheduler_runner.py  in-process loop that fires due jobs out of band (watch polls act as the owner)
  notify.py            owner_messenger_id() for /internal/notify
  progress.py          interim "still working" ping helpers (labels + templated message)
  admin.py             owner-only "/" command surface (ops/diagnostics; bypasses agents)
  diag.py              leaf: in-memory diagnostic state (uptime, last-turn, inbound)
  tracing.py           MLflow tracing setup + last_trace_id()

app/db/                Postgres access layer: connection, schema, repos, migration
                         (conversation, memory, scheduler, principals — the harness's own data)
config/subagents.yaml  Tool sub-agents: description, url/key env vars, identity, persona, budgets
config/models/         Model profiles (prod.yaml, local.yaml) — pick with MODEL_PROFILE
config/providers.yaml  Named model servers (e.g. gpu-server → local vLLM URL)
mcp-config/<server>/   Per-server env + config for the compose deployment (gitignored)
deploy/                postgres/ (per-service databases), k8s/jarvis.yaml
evals/fixtures/mcp_tools.json   MCP tool-schema snapshot for offline evals
data/                  JSON "databases" when DATABASE_URL is unset
logs/                  Per-run log files
```

### How a turn flows
`lib/runtime/engine.py` `handle_message(conversation_id, text, ctx)` is the single
entry point (REPL and Messenger both call it):
1. It binds the shared `Ctx` for the turn via `bind_app_context(ctx)` (a contextvar),
   so every tool reaches the same run state, and sets `ctx.conversation_id`.
2. It builds a **bounded, context-engineered history** for `conversation_id` via
   `lib.runtime.history_window.build_chat_history`: the last N user/assistant pairs
   verbatim (`HISTORY_WINDOW_PAIRS`, default 3), prefixed by an incrementally-maintained
   running summary (system message, Polish, ≤ `HISTORY_SUMMARY_MAX_SENTENCES` sentences,
   default 5) of everything older. `build_chat_history` is **read-only and LLM-free** — it
   assembles `[summary?] + everything since summary_upto` and returns immediately, so the
   turn never waits on compression. The fold (one small LLM call, model `history_compressor`,
   folding only the just-evicted exchange) runs **off the critical path**: the engine kicks it
   off as a fire-and-forget background task (`schedule_summary_fold`) in step 6, after the
   reply is recorded. Its output only matters for the *next* turn; if a fold hasn't finished
   by then, that turn shows the not-yet-folded exchanges verbatim (bounded to window + one
   turn's lag) and the next fold catches up. Folds are coalesced per conversation. Set
   `HISTORY_COMPRESSION_ENABLED` falsy to fall back to replaying the full history.
3. It builds a fresh coordinator (`create_coordinator_agent()`) and runs it:
   `coordinator.run(user_msg=text, chat_history=history, ctx=Context(coordinator))`.
   The coordinator calls sub-agents **as tools** (`agent_as_tool(...)`), in parallel
   when the query spans domains (LlamaIndex caps concurrent tool calls at
   `num_workers=4` per step). It then **writes the answer** in Polish: it owns the entire
   content (facts and numbers from this turn's sub-agent results only, what is unavailable,
   which actions were confirmed).
   On a **live user turn** (`origin="user"`) with a transport `notify` callback, the
   coordinator is awaited via `_gather_with_progress_ping`, which watches its event
   stream and — if the turn outlasts `PROGRESS_PING_AFTER_SECONDS` (default 20) — pushes
   **one** interim "still working" message naming the sub-agents already returned
   (templated Polish, no LLM; see `lib/runtime/progress.py`). Ordering is race-free: the
   final reply is only sent by the transport after `handle_message` returns, so the ping
   always precedes it. `notify` is a per-call parameter (not on `Ctx`) because it is wired per turn.
   **Multi-user:** the engine resolves the acting principal (from `user_id`) into a
   profile (roster + per-agent models) and composes a **per-turn** `Ctx` scoped to that
   principal — a cached group scope (shared devices/maps) + fresh user scope (memory,
   conversation, profile) — so `create_coordinator_agent(profile)` exposes only that
   user's rostered subagents. See docs/MULTI_USER.md.
4. It then runs the **composer deterministically** (`composer.run(...)`) — a code step,
   not an LLM decision — which only **formats** the coordinator's answer as natural plain
   Polish chat text (no Markdown). It is a form-only editor: it gets just that answer (no
   user message, no history) and must not add, remove or correct content. When the
   coordinator hits its deadline, the answer is a fixed notice plus the sub-agent results
   that arrived (`_partial_gather`), so the composer still only formats.
5. It persists the turn (user message + assistant reply) via `conversations.record_turn`,
   stamping the assistant row with the MLflow trace id (`last_trace_id()`, empty when
   tracing is off).
6. It schedules the rolling-summary fold as a background task (`schedule_summary_fold`, see
   step 2) — off the critical path, so the reply above was never blocked on the compressor
   LLM call — and returns the reply.

Conversation continuity is **ours** (Postgres/JSON), not the OpenAI Responses API's
`previous_response_id` — that mechanism, along with `RunConfig`/`Runner` and the run
hooks, is gone.

### Admin / ops command surface
Messages starting with `/` are intercepted **before** the agent stack — in
`app/webhook.py` `_process` and the REPL loop — by `lib.runtime.admin.maybe_handle`, which
returns a reply string (handled deterministically, no LLM) or `None` to fall through to
the coordinator. This is a technical window into the running service from the phone
(`/stop` to cancel the in-flight turn, `/status`, `/logs`, `/errors`, `/jobs [all]` (tags
each item's kind — cron vs one-shot vs repeating/one-shot watch — for verification; `all`
also shows completed/cancelled),
`/fire <id>`, `/scheduler pause|resume`, `/mcp [keys|revoke <id>]` (tool-server status, FPL
self-service keys), `/user`, `/group`, `/profile <user>` (multi-user management — see
docs/MULTI_USER.md), `/agents`, `/models`, `/env` with masked secrets,
`/memory`, `/summary`, `/reset confirm`, `/last`, `/window`, `/version`, `/probe`, `/help`).
`/stop` works because the webhook runs each inbound message as its own task: a live turn
registers its `asyncio.Task` in `diag`, and the concurrently-arriving `/stop` cancels it. Bypassing the agents is deliberate: the
diagnostics must work when the LLM/coordinator is the thing that's broken. Access is
restricted to **admins** — the owner plus members of the `admins` group (`is_admin()` in
`lib/domain/principals.py`, enforced in the webhook before the command runs; REPL is the
local owner). See docs/MULTI_USER.md.
Output is plain text formatted for a mobile chat bubble (no Markdown). In-memory
diagnostic state (uptime, last-turn stats, the `/window` inbound tracker, the scheduler
heartbeat/pause) lives in `lib/runtime/diag.py` + `scheduler_runner` and resets on restart.
Add a command by registering an `async` handler in `admin._COMMANDS` (`/help` is generated
from that metadata). See `docs/ADMIN.md`.

### Registration patterns (important conventions)
The three decorator-backed registries now all live in `lib/core/registry.py` (imported
as `from lib.core import ...`); registration happens by **import-time side effect**, so a
tool/agent module must be imported for its decorator to fire. `lib/agents/__init__.py`
imports every feature subpackage precisely to trigger this. Follow them when adding things:

- **Tool sub-agents (the usual case)** — **no Python code in this repo**: build an MCP server
  in its own repo with `jarvis-mcp-kit` (tools = documented async functions; the tool guide
  goes in the server's `instructions` and tool docstrings), then add an entry to
  `config/subagents.yaml` (description, `url_env`, `key_env`, `identity`, optional `persona`
  and `max_calls_per_turn`) and the URL/key to `.env`. `lib/agents/mcp_subagents` registers
  an `McpToolset` and an `AGENTS` factory for it; the coordinator includes it when the
  principal's roster enables it and its server has been listed.
- **Harness agents** (memory, scheduler) — `@agents_decorator(name)` registers a factory into
  `AGENTS` and prepends the environment preamble; the package has `agent.py` + `tools.py` +
  `__init__.py`, plus an import in `lib/agents/__init__.py` and the coordinator catalog.
- **Harness tools** — `@tool_ownership(agent_name)` **above** `@function_tool` appends the
  tool to `TOOLS_BY_AGENT[agent_name]`. Tool functions take `ctx: Context` (auto-injected,
  hidden from the model's schema) and reach shared run state via `app_context(ctx)`.
- **Models** — every agent reads its LLM via `LLM_BY_AGENT[name]()` and passes it as
  `llm=`. There is **no per-agent factory to write**: `LLM_BY_AGENT` falls back to a
  config-driven resolver (`build_llm(agent_name)`, bound via `bind_default_llm_resolver`)
  that reads the agent's entry in the active model profile (`config/models/*.yaml`) and dispatches through
  `PROVIDER_BUILDERS`. `@llm_usage([agent_names])` in `lib/core/llm.py` remains available
  as an explicit, code-level override (it wins over the resolver).

### Shared context
`Ctx` (`lib/core/cache.py`, imported as `from lib.core import Ctx`) is the per-turn
shared state. `engine.handle_message` binds it for the turn with `bind_app_context(ctx)`
(a contextvar), and every tool reaches it via `app = app_context(ctx)` — e.g.
`app.user_id`, `app.conversation_id`, `app.scratch` (per-turn counters such as MCP call
budgets). Device handles and saved addresses now live in the `mcp-home` / `mcp-maps`
servers; the old `devices`/`known_adresses` group-scope fields are unused leftovers. The contextvar propagates to the coordinator's step-worker tasks
and to every nested sub-agent run, so state written by one tool is visible to any later
tool in the same turn. (The workflow `Context` itself is *not* used to carry `Ctx`: its
store is serialized and `Ctx` — Redis client, live device handles — is not serializable,
and arbitrary Context attributes don't reach the tool's injected Context.) `Ctx` also holds a Redis `Cache`, which is now **idle**:
its only user was the removed `previous_response_id` continuity, so Redis is optional
(nothing calls its methods; a missing Redis no longer breaks a turn). It was left in
place rather than removed.

## Agents & their status

| Agent | Where its tools run | Notes |
|-------|---------------------|-------|
| `coordinator` | — (sub-agents as tools) | Routes, gathers, writes the answer content |
| `composer` | — | Deterministic final formatting step |
| `memory_operator` | in-repo (`lib/domain/memory.py`) | Memory profile injected into the coordinator prompt (cached, `MEMORY_SUMMARY_CACHE_TTL_SECONDS`). See `docs/MEMORY.md` |
| `scheduler_agent` | in-repo (`lib/domain/scheduler.py`) | Reminders, briefs, conditional watches (edge-triggered, `_previous` reading, `repeating`); due jobs fire via `scheduler_runner`; watch sources call MCP tools as the watch owner. See `docs/SCHEDULER.md` |
| `fpl_agent` | `mcp-fpl` (public: `/mcp/fpl`, `/mcp/join`) | "My team/league" from the caller's key; own Postgres db (caches, daily league history, self-service keys); CronJobs `collect`/`warm-cache` |
| `finance_agent` | `mcp-finance` | FX, quotes, ticker search, daily history (own db; `collect` CronJob) |
| `weather_agent` | `mcp-weather` | OpenWeather + Open-Meteo |
| `news_agent` | `mcp-news` | Tavily (official client) |
| `math_agent` | `mcp-math` | Exact statistics over figures from other agents |
| `maps_agent` | `mcp-maps` (`identity: group`) | Saved places per household, aliases only |
| `calendar_agent` | `mcp-calendar` (`identity: user`) | iCloud CalDAV account per user; gated delete |
| `iot_operator` | `mcp-home` on the home network (`identity: group`) | Tuya devices per household; reached via host networking (compose) or the WireGuard `home-gateway` (k3s) |
| `web_agent` | pending `mcp-web` | Lives on the `web-browsing` branch until extracted |

## Running it

Requires a `.env` (copy from `.env_template`) and Poetry. Postgres (docker-compose
service) is used for conversation history, memory and scheduled jobs when
`DATABASE_URL` is set (otherwise on-disk JSON under `data/`). **Redis** is now
optional — after the LlamaIndex migration nothing uses the cache — but the
docker-compose service and the default `Cache()` remain harmlessly in place.

```bash
# 1. Fill in .env (see .env_template for required keys)
# 2. Start Redis (locally or via docker compose)
docker compose up -d redis
# 3. Install deps and run
poetry install
poetry run python app/main.py
```

`docker-compose.yml` runs the whole stack: Jarvis (Messenger webhook), Postgres, Redis,
MLflow, Caddy and every MCP server from its published image (`ghcr.io/jaknow01/mcp-*`), with
per-server config in `mcp-config/<server>/` — see `docs/MCP.md` for the setup steps (keys,
databases, backfills, collector crontab). Without the tool servers, Jarvis still runs but its
tool sub-agents are absent from the roster.

### Models / providers
Each agent's model is a `provider/model` string; the provider is the part before the
**first** `/` (so OpenRouter ids like `openrouter/deepseek/deepseek-r1` keep their inner
slash), and a **bare** model (no `/`) means provider `openai` — backward-compatible with
old bare model names. `lib/core/llm.py` parses the prefix and dispatches through a
provider registry:

| provider | builder | notes |
|----------|---------|-------|
| `openai` | `OpenAIResponses` | `/responses` — tools + reasoning together |
| `anthropic` | native `Anthropic` | extended thinking (`thinking_dict`) |
| `openrouter` / `groq` / `xai` | `OpenAILike(api_base=…)` | OpenAI-compatible |
| `openai-compat` | `OpenAILike(api_base=OPENAI_COMPAT_BASE_URL)` | ad-hoc escape hatch |
| *named server* (`config/providers.yaml`) | `OpenAILike(api_base=its base_url)` | self-hosted vLLM/Ollama |

**Named servers** (`lib/core/custom_providers.py`): `config/providers.yaml` maps a provider
name to a server — `gpu-server: {type: openai-compat, base_url: …, api_key_env: …}` — so
`gpu-server/RedHatAI/Qwen3.8-27B-INT4` means "that model on that machine". Several local
models = several servers (or several model ids on one server); addresses live once in that
shared file and profiles/sweeps name only models. Optional (absent → no custom providers;
path overridable with `PROVIDERS_CONFIG`), re-read on change, validated (names may not
redefine a built-in provider; only `openai-compat` type for now). `api_key_env` names the
env var holding a key; omitted → placeholder `EMPTY` for an unauthenticated server.

**Model profiles are the only source of model config** (`lib/core/model_profiles.py`). A
profile is a YAML file `config/models/<name>.yaml` (committed; no secrets — API keys stay in
`.env`); the active one is picked at startup by `MODEL_PROFILE` (default `prod`; dir
overridable with `MODEL_PROFILES_DIR`). Shipped: `prod` (everything on
DeepSeek via OpenRouter except the composer on gpt-4.1-mini) and `local` (everything on `gpu-server`).
Schema: `description`, `default` (model / reasoning / thinking_budget for every agent not
listed), `agents.<name>` (merged field by field over `default`; a bare string is the model
only — this includes `coordinator`, `composer`, `history_compressor`, `watch_coder`). A
profile names models only — where a self-hosted model runs is its provider prefix (a named
server). Unknown keys are rejected. The file is re-read when it changes
(mtime). Resolution per agent: per-principal override (`principals.yaml` → `agent_config`)
→ profile `agents.<name>` → profile `default`; an unset reasoning means none. `/models`
shows the active profile. The old `DEFAULT_MODEL` / `AGENT_<NAME>_MODEL` /
`*_REASONING_EFFORT` / `*_THINKING_BUDGET` / `COORDINATOR_MODEL` / `OPENAI_DEFAULT_MODEL`
env vars are no longer read. `using_profile(profile)` forces a profile in-process
(contextvar) — the eval harness uses it to sweep models.

Reasoning effort is one vocabulary — `off`/`minimal`/`low`/`medium`/`high` (plus the
disable sentinels `none`/`off`/`no`/`false`/`disabled`/empty) — normalized per provider in
`lib/core/reasoning.py`:

| effort | OpenAI (Responses) | Anthropic (native) | OpenAI-compatible |
|--------|--------------------|--------------------|-------------------|
| off | omit `reasoning_options` | thinking off | omit |
| minimal/low/medium/high | `reasoning_options={"effort": …}` | `thinking_dict` budget from `EFFORT_BUDGET` (1k/4k/12k/32k) | `reasoning_effort=…` |

For Anthropic thinking the builder enforces the API's constraints: `temperature=1` and
`max_tokens` > budget; a profile entry's `thinking_budget` overrides the table.

### Environment variables (`.env`)
`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `OPENROUTER_API_KEY` (provider keys — only the
ones you use are required; named servers read the env var their `api_key_env` names, e.g.
`OPENAI_COMPAT_API_KEY`; the ad-hoc `openai-compat` provider reads `OPENAI_COMPAT_BASE_URL` +
`OPENAI_COMPAT_API_KEY`), `MODEL_PROFILE` (+ optional
`MODEL_PROFILES_DIR`) selecting the model profile (see "Models / providers" above),
`XAI_API_KEY`, `DATABASE_URL` (Postgres; when unset the stores fall back to on-disk JSON),
`TRACING_ENABLED` + `MLFLOW_TRACKING_URI` + `MLFLOW_EXPERIMENT` (agent tracing; when the URI
is unset or `TRACING_ENABLED` is falsy, tracing is a no-op). External tool API keys
(OpenWeather, Tavily, Google Maps, iCloud, Tuya) and FPL/finance settings are **not** Jarvis
env vars any more — they belong to the servers (`mcp-config/<server>/.env`).
`SCHEDULER_ENABLED` + `SCHEDULER_TICK_SECONDS` + `SCHEDULER_TIMEZONE` (the proactive
scheduler loop: master switch — same falsy-parsing as `agent_enabled()`, default on;
tick interval in seconds, default 30; timezone for cron/`run_at`, default Europe/Warsaw.
Proactive delivery reuses `MESSENGER_PAGE_ACCESS_TOKEN`). See `docs/SCHEDULER.md`.
`PROGRESS_PING_ENABLED` + `PROGRESS_PING_AFTER_SECONDS` (the interim "still working"
ping on slow live turns: master switch — same falsy-parsing as `agent_enabled()`, default
on; delay before the single ping fires in seconds, default 20. See `lib/runtime/progress.py`
and the turn-flow note above).
`SUBAGENT_TIMEOUT_SECONDS` + `COORDINATOR_TIMEOUT_SECONDS` + `LLM_REQUEST_TIMEOUT_SECONDS` +
`LLM_MAX_RETRIES` (turn time limits: wall-clock cap per sub-agent run in `agent_as_tool`,
default 120 — on timeout the run is cancelled and the coordinator gets an error string telling
it to continue without that agent; wall-clock cap on the coordinator's gather phase in the
engine, default 300 — past it the run is cancelled and the composer replies from the sub-agent
results collected so far; `0` disables either cap. The last two are per-HTTP-request limits set
on every provider client, defaults 60 s / 2 retries — per-read only, which is why the wall-clock
caps exist). `COORDINATOR_FORCE_TOOL` (default on): the coordinator is built with
`initial_tool_choice="required"` so its first step must call a tool — it delegates instead
of answering from history (weaker/local models otherwise invent figures on follow-ups). Pure
small talk goes through the `no_data_needed` escape tool (`coordinator/no_data.py`); when that
is the only call in a turn the engine replaces the coordinator's answer with a fixed one per
declared kind (`_guard_no_data_turn`), so misuse can't leak invented data. The
native Anthropic class is wrapped (`ToolChoiceAnthropic` in `lib/core/llm.py`) to translate
`tool_choice` into Anthropic's `tool_required`. Every sub-agent prompt also gets
`SUBAGENT_TOOL_RULES` appended by `agent_as_tool` (tools only, sanity-check results, retry
once then report). Harness tools must not block the event loop (`asyncio.to_thread` for
blocking clients). Daily collectors (finance 23:00, FPL league snapshot 04:00, FPL cache
warming) run inside `mcp-finance` / `mcp-fpl` as one-shot commands (cron / k8s CronJobs).

**Agent tracing (dev observability):** setting `MLFLOW_TRACKING_URI` enables MLflow
auto-tracing for the LlamaIndex runtime (`mlflow.llama_index.autolog()` in
`lib/runtime/tracing.py`, called once from `app/main.py`), capturing each
coordinator→sub-agent→tool run as a hierarchical trace. The turn's trace id is read
back with `last_trace_id()` and stored on the assistant message row. The docker-compose `mlflow` service runs the tracking server + UI (open
http://localhost:5001 → "Traces"; host port 5001 avoids the macOS :5000 AirPlay
conflict). The app uses the lightweight `mlflow-tracing` client (HTTP → server, no
local store) on purpose, to avoid churning the lockfile. `TRACING_ENABLED` (same
falsy-parsing as `agent_enabled()`) is a master kill-switch that disables tracing
even with a URI set. Tracing is out-of-band and failures are swallowed, so it never
affects agent behavior. See `docs/TRACING.md`.

**MCP tool servers (Jarvis as client):** per server `MCP_<NAME>_URL` + `MCP_<NAME>_KEY`
(names declared in `config/subagents.yaml`; compose sets the in-network URLs), plus
`MCP_TOOLS_TTL_SECONDS` (300), `MCP_RETRY_SECONDS` (30), `MCP_LIST_TIMEOUT_SECONDS` (10),
`MCP_CALL_TIMEOUT_SECONDS` (100), `SUBAGENTS_CONFIG`, and `INTERNAL_NOTIFY_TOKEN` (bearer for
`POST /internal/notify`, which servers use to message the owner; blocked publicly by Caddy).
Jarvis's key on each server must be `trusted: true` (and `admin: true` on mcp-fpl for
`/mcp revoke`). See `docs/MCP.md`.

**Per-subagent toggles:** each subagent is enabled by default and can be hidden from
the coordinator by setting `AGENT_<NAME>_ENABLED` to a falsy value (0/false/no/off) —
e.g. `AGENT_NEWS_AGENT_ENABLED=false`. A disabled agent is simply not registered as a
coordinator tool (`agent_enabled()` in `lib/core/registry.py`, read at coordinator-build
time). Disabling `memory_operator` also drops the memory profile injection.

### Testing
`pytest` (dev dependency) with tests under `tests/`. Run `poetry run pytest`. No network or
API keys required; MCP sub-agents are tested with installed tool snapshots
(`tests/conftest_mcp.py`). Tool behaviour is tested in each server's own repo (incl. a parity
test against the pre-extraction tool schemas). Evals run against
`evals/fixtures/mcp_tools.json` (refresh: `python -m evals.snapshot_tools`).

## Conventions & gotchas
- **User-facing replies are in Polish**; tool/agent *instructions* and code are in
  English. Keep that split.
- **Tool docstrings are the tool spec** the LLM sees — write them carefully
  (description, parameters, output, notes). Match the existing detailed style.
- **Async everywhere.** Tools are `async` and should run I/O concurrently with
  `asyncio.gather` (see `iot_operator` tools). A design goal in `TODO.txt`: agent
  factory methods should become async so all agents can be created concurrently.
- **Logging, not prints.** `lib/core/logger.py` adds a custom `CONVERSATION` level
  (25) via `logger.conversation(...)`. Legacy `print()` calls still linger in
  older modules — prefer `logger` for new code and
  migrate prints when you touch them.
- **Sensitive data stays server-side; the model uses handles, not raw values.** Enforced
  in the tool servers: `mcp-home` exposes devices by **name** only (no ip / local_key /
  dev_id), `mcp-maps` saved places by **alias** only (addresses resolved and relabelled on
  the server), `mcp-calendar` event summaries/UIDs only. Personal-data servers scope data to
  the acting principal (`X-On-Behalf-Of` from Jarvis's trusted key; `identity` in
  `config/subagents.yaml`); an unknown principal gets nothing. External API secrets live only
  with their server; Jarvis holds just its MCP keys.
- **Storage** — Jarvis's own data (conversations + rolling summary, memory, scheduled jobs,
  principals) lives in **Postgres** (`jarvis` database) when `DATABASE_URL` is set, else in
  JSON under `data/`; the DB layer is isolated in `app/db/` (same JSON/Postgres backend
  selection everywhere). The per-turn hot-path read uses `load_history_and_summary()` (one
  connection). Tool-server data lives in **their own databases** on the same instance
  (`mcp_fpl`: FPL reference/league caches, daily league history, self-service keys;
  `mcp_finance`: daily observations) — see `deploy/postgres/README.md`. The old `fpl_*`,
  `observations` and `mcp_api_keys` tables in the `jarvis` database are unused and can be
  dropped after the cut-over. Migrate with `python -m app.db.migrate`; see `docs/STORAGE.md`.
  JSON stores under `data/` are gitignored except `.gitkeep`.
- **`.venv/` and `venv/`** both exist in the working tree but are gitignored; the
  canonical dependency source of truth is `pyproject.toml` + `poetry.lock`.
- **Don't churn dependencies casually** — the lockfile was painful to stabilize
  (see README).

## Roadmap (from `TODO.txt` + vision)
Rough priority order to reach a usable end state:
1. **Messaging front-end** (Messenger / iMessage / Telegram) replacing the REPL —
   likely a long-running service that receives messages over HTTP/webhook and
   feeds them into the same coordinator run loop.
2. ~~**Memory agent**~~ — **done (v1):** structured JSON store (`lib/domain/memory.py`),
   save/get/update/delete tools, wired into the coordinator with a profile injected
   into its prompt each turn. See `docs/MEMORY.md`. Next: implicit habit learning,
   and migrating scattered assumptions (e.g. the maps "fast walker") into it.
3. ~~**Scheduler**~~ — **done (v1):** a `scheduler_agent` creates cron-like jobs
   (`lib/domain/scheduler.py`, JSON/Postgres store), and `lib/runtime/scheduler_runner.py` — an
   in-process asyncio loop started in the webhook `lifespan` — fires due jobs "out of
   band" by re-entering `engine.handle_message(..., origin="system")` and delivering
   the reply (Messenger `HUMAN_AGENT` tag, or the log). See `docs/SCHEDULER.md`. Next:
   richer NL→schedule parsing, editing jobs from other channels, One-Time Notification.
4. **Deepen agents** — e.g. finance analysis beyond raw quotes, richer memory.

Done in this pass (polish): implemented `get_current_date_and_time`, added Yahoo
Finance stock/index quotes (incl. GPW) to `finance_agent`, migrated the remaining
`print()` calls to `logger`, and added a `pytest` suite.

**Chosen direction for the phone front-end:** *architecture first* — before
committing to a channel (Messenger / iMessage / Telegram), decouple the core into a
service with a clean input/output interface so any channel can plug in later. The
current `Chatbot` REPL (`lib/runtime/chatbot.py`) is the seam to generalize: extract a
transport-agnostic "handle one message → return one reply" entry point, then add
channel adapters on top.

## Git / branch state
All feature branches (`logger`, `smarts`, `financial-agent`, `weather-agent`,
`news-agent`, `openai-agents-sdk-swith`) have been **merged into `main`**. `main`
is the single source of truth going forward; create new feature branches off it.

### Commit message convention
**Do NOT append a `Co-Authored-By: Claude ...` trailer** (or any AI co-author
trailer) to commit messages in this repo. Keep commit messages clean without it.
