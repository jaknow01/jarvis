## 1. Prerequisites

- [ ] 1.1 Commit and merge the `web-browsing` branch (`web_agent`) into `main`
- [ ] 1.2 Merge `mcp-fpl-pilot` into `main`; branch this change from the result
- [ ] 1.3 Record the current tool catalogue (per domain: tool names + input schemas) as the
  parity baseline for D3 (`evals/fixtures/tool_baseline.json`)

## 2. jarvis-mcp-kit (new public repo)

- [ ] 2.1 Create `github.com/jaknow01/jarvis-mcp-kit` (Poetry package, MIT, README), depending
  only on `mcp`, `starlette`, `uvicorn`, `pyyaml`
- [ ] 2.2 Port the client registry from the pilot (`mcp_keys.py`): YAML file with mtime reload,
  last-good on error, `trusted` flag, per-server settings, issued-key store protocol
- [ ] 2.3 Port the gateway (header / `X-API-Key` / key-in-URL, 401/403/404, path rewrite) and
  add trusted `X-On-Behalf-Of` resolution exposed via `current_client()` /
  `current_principal()` contextvars
- [ ] 2.4 Implement `serve(tools, name, instructions, …)`: plain async functions → MCP tools
  (schema from signature, description from docstring, read-only annotations), `whoami`,
  stateless Streamable HTTP + JSON, `/health`
- [ ] 2.5 Port the per-client rate limiter, key-free logging setup and a uvicorn launcher with
  access log off and proxy headers on
- [ ] 2.6 Add the `keys` CLI (`new`, `hash`, `list`) usable from any server image
- [ ] 2.7 Port the pilot's gateway/registry/rate-limit tests; add tests for on-behalf-of
  (trusted honoured, untrusted ignored) and key redaction in logs
- [ ] 2.8 CI (lint + tests) on GitHub Actions; tag `v0.1.0`

## 3. Server repo template

- [ ] 3.1 Define the common layout (`src/<pkg>/server.py`, `cli.py`, `tests/`, `Dockerfile`,
  `docker-compose.yml`, `.env.example`, `clients.example.yaml`, README with "connect from
  Claude app / Claude Code / Jarvis")
- [ ] 3.2 CI workflow: tests + build and push `ghcr.io/jaknow01/<repo>:<tag>` on tags
- [ ] 3.3 k8s manifest skeleton per server (Deployment, Service, Secret refs, probes on
  `/health`, optional CronJobs/Ingress)

## 4. Stateless servers

- [ ] 4.1 `mcp-math`: move the four calculation tools + tests; instructions from the
  math_agent prompt
- [ ] 4.2 `mcp-weather`: move current/forecast tools and `forecast.py`; OpenWeather key in its
  env; drop the "date tool first" guard; instructions from the weather prompt
- [ ] 4.3 `mcp-news`: move Tavily news/finance search tools (async-wrapped, timeouts);
  instructions from the news prompt
- [ ] 4.4 `mcp-web`: move `fetch.py` (SSRF guard), `pages.py` (page store), all six tools and
  the Tavily Extract fallback; untrusted-content notice stays in tool outputs; tests
- [ ] 4.5 Verify tool parity of 4.1–4.4 against the baseline from 1.3

## 5. Stateful servers: FPL and finance

- [ ] 5.1 `mcp-fpl`: move `fpl_api.py`, tools, identity resolution (explicit arg → caller
  defaults, no server-wide fallback) and FPL error hints; storage module for
  `fpl_reference`, `fpl_league_squads`, `fpl_league_history` on its own `DATABASE_URL`
- [ ] 5.2 `mcp-fpl`: CLI `collect`, `warm-cache`, `backfill`; remove in-process loops
- [ ] 5.3 `mcp-fpl`: move the `/join` page and issued-key store (`mcp_api_keys`) from the
  pilot; owner notification via `JOIN_NOTIFY_WEBHOOK_URL` + token; `keys revoke <entry>` CLI
  and an admin-only revoke/list tool
- [ ] 5.4 `mcp-fpl`: move tests (tools, identity, join flow, escaping, rate limit, revoke)
- [ ] 5.5 `mcp-finance`: move FX/quotes/ticker search/history tools, `fx.py`, `history.py`,
  `symbols.py`; `observations` storage on its own database; CLI `collect`, `backfill`;
  watchlist env (`OBSERVATIONS_*`) moves with it; tests
- [ ] 5.6 Verify tool parity of 5.1 and 5.5 against the baseline

## 6. Personal-data servers

- [ ] 6.1 `mcp-maps`: move directions + saved-places tools; places registry per principal
  (file/Secret keyed by principal id), alias-only outputs, Google Maps key in its env; tests
  for per-principal resolution and no address leakage
- [ ] 6.2 `mcp-calendar`: move CalDAV link and four tools (gated delete); CalDAV account per
  principal (start with the owner's account), credentials in its env/Secret; tests
- [ ] 6.3 `mcp-home`: move Tuya device model/link and iot tools; device registry per
  principal from a mounted file (device keys never returned); arm64 image for the Pi; tests
- [ ] 6.4 Verify tool parity of 6.1–6.3 against the baseline

## 7. Jarvis: config-defined sub-agents

- [ ] 7.1 Add `config/subagents.yaml` (+ loader with validation) declaring the nine
  sub-agents: description, `url_env`, `key_env`, `identity`, `persona`, optional
  `max_calls_per_turn`
- [ ] 7.2 Generic sub-agent factory: tools from `McpToolset`, prompt = preamble + server
  instructions + persona + tool rules, model via `LLM_BY_AGENT`; keep agent names for
  roster/profiles
- [ ] 7.3 Coordinator roster: include a config sub-agent only when enabled for the principal
  AND its toolset has a listing; log omissions
- [ ] 7.4 Identity forwarding: engine binds the turn's user/group in a contextvar;
  `McpToolset` adds `X-On-Behalf-Of` per the sub-agent's `identity`
- [ ] 7.5 Per-turn call budgets in the toolset wrapper (replaces `web_agent` scratch budgets)
- [ ] 7.6 Remove the local-tools fallback and `FPL_TOOLS_SOURCE`; refresh all configured
  toolsets per turn (concurrently with history load)
- [ ] 7.7 Tests: factory from config, roster omission on missing listing, header forwarding
  per identity mode, budget exhaustion, error string on call failure

## 8. Jarvis: watches, notifications, admin

- [ ] 8.1 `watch_sources.py`: source id `<agent>:<tool>` resolved to an MCP call with the
  watch owner forwarded; catalogue from read-only tools of configured servers; three-state
  results preserved
- [ ] 8.2 One-time migration of stored watch source ids to the new format (JSON + Postgres)
- [ ] 8.3 `POST /internal/notify` in the webhook app (bearer token, owner Messenger delivery)
  for server notifications
- [ ] 8.4 Admin: `/mcp` lists configured servers + listing status and FPL self-service keys
  (via the admin tool), `/mcp revoke <entry>` calls the FPL admin tool; remove `/collect`
- [ ] 8.5 Update watch, scheduler and admin tests

## 9. Jarvis: evals

- [ ] 9.1 `evals/fixtures/mcp_tools.json` + `python -m evals.snapshot_tools` refreshing it from
  live servers
- [ ] 9.2 Eval mode builds sub-agents from the snapshot with fixture bodies; routing capture
  unchanged
- [ ] 9.3 Run the existing benchmark suite offline and compare routing metrics with the
  pre-change baseline

## 10. Jarvis: removal of in-process tools

- [ ] 10.1 Delete `lib/agents/{fpl,finance,weather,news,maps,calendar,math,web}_agent` and
  `iot_operator`, their exports and imports in `lib/agents/__init__.py`
- [ ] 10.2 Delete `lib/runtime/{mcp_server,mcp_keys,mcp_join,observations_runner,
  fpl_cache_runner}.py`, `lib/domain/{observations,mcp_keys}.py`, `app/mcp_server.py`,
  `scripts/{backfill_finance,backfill_fpl_history,mcp_key}.py`, FPL/observations/key repos
  in `app/db`, their lifespan wiring and tests
- [ ] 10.3 Drop Jarvis's `fpl_*`, `observations`, `mcp_api_keys` table DDL from
  `app/db/schema.py` (tables themselves dropped manually after the grace period)
- [ ] 10.4 Remove now-unused dependencies (tinytuya, googlemaps, pyowm, openmeteo, caldav,
  tavily, trafilatura, …) and per-domain env vars from `.env_template`
- [ ] 10.5 Full test suite green; `grep` confirms no import of removed packages

## 11. Deployment (k3s)

- [ ] 11.1 Postgres: databases `jarvis`, `mcp_fpl`, `mcp_finance` with per-service logins
  granted only on their own database; verify cross-database access is denied
- [ ] 11.2 Namespaces `jarvis`, `mcp`; per-service Secrets (external API keys, clients files,
  database URLs); Jarvis Secret with server keys (trusted) and notify token
- [ ] 11.3 Deploy the eight cluster servers (Deployment + ClusterIP + probes)
- [ ] 11.4 Ingress + cert-manager for `mcp-fpl` (`/mcp/fpl…`, `/join`) on the chosen hostname
- [ ] 11.5 CronJobs: `mcp-fpl collect` (04:00), `mcp-fpl warm-cache` (every 3 h),
  `mcp-finance collect` (23:00), all `concurrencyPolicy: Forbid`, `Europe/Warsaw`
- [ ] 11.6 Run `mcp-fpl backfill` and `mcp-finance backfill`; check history tools answer
- [ ] 11.7 Deploy Jarvis with `config/subagents.yaml` pointing at in-cluster URLs

## 12. Home network

- [ ] 12.1 Router: add WireGuard peer `k3s` (own key, tunnel IP); firewall rule allowing that
  peer only to `<pi-lan-ip>:8010`, dropping everything else; document the rule
- [ ] 12.2 Raspberry Pi: run `mcp-home` in Docker (restart policy, device registry file,
  clients file with Jarvis's trusted key)
- [ ] 12.3 `home-gateway` pod: userspace WireGuard client + TCP forwarder to the Pi, WireGuard
  key from a Secret; ClusterIP service `mcp-home` in namespace `jarvis`
- [ ] 12.4 NetworkPolicy: only Jarvis pods may reach `home-gateway`
- [ ] 12.5 Verify: Jarvis toggles a light; a pod in `mcp` cannot reach the gateway; from the
  tunnel peer, any other home address/port is unreachable; a request without key gets 401;
  with the router offline, other sub-agents still answer

## 13. Cut-over and documentation

- [ ] 13.1 Point friends' FPL connectors at the new public URL (keys preserved by migrating
  the `mcp_api_keys` rows, or re-issued via `/join`) and announce the change
- [ ] 13.2 Update Jarvis `CLAUDE.md`, `docs/MCP.md` (now: client side + topology),
  `docs/STORAGE.md`, `docs/OBSERVATIONS.md`, `docs/SCHEDULER.md` (watch sources), and remove
  per-domain docs that moved to server READMEs
- [ ] 13.3 Keep the previous Jarvis image tagged for rollback; after a stable period drop the
  old tables from Jarvis's database
