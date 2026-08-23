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

Built on the **OpenAI Agents SDK** (`agents` package), with LiteLLM as a model
adapter layer so non-OpenAI models (xAI/Grok, Groq) can be plugged in per agent.

`lib/` is layered as a strict import DAG — imports go **up only**:
`runtime → agents → {domain, core}`, `domain → core`, `core → (nothing in lib)`.
Each layer's `__init__.py` is its public surface: import across layers/features from
the package `__init__`, never from a submodule directly.

```
app/main.py            Entry point: load env, configure logging, run the chatbot loop

lib/core/              LEAF layer — no imports from elsewhere in lib
  registry.py          The 3 registries (TOOLS_BY_AGENT/AGENTS/LLM_BY_AGENT) + decorators
  cache.py             Redis-backed Cache + Ctx (shared run context object)
  env.py               environment_preamble() — date/time/locale block for every agent
  llm.py               Per-agent model + ModelSettings factories (registered via decorator)
  logger.py            Root logger config + custom CONVERSATION log level

lib/domain/            Shared stateful stores (used by agents AND runtime)
  memory.py            Long-term memory store (JSON or Postgres backend; see app/db)
  scheduler.py         Scheduled-jobs store (JSON or Postgres backend)

lib/agents/            Coordinator + one feature subpackage per agent
  __init__.py          Aggregator: imports every subpackage (registration), public surface
  coordinator/         create_coordinator_agent (wires subagents in as tools)
  composer/            create_composer_agent (handoff target: writes the final reply)
  <agent>/agent.py     the create_*_agent() factory
  <agent>/tools.py     that agent's @tool_ownership function-tools
  <agent>/<helper>.py  that agent's private helpers, e.g.
                         iot_operator/{device,tuya_link}.py (Tuya model + link),
                         maps_agent/directions.py, weather_agent/forecast.py,
                         finance_agent/fx.py, fpl_agent/fpl_api.py

lib/runtime/           TOP layer — wires the network to a transport / runs jobs
  engine.py            transport-agnostic handle_message(conversation_id, text, ctx)
  chatbot.py           REPL loop
  messenger.py         Messenger webhook adapter (signature, send)
  scheduler_runner.py  in-process loop that fires due jobs out of band
  hooks.py run_config.py tracing.py

app/db/                Postgres access layer: connection, schema, repos, migration
data/                  JSON "databases" (devices, preferences, maps memory, memory)
logs/                  Per-run log files
```

### How a turn flows
1. `Chatbot.start_chatbot()` loops on `input()`.
2. Each turn builds a fresh `RunConfig` (`lib/runtime/run_config.py`) and a fresh
   coordinator (`create_coordinator_agent()`).
3. `Runner.run(coordinator, input=text, previous_response_id=..., context=ctx)`
   executes. The **previous response id** is stored in Redis so the OpenAI
   Responses API keeps conversation continuity across turns.
4. The coordinator calls sub-agents **as tools** (`agent.as_tool(...)`), possibly
   in parallel. Sub-agents call their own function-tools.

### Registration patterns (important conventions)
The three decorator-backed registries now all live in `lib/core/registry.py` (imported
as `from lib.core import ...`); registration happens by **import-time side effect**, so a
tool/agent module must be imported for its decorator to fire. `lib/agents/__init__.py`
imports every feature subpackage precisely to trigger this. Follow them when adding things:

- **Agents** — `@agents_decorator(name)` registers a factory into `AGENTS`. Each
  sub-agent lives in `lib/agents/<name>/agent.py` as a `create_*_agent()` factory
  returning an `Agent`; the coordinator wires them in via
  `.as_tool(tool_name=, tool_description=)`. A new agent needs a package with
  `agent.py` + `tools.py` + an `__init__.py` (importing `.tools` for the side effect and
  re-exporting the factory), plus one import line in `lib/agents/__init__.py` and the
  coordinator's `subagents` list.
- **Tools** — `@tool_ownership(agent_name)` **above** `@function_tool` in that agent's
  `lib/agents/<name>/tools.py` appends the tool to `TOOLS_BY_AGENT[agent_name]`. An agent
  picks up its tools with `tools = TOOLS_BY_AGENT[name]`. Decorator order matters:
  `@tool_ownership` outermost, `@function_tool` innermost.
- **Models** — `@llm_usage([agent_names])` in `lib/core/llm.py` maps agents to a model
  factory returning `{"model_name", "settings"}`. Agents read it via `LLM_BY_AGENT[name]()`.

### Shared context
`Ctx` (`lib/core/cache.py`, imported as `from lib.core import Ctx`) is passed as
`context=` into `Runner.run` and reaches every tool as `RunContextWrapper[Ctx]`. Tools
stash intermediate state on it (`ctx.context.devices`, `ctx.context.devices_preferences`,
`ctx.context.known_adresses`). It also holds the Redis `Cache`. `Ctx.devices` is typed
as `dict[str, Any]` (not `SmartDevice`) on purpose, so `core` stays a leaf and never
imports the iot domain.

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
| `memory_operator` | Long-term user memory (preferences/facts) | JSON store (`lib/domain/memory.py`): save/get/update/delete | Working — wired into the coordinator; a memory profile is injected into the coordinator prompt each turn. See `docs/MEMORY.md` |
| `scheduler_agent` | Proactive cron jobs / reminders | JSON or Postgres store (`lib/domain/scheduler.py`): create/list/delete/update scheduled jobs | Working — the coordinator delegates "remind me…/every morning…"; due jobs fire out of band via `lib/runtime/scheduler_runner.py` (started in the webhook `lifespan`) and are delivered on Messenger (tag HUMAN_AGENT) or the log. See `docs/SCHEDULER.md` |

## Running it

Requires **Redis** on `localhost:6379`, a `.env` (copy from `.env_template`), and
Poetry.

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

### Environment variables (`.env`)
`OPENAI_API_KEY`, `OPENAI_DEFAULT_MODEL`, `COORDINATOR_MODEL` +
`COORDINATOR_REASONING_EFFORT` (the coordinator runs on a dedicated "smarter"
reasoning model — `COORDINATOR_MODEL` picks it, falling back to
`OPENAI_DEFAULT_MODEL`; `COORDINATOR_REASONING_EFFORT` is minimal/low/medium/high,
default medium. The env is the source of truth for both. Because the coordinator
uses a *different* model, `RunConfig` no longer pins one model for the whole run —
each agent's own model in `lib/core/llm.py` is now honored per-agent via the provider,
so e.g. `news_agent` really runs its configured model), `GOOGLE_MAPS_API_KEY`, `XAI_API_KEY`,
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

**Agent tracing (dev observability):** setting `MLFLOW_TRACKING_URI` enables MLflow
auto-tracing for the OpenAI Agents SDK (`lib/runtime/tracing.py`, called once from
`app/main.py`), capturing each coordinator→sub-agent→tool run as a hierarchical
trace. The docker-compose `mlflow` service runs the tracking server + UI (open
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
  falling back to an in-process memo + the live API when no DB is configured.
  The `scheduler_agent` likewise persists jobs to Postgres (table `scheduled_jobs`, via
  `app/db/scheduler_repo.py`) when `DATABASE_URL` is set, else to
  `data/scheduler_data/jobs.json` — same JSON/Postgres backend-selection pattern as memory.
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
