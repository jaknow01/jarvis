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
native `Anthropic`, `openrouter`/`groq`/`xai`/`openai-compat` → `OpenAILike`). See
"Models / providers" below. Conversation continuity is owned by us in Postgres
(`lib/domain/conversation.py`), not by the OpenAI Responses API's `previous_response_id`.
(The project migrated off the OpenAI Agents SDK. The `litellm` dependency is present but
unused — a universal adapter was rejected in favor of native per-provider LLM classes for
tool-calling fidelity; left in place to avoid extra lockfile churn.)

`lib/` is layered as a strict import DAG — imports go **up only**:
`runtime → agents → {domain, core}`, `domain → core`, `core → (nothing in lib)`.
Each layer's `__init__.py` is its public surface: import across layers/features from
the package `__init__`, never from a submodule directly.

```
app/main.py            Entry point: load env, configure logging, run the chatbot loop

lib/core/              LEAF layer — no imports from elsewhere in lib
  registry.py          The 3 registries (TOOLS_BY_AGENT/AGENTS/LLM_BY_AGENT) + decorators
  cache.py             Ctx (shared run context) + Cache (Redis; now idle/optional) +
                         app_context()/bind_app_context() contextvar accessors
  agent_tool.py        agent_as_tool() — wrap a sub-agent as a coordinator FunctionTool
  tooling.py           function_tool — LlamaIndex analogue of the old @function_tool
  env.py               environment_preamble() — date/time/locale block for every agent
  llm.py               Provider-agnostic LLM resolution: model-prefix parser +
                         PROVIDER_BUILDERS + build_llm(agent_name) config resolution
  reasoning.py         Provider-normalized reasoning effort (effort enum → OpenAI
                         effort literal / Anthropic thinking budget / compat field)
  logger.py            Root logger config + custom CONVERSATION log level

lib/domain/            Shared stateful stores (used by agents AND runtime)
  memory.py            Long-term memory store (JSON or Postgres backend; see app/db)
  scheduler.py         Scheduled-jobs store (JSON or Postgres backend)
  conversation.py      Conversation-history store + rolling-window summary (JSON or Postgres)
  observations.py      Daily time-series store for external readings (JSON or Postgres; see docs/OBSERVATIONS.md)

lib/agents/            Coordinator + one feature subpackage per agent
  __init__.py          Aggregator: imports every subpackage (registration), public surface
  coordinator/         create_coordinator_agent (wires subagents in as tools; gathers only)
  composer/            create_composer_agent (deterministic final step: writes the reply)
  <agent>/agent.py     the create_*_agent() factory (returns a FunctionAgent)
  <agent>/tools.py     that agent's @tool_ownership function-tools
  <agent>/<helper>.py  that agent's private helpers, e.g.
                         iot_operator/{device,tuya_link}.py (Tuya model + link),
                         maps_agent/directions.py, weather_agent/forecast.py,
                         finance_agent/{fx.py, history.py}, fpl_agent/fpl_api.py,
                         math_agent/ (deterministic calc tools; no data source of its own)

lib/runtime/           TOP layer — wires the network to a transport / runs jobs
  engine.py            transport-agnostic handle_message(conversation_id, text, ctx)
  history_window.py    rolling-window context: last N pairs verbatim + incremental summary
  chatbot.py           REPL loop
  messenger.py         Messenger webhook adapter (signature, send)
  scheduler_runner.py  in-process loop that fires due jobs out of band
  observations_runner.py daily time-series collectors (finance @23:00, FPL league @04:00)
  progress.py          interim "still working" ping helpers (labels + templated message)
  admin.py             owner-only "/" command surface (ops/diagnostics; bypasses agents)
  diag.py              leaf: in-memory diagnostic state (uptime, last-turn, inbound)
  tracing.py           MLflow tracing setup + last_trace_id()

app/db/                Postgres access layer: connection, schema, repos, migration
                         (incl. conversation_repo.py — conversations + messages;
                         observations_repo.py — daily time-series;
                         fpl_history_repo.py — permanent daily FPL league snapshots)
scripts/               One-shot ops scripts: backfill_finance.py (year of daily finance history),
                         backfill_fpl_history.py (per-gameweek FPL league history)
data/                  JSON "databases" (devices, preferences, maps memory, memory)
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
   `num_workers=4` per step). It **only gathers** — it never writes the user's reply.
   On a **live user turn** (`origin="user"`) with a transport `notify` callback, the
   coordinator is awaited via `_gather_with_progress_ping`, which watches its event
   stream and — if the turn outlasts `PROGRESS_PING_AFTER_SECONDS` (default 20) — pushes
   **one** interim "still working" message naming the sub-agents already returned
   (templated Polish, no LLM; see `lib/runtime/progress.py`). Ordering is race-free: the
   final reply is only sent by the transport after `handle_message` returns, so the ping
   always precedes it. `notify` is a per-call parameter (not on `Ctx`) because one `Ctx`
   is shared across concurrent Messenger senders.
4. It then runs the **composer deterministically** (`composer.run(...)`) on the
   gathered results — a code step, not an LLM decision — which writes the final reply
   in Polish.
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
`/fire <id>`, `/scheduler pause|resume`, `/collect [finance|fpl]` (run the daily observation
collectors now), `/agents`, `/models`, `/env` with masked secrets,
`/memory`, `/summary`, `/reset confirm`, `/last`, `/window`, `/version`, `/probe`, `/help`).
`/stop` works because the webhook runs each inbound message as its own task: a live turn
registers its `asyncio.Task` in `diag`, and the concurrently-arriving `/stop` cancels it. Bypassing the agents is deliberate: the
diagnostics must work when the LLM/coordinator is the thing that's broken. It's implicitly
owner-only (the webhook already gates on `MESSENGER_ALLOWED_SENDER_ID`; REPL is local).
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

- **Agents** — `@agents_decorator(name)` registers a factory into `AGENTS` and prepends
  the environment preamble to the agent's `system_prompt`. Each sub-agent lives in
  `lib/agents/<name>/agent.py` as a `create_*_agent()` factory returning a
  LlamaIndex `FunctionAgent`; the coordinator wires them in via
  `agent_as_tool(factory(), tool_name=, tool_description=)`. A new agent needs a package
  with `agent.py` + `tools.py` + an `__init__.py` (importing `.tools` for the side effect
  and re-exporting the factory), plus one import line in `lib/agents/__init__.py` and the
  coordinator's `subagents` list.
- **Tools** — `@tool_ownership(agent_name)` **above** `@function_tool` (the LlamaIndex
  one from `lib.core`) in that agent's `lib/agents/<name>/tools.py` appends the tool to
  `TOOLS_BY_AGENT[agent_name]`. An agent picks up its tools with `tools = TOOLS_BY_AGENT[name]`.
  Decorator order matters: `@tool_ownership` outermost, `@function_tool` innermost. Tool
  functions take `ctx: Context` (the LlamaIndex workflow Context, auto-injected and hidden
  from the model's schema) and reach shared run state via `app_context(ctx)`.
- **Models** — every agent reads its LLM via `LLM_BY_AGENT[name]()` and passes it as
  `llm=`. There is **no per-agent factory to write**: `LLM_BY_AGENT` falls back to a
  config-driven resolver (`build_llm(agent_name)`, bound via `bind_default_llm_resolver`)
  that reads `AGENT_<NAME>_MODEL`/`AGENT_<NAME>_REASONING_EFFORT` and dispatches through
  `PROVIDER_BUILDERS`. `@llm_usage([agent_names])` in `lib/core/llm.py` remains available
  as an explicit, code-level override (it wins over the resolver).

### Shared context
`Ctx` (`lib/core/cache.py`, imported as `from lib.core import Ctx`) is the per-turn
shared state. `engine.handle_message` binds it for the turn with `bind_app_context(ctx)`
(a contextvar), and every tool reaches it via `app = app_context(ctx)` — reading/writing
`app.devices`, `app.devices_preferences`, `app.known_adresses`, `app.time_date_now`,
`app.conversation_id`. The contextvar propagates to the coordinator's step-worker tasks
and to every nested sub-agent run, so state written by one tool is visible to any later
tool in the same turn. (The workflow `Context` itself is *not* used to carry `Ctx`: its
store is serialized and `Ctx` — Redis client, live device handles — is not serializable,
and arbitrary Context attributes don't reach the tool's injected Context.) `Ctx.devices`
is typed as `dict[str, Any]` (not `SmartDevice`) on purpose, so `core` stays a leaf and
never imports the iot domain. `Ctx` also holds a Redis `Cache`, which is now **idle**:
its only user was the removed `previous_response_id` continuity, so Redis is optional
(nothing calls its methods; a missing Redis no longer breaks a turn). It was left in
place rather than removed.

## Agents & their status

| Agent | Purpose | Tools / APIs | Status |
|-------|---------|--------------|--------|
| `coordinator` | Routes user requests, aggregates answers | sub-agents as tools | Working |
| `iot_operator` | Smart lighting control | Tuya (tinytuya): state, on/off, mode, color, temp | Working (needs real device data in `data/smart_device_data/`) |
| `maps_agent` | Routes & navigation | Google Maps Directions | Working |
| `weather_agent` | Current weather + forecast | OpenWeather (current) + Open-Meteo (forecast) + date/time | Working |
| `finance_agent` | Financial data | Frankfurter (FX rates) + Yahoo Finance (stock/index quotes, incl. GPW via `.WA`) | Working |
| `news_agent` | News & market news search | Tavily search (news + finance topics) | Working (uses `gpt-5-mini` reasoning model) |
| `fpl_agent` | Fantasy Premier League | Unofficial FPL API (keyless): fixtures + FDR, PL teams, owner's squad, mini-league standings | Working — needs `FPL_ENTRY_ID` (and optional `FPL_LEAGUE_ID`) in `.env` for the "my squad"/"my league" tools |
| `memory_operator` | Long-term user memory (preferences/facts) | JSON store (`lib/domain/memory.py`): save/get/update/delete | Working — wired into the coordinator; a memory profile is injected into the coordinator prompt each turn (`memory.summary()`, cached with a short TTL — `MEMORY_SUMMARY_CACHE_TTL_SECONDS`, default 60s — and invalidated on writes, so it isn't a DB read on the hot path every turn). See `docs/MEMORY.md` |
| `scheduler_agent` | Proactive cron jobs / reminders + conditional watches | JSON or Postgres store (`lib/domain/scheduler.py`): create/list/delete/update scheduled jobs; `create_watch` for conditional watches | Working — the coordinator delegates "remind me…/every morning…"; due jobs fire out of band via `lib/runtime/scheduler_runner.py` (started in the webhook `lifespan`) and are delivered on Messenger (tag HUMAN_AGENT) or the log. **Conditional watches** (`kind="watch"`) poll a data source and notify on a false→true edge. The previous poll's reading is injected into the predicate as `data["_previous"]` (None first poll) so it can detect a CHANGE ("new points appeared"), and `repeating=True` keeps the watch armed to notify on every future edge (else one-shot). See `docs/SCHEDULER.md` |
| `math_agent` | Deterministic calculations over numbers | Pure Python (`statistics`/`math`): `calculate_statistics`, `percentage_change`, `linear_trend`, `correlation` | Working — general-purpose calculator (no data source of its own); the coordinator feeds it figures gathered from other agents (e.g. a finance/FPL time-series) so arithmetic/stats are exact, not LLM-guessed |

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

There is also a `Dockerfile` / `docker-compose.yml` (the app container is
scaffolded; note the compose file exposes port 8002 for a future HTTP entry point
that does not exist yet). When running the whole stack in Docker, switch the Redis
URL in `lib/core/cache.py` from `redis://localhost:6379` to `redis://redis:6379`.

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
| `openai-compat` | `OpenAILike(api_base=env)` | local/vLLM/Ollama escape hatch |

Config is per agent, same `AGENT_<NAME>_…` convention as `AGENT_<NAME>_ENABLED`:
`AGENT_<NAME>_MODEL`, `AGENT_<NAME>_REASONING_EFFORT`, and (Anthropic only)
`AGENT_<NAME>_THINKING_BUDGET`. Global fallbacks: `DEFAULT_MODEL` and
`DEFAULT_REASONING_EFFORT`. Resolution per agent: `AGENT_<NAME>_MODEL` → `DEFAULT_MODEL`
→ legacy. **Legacy fallbacks** keep old configs working: `COORDINATOR_MODEL` /
`COORDINATOR_REASONING_EFFORT` back the coordinator's new keys, and `OPENAI_DEFAULT_MODEL`
backs `DEFAULT_MODEL` (a bare value → `openai/…`). The coordinator keeps its historical
`medium` reasoning default when nothing is set.

Reasoning effort is one vocabulary — `off`/`minimal`/`low`/`medium`/`high` (plus the
disable sentinels `none`/`off`/`no`/`false`/`disabled`/empty) — normalized per provider in
`lib/core/reasoning.py`:

| effort | OpenAI (Responses) | Anthropic (native) | OpenAI-compatible |
|--------|--------------------|--------------------|-------------------|
| off | omit `reasoning_options` | thinking off | omit |
| minimal/low/medium/high | `reasoning_options={"effort": …}` | `thinking_dict` budget from `EFFORT_BUDGET` (1k/4k/12k/32k) | `reasoning_effort=…` |

For Anthropic thinking the builder enforces the API's constraints: `temperature=1` and
`max_tokens` > budget; `AGENT_<NAME>_THINKING_BUDGET` overrides the table.

### Environment variables (`.env`)
`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `OPENROUTER_API_KEY` (provider keys — only the
ones you use are required; `openai-compat` also reads `OPENAI_COMPAT_BASE_URL` +
`OPENAI_COMPAT_API_KEY`), `DEFAULT_MODEL` + `DEFAULT_REASONING_EFFORT` and the per-agent
`AGENT_<NAME>_MODEL` / `AGENT_<NAME>_REASONING_EFFORT` / `AGENT_<NAME>_THINKING_BUDGET`
(see "Models / providers" above; legacy `OPENAI_DEFAULT_MODEL` / `COORDINATOR_MODEL` /
`COORDINATOR_REASONING_EFFORT` still honored as fallbacks), `GOOGLE_MAPS_API_KEY`,
`XAI_API_KEY`,
`OPENWEATHER_API_KEY`, `TAVILY_API_KEY`, `DATABASE_URL` (Postgres; when unset the
memory store falls back to the on-disk JSON file), `TRACING_ENABLED` +
`MLFLOW_TRACKING_URI` + `MLFLOW_EXPERIMENT` (agent tracing; when the URI is unset or
`TRACING_ENABLED` is falsy, tracing is a no-op). (Any
Messenger/Telegram integration will add more.) `FPL_ENTRY_ID` + `FPL_LEAGUE_ID` (the
owner's Fantasy Premier League manager id and default mini-league, used by
`fpl_agent`'s "my squad"/"my league" tools; the FPL API itself is keyless). Note: the
Yahoo Finance quote source, the Frankfurter FX source and the FPL API are keyless.
`SCHEDULER_ENABLED` + `SCHEDULER_TICK_SECONDS` + `SCHEDULER_TIMEZONE` (the proactive
scheduler loop: master switch — same falsy-parsing as `agent_enabled()`, default on;
tick interval in seconds, default 30; timezone for cron/`run_at`, default Europe/Warsaw.
Proactive delivery reuses `MESSENGER_PAGE_ACCESS_TOKEN`). See `docs/SCHEDULER.md`.
`PROGRESS_PING_ENABLED` + `PROGRESS_PING_AFTER_SECONDS` (the interim "still working"
ping on slow live turns: master switch — same falsy-parsing as `agent_enabled()`, default
on; delay before the single ping fires in seconds, default 20. See `lib/runtime/progress.py`
and the turn-flow note above).
`OBSERVATIONS_ENABLED` + `OBSERVATIONS_FINANCE_HOUR` + `OBSERVATIONS_FPL_HOUR` +
`OBSERVATIONS_TICK_SECONDS` + `OBSERVATIONS_FX`/`OBSERVATIONS_EQUITIES`/`OBSERVATIONS_METALS`
(the daily time-series collectors: master switch — same falsy-parsing as `agent_enabled()`,
default on; local hours each collector fires, default 23 and 4, in `SCHEDULER_TIMEZONE`; clock
check interval, default 300s; the finance watchlist — FX vs PLN, equities/metals as Yahoo
tickers, defaults `USD,EUR,GBP,CHF` / `^GSPC` / `GC=F,SI=F`, plus `OBSERVATIONS_SP500_ETF`
(default `SP5.PA`) — a EUR cross-listing of the owner's Amundi S&P 500 **distributing** ETF
from which `index:sp500_pln` = ETF(EUR) × EUR/PLN is derived (Yahoo has no GPW PLN listing;
must be the dist class to match the owner's fund). The FPL collector uses `FPL_LEAGUE_ID`.
See `docs/OBSERVATIONS.md`).

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

**Per-subagent toggles:** each subagent is enabled by default and can be hidden from
the coordinator by setting `AGENT_<NAME>_ENABLED` to a falsy value (0/false/no/off) —
e.g. `AGENT_NEWS_AGENT_ENABLED=false`. A disabled agent is simply not registered as a
coordinator tool (`agent_enabled()` in `lib/core/registry.py`, read at coordinator-build
time). Disabling `memory_operator` also drops the memory profile injection.

### Testing
`pytest` (dev dependency) with tests under `tests/`. Run `poetry run pytest`. Tests
cover pure helpers and the decorator registries only — no network or API keys
required. Anything that hits an external API (Yahoo, Tavily, Google Maps, OpenWeather)
is intentionally left out of the suite for now; add mocked/integration tests as those
grow.

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
  each agent's `tools.py` / `lib/agents/iot_operator/device.py` — prefer `logger` for new code and
  migrate prints when you touch them.
- **Sensitive data stays server-side; the model uses handles, not raw values.**
  - *Devices:* the model refers to smart devices only by **name**; ip / local_key /
    dev_id never reach the LLM (`describe_as_json()` returns only name/room/zones; the
    iot tools take device names and resolve them via `_resolve_devices`). Don't
    reintroduce `SmartDevice` objects as tool parameters.
  - *Maps:* the model refers to saved places only by **alias** (Home, work, …);
    `get_maps_memory` returns aliases only, and `get_route_details` resolves an alias
    to its real street address server-side (`_resolve_place`) and relabels the route
    endpoints back to the alias. (Residual: turn-by-turn steps from Google can still
    mention street names along the requested route — inherent to navigation.)
- **Storage** — data lives in **Postgres** (docker-compose service) with the on-disk
  JSON files under `data/` kept as the migration source / easy-to-eyeball copy. The
  DB layer is isolated in `app/db/`; the `memory_operator` writes to Postgres when
  `DATABASE_URL` is set (else the JSON file). The `fpl_agent` also caches its reference
  data (players/teams) in Postgres via `app/db/fpl_repo.py` (table `fpl_reference`, a
  JSONB snapshot of `bootstrap-static` with a `FPL_CACHE_TTL_SECONDS` freshness window),
  falling back to an in-process memo + the live API when no DB is configured. Its
  **mini-league squad scan** — the biggest latency cost (a picks fetch per manager,
  behind the "who else has player X / how many own a player from team Y" questions) — is
  cached the same way: `app/db/fpl_league_repo.py` (table `fpl_league_squads`, one JSONB
  snapshot per `(league_id, gameweek)`, `FPL_LEAGUE_CACHE_TTL_SECONDS` default 3h), read
  through by `load_league_squads()` in `fpl_agent/fpl_api.py` (L1 memo → Postgres → live
  scan, write-through; a query on a stale/missing snapshot rescans on demand). A background
  loop (`lib/runtime/fpl_cache_runner.py`, started in the webhook `lifespan`;
  `FPL_CACHE_REFRESH_ENABLED` / `FPL_CACHE_REFRESH_INTERVAL_SECONDS`) warms the owner's
  configured `FPL_LEAGUE_ID` every few hours; national/global leagues are never warmed in
  the background (only cached lazily if explicitly queried).
  The `scheduler_agent` likewise persists jobs to Postgres (table `scheduled_jobs`, via
  `app/db/scheduler_repo.py`) when `DATABASE_URL` is set, else to
  `data/scheduler_data/jobs.json` — same JSON/Postgres backend-selection pattern as memory.
  **Conversation history** follows the same pattern: `lib/domain/conversation.py` +
  `app/db/conversation_repo.py` persist `conversations` + `messages` (two rows per turn:
  the user message and the assistant reply, the latter stamped with the MLflow trace id)
  to Postgres when `DATABASE_URL` is set, else `data/conversation_data/conversations.json`.
  Each conversation also carries the rolling-window `summary` + `summary_upto` coverage
  pointer (see the context-window note under "How a turn flows"); on Postgres these are
  two `ADD COLUMN IF NOT EXISTS` columns on `conversations`. The per-turn hot-path read
  uses `load_history_and_summary()` — history + summary over a **single** connection —
  rather than two separate `load_history`/`load_summary` round-trips.
  **Time-series ("observations")** follow the same pattern: `lib/domain/observations.py` +
  `app/db/observations_repo.py` persist a generic daily series (table `observations`, PK
  `(series, obs_date)`, idempotent upsert) to Postgres when `DATABASE_URL` is set, else
  `data/observations_data/observations.json`. The **FPL daily league history** (table
  `fpl_league_history`, one append-only JSONB snapshot per `(league_id, snapshot_date)` —
  every manager's rank, points and squad; distinct from the TTL squad *cache* in
  `fpl_league_squads`) is **Postgres-only** via `app/db/fpl_history_repo.py`. Both are
  written daily by the deterministic collectors in `lib/runtime/observations_runner.py`
  (finance @23:00, FPL @04:00; started in the webhook `lifespan`) and read back by the
  history tools inside `finance_agent` / `fpl_agent`. Seed history with
  `python -m scripts.backfill_finance` (a year of daily finance) and
  `python -m scripts.backfill_fpl_history` (per-gameweek FPL league snapshots); both are also
  runnable on demand from the admin `/collect` command. See `docs/OBSERVATIONS.md`.
  Migrate with `python -m app.db.migrate`.
  See `docs/STORAGE.md`. JSON stores under `data/` are gitignored except `.gitkeep`.
  Real device keys / preferences live there and are not committed.
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
