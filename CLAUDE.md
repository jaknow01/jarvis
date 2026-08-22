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

```
app/main.py            Entry point: load env, configure logging, run the chatbot loop
lib/chatbot.py         REPL loop: reads input, runs coordinator, persists conversation id
lib/agents.py          Coordinator + all sub-agent factories (registered via decorator)
lib/tools.py           All function-tools, grouped per agent via @tool_ownership
lib/tools_utils.py     Pure helpers for tools (directions parsing, forecast fetch, currency)
lib/llm.py             Per-agent model + ModelSettings selection (registered via decorator)
lib/smart_device.py    Tuya smart-bulb model & control (pydantic + tinytuya)
lib/cache.py           Redis-backed cache + Ctx (shared run context object)
lib/run_config.py      Builds the Agents SDK RunConfig (OpenAI Responses model)
lib/logger.py          Root logger config + custom CONVERSATION log level
lib/memory.py          Long-term memory store (JSON or Postgres backend; see app/db)
lib/tracing.py         MLflow agent tracing setup (opt-in via MLFLOW_TRACKING_URI)
app/db/                Postgres access layer: connection, schema, repos, migration
data/                  JSON "databases" (devices, preferences, maps memory, memory)
logs/                  Per-run log files
```

### How a turn flows
1. `Chatbot.start_chatbot()` loops on `input()`.
2. Each turn builds a fresh `RunConfig` (`lib/run_config.py`) and a fresh
   coordinator (`create_coordinator_agent()`).
3. `Runner.run(coordinator, input=text, previous_response_id=..., context=ctx)`
   executes. The **previous response id** is stored in Redis so the OpenAI
   Responses API keeps conversation continuity across turns.
4. The coordinator calls sub-agents **as tools** (`agent.as_tool(...)`), possibly
   in parallel. Sub-agents call their own function-tools.

### Registration patterns (important conventions)
The codebase uses three decorator-backed registries. Follow them when adding things:

- **Agents** — `@agents_decorator(name)` in `lib/agents.py` registers a factory
  into `AGENTS`. Each sub-agent is a `create_*_agent()` factory returning an
  `Agent`; the coordinator wires them in via `.as_tool(tool_name=, tool_description=)`.
- **Tools** — `@tool_ownership(agent_name)` **above** `@function_tool` in
  `lib/tools.py` appends the tool to `TOOLS_BY_AGENT[agent_name]`. An agent picks
  up its tools with `tools = TOOLS_BY_AGENT[name]`. Decorator order matters:
  `@tool_ownership` outermost, `@function_tool` innermost.
- **Models** — `@llm_usage([agent_names])` in `lib/llm.py` maps agents to a model
  factory returning `{"model_name", "settings"}`. Agents read it via
  `LLM_BY_AGENT[name]()`.

### Shared context
`Ctx` (`lib/cache.py`) is passed as `context=` into `Runner.run` and reaches every
tool as `RunContextWrapper[Ctx]`. Tools stash intermediate state on it
(`ctx.context.devices`, `ctx.context.devices_preferences`, `ctx.context.known_adresses`).
It also holds the Redis `Cache`.

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
| `memory_operator` | Long-term user memory (preferences/facts) | JSON store (`lib/memory.py`): save/get/update/delete | Working — wired into the coordinator; a memory profile is injected into the coordinator prompt each turn. Reminders/scheduler deferred. See `docs/MEMORY.md` |

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
URL in `lib/cache.py` from `redis://localhost:6379` to `redis://redis:6379`.

### Environment variables (`.env`)
`OPENAI_API_KEY`, `OPENAI_DEFAULT_MODEL`, `COORDINATOR_MODEL` +
`COORDINATOR_REASONING_EFFORT` (the coordinator runs on a dedicated "smarter"
reasoning model — `COORDINATOR_MODEL` picks it, falling back to
`OPENAI_DEFAULT_MODEL`; `COORDINATOR_REASONING_EFFORT` is minimal/low/medium/high,
default medium. The env is the source of truth for both. Because the coordinator
uses a *different* model, `RunConfig` no longer pins one model for the whole run —
each agent's own model in `lib/llm.py` is now honored per-agent via the provider,
so e.g. `news_agent` really runs its configured model), `GOOGLE_MAPS_API_KEY`, `XAI_API_KEY`,
`OPENWEATHER_API_KEY`, `TAVILY_API_KEY`, `DATABASE_URL` (Postgres; when unset the
memory store falls back to the on-disk JSON file), `TRACING_ENABLED` +
`MLFLOW_TRACKING_URI` + `MLFLOW_EXPERIMENT` (agent tracing; when the URI is unset or
`TRACING_ENABLED` is falsy, tracing is a no-op). (Any
Messenger/Telegram integration will add more.) `FPL_ENTRY_ID` + `FPL_LEAGUE_ID` (the
owner's Fantasy Premier League manager id and default mini-league, used by
`fpl_agent`'s "my squad"/"my league" tools; the FPL API itself is keyless). Note: the
Yahoo Finance quote source, the Frankfurter FX source and the FPL API are keyless.

**Agent tracing (dev observability):** setting `MLFLOW_TRACKING_URI` enables MLflow
auto-tracing for the OpenAI Agents SDK (`lib/tracing.py`, called once from
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
coordinator tool (`agent_enabled()` in `lib/agents.py`, read at coordinator-build
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
- **Logging, not prints.** `lib/logger.py` adds a custom `CONVERSATION` level
  (25) via `logger.conversation(...)`. Legacy `print()` calls still linger in
  `lib/tools.py` / `lib/smart_device.py` — prefer `logger` for new code and
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
2. ~~**Memory agent**~~ — **done (v1):** structured JSON store (`lib/memory.py`),
   save/get/update/delete tools, wired into the coordinator with a profile injected
   into its prompt each turn. See `docs/MEMORY.md`. Next: implicit habit learning,
   and migrating scattered assumptions (e.g. the maps "fast walker") into it.
3. **Scheduler** — a mechanism to deliver messages to the agent "out of band"
   (cron-like jobs). Enables proactive briefs and reminders; the `memory_operator`
   is the intended owner of creating/editing scheduled entries.
4. **Deepen agents** — e.g. finance analysis beyond raw quotes, richer memory.

Done in this pass (polish): implemented `get_current_date_and_time`, added Yahoo
Finance stock/index quotes (incl. GPW) to `finance_agent`, migrated the remaining
`print()` calls to `logger`, and added a `pytest` suite.

**Chosen direction for the phone front-end:** *architecture first* — before
committing to a channel (Messenger / iMessage / Telegram), decouple the core into a
service with a clean input/output interface so any channel can plug in later. The
current `Chatbot` REPL (`lib/chatbot.py`) is the seam to generalize: extract a
transport-agnostic "handle one message → return one reply" entry point, then add
channel adapters on top.

## Git / branch state
All feature branches (`logger`, `smarts`, `financial-agent`, `weather-agent`,
`news-agent`, `openai-agents-sdk-swith`) have been **merged into `main`**. `main`
is the single source of truth going forward; create new feature branches off it.

### Commit message convention
**Do NOT append a `Co-Authored-By: Claude ...` trailer** (or any AI co-author
trailer) to commit messages in this repo. Keep commit messages clean without it.
