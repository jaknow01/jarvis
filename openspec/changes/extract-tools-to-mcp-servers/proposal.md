## Why

Every tool Jarvis can use (FPL, finance, weather, news, maps, calendar, smart lights, web,
math) is compiled into the assistant's own process, so no other client can use it and every
tool has to run wherever the assistant runs. The owner wants tools to be standalone services
that any MCP client can attach to — friends' Claude apps (FPL), the owner's Claude Code, and
Jarvis itself as just one more client — and that can be deployed independently: the agent on
a remote k3s cluster, the smart-lights server inside the home network. The `mcp-fpl-pilot`
branch proved the approach for one domain (an API-key–gated MCP server, Jarvis consuming it,
a self-service key page); this change makes it the architecture for all tools.

## What Changes

- **New shared library `jarvis-mcp-kit`** (own public repo, versioned by git tags): the
  common plumbing every tool server needs — API-key authentication (header or key-in-URL),
  client registry (config file + issued keys), per-client rate limiting, on-behalf-of user
  identity for trusted callers, key-free logging, `/health`, and the adapter that serves
  documented tool functions over Streamable HTTP. Extracted from the pilot's
  `lib/runtime/mcp_server.py` + `mcp_keys.py`.
- **Nine new tool-server repos**, one per domain, each a public repo with its own image,
  config, secrets and (where needed) its own database: `mcp-fpl`, `mcp-finance`,
  `mcp-weather`, `mcp-news`, `mcp-maps`, `mcp-calendar`, `mcp-home`, `mcp-web`, `mcp-math`.
  Each server owns its tool guide (MCP `instructions` + tool descriptions), its external-API
  secrets, and its data.
- **Data moves to its owner.** FPL caches/league history and finance observations move out of
  Jarvis's Postgres into per-service databases (separate database + login on a shared Postgres
  instance). Daily collectors and cache warmers move into the services and run as k8s
  CronJobs. History is rebuilt from the upstream APIs by each service's backfill command — no
  table migration. **BREAKING**: Jarvis's `fpl_*` and `observations` tables, its
  `observations_runner` / `fpl_cache_runner` loops and the `/collect` admin command are removed.
- **FPL self-service keys move to `mcp-fpl`.** The `/join` page (league-membership gate, one
  key per entry, owner notification) becomes part of the FPL server; the owner notification is
  sent through a configurable webhook rather than Jarvis's Messenger code.
- **Jarvis becomes an MCP client harness.** Sub-agents are defined by configuration (server
  URL, key, model, persona) instead of code; at build time each sub-agent receives the
  server's tools and its `instructions`. The coordinator, composer, engine, conversation
  history, `memory_operator` and `scheduler_agent` (including job firing and conditional
  watches) stay in Jarvis. Watches poll data sources by calling tools over MCP.
  **BREAKING**: `lib/agents/<domain>/` packages for the nine domains are deleted; there is no
  local-tools fallback — an unreachable server makes that sub-agent's data unavailable.
- **Per-principal identity for private servers.** Servers holding personal data
  (`mcp-maps`, `mcp-calendar`, `mcp-home`) accept an on-behalf-of header — the household
  group for shared places and devices, the user for the calendar — but only from keys marked
  as trusted (Jarvis's), and scope their data to that principal.
- **Home access over WireGuard.** `mcp-home` runs on the owner's Raspberry Pi (plain Docker,
  not a cluster node). The cluster reaches it through a dedicated `home-gateway` pod holding a
  WireGuard peer of the home router; the router firewall limits that peer to the Pi's MCP
  port, and a NetworkPolicy lets only Jarvis use the gateway.
- **Evals mock at the MCP boundary.** The eval harness loads tool schemas from a committed
  snapshot of the servers' listings and replaces calls with fixtures, so benchmarks need no
  running servers.
- **Deployment target k3s.** Public servers (today `mcp-fpl`) behind Ingress with TLS;
  everything else ClusterIP-only.

## Capabilities

### New Capabilities
- `mcp-server-kit`: the shared library contract — key authentication, client registry,
  trusted on-behalf-of identity, rate limiting, key-free logging, health, tool serving.
- `mcp-tool-servers`: what every domain server must provide — tool catalogue and guide,
  per-server secrets, data ownership, collectors/backfill, per-domain specifics.
- `mcp-fpl-self-service-keys`: the FPL `/join` page — league-membership gate, one active key
  per entry, replacement confirmation, owner notification, revocation.
- `mcp-subagents`: Jarvis's side — config-defined sub-agents bound to MCP servers, tool and
  instruction loading, failure behaviour, MCP-based watch sources, per-user forwarding.
- `home-network-access`: reaching `mcp-home` inside the home network from the cluster via a
  WireGuard gateway with least-privilege on both ends.
- `mcp-eval-mocks`: evaluating Jarvis against MCP tool schemas without live servers.

### Modified Capabilities
<!-- none: model-provider resolution (`model-providers`) is unchanged; config-defined
sub-agents keep resolving their models through the existing profile mechanism. -->

## Impact

- **Jarvis repo**: removes `lib/agents/{fpl,finance,weather,news,maps,calendar,iot_operator,
  web,math}_agent`, `lib/runtime/{mcp_server,mcp_keys,mcp_join,observations_runner,
  fpl_cache_runner}.py`, `lib/domain/{observations,mcp_keys}.py`, related `app/db` repos and
  tables, `scripts/backfill_*`, `scripts/mcp_key.py`, `app/mcp_server.py`; reworks the
  coordinator roster, `watch_sources.py`, eval mocks, admin commands, docker-compose and
  `.env_template`; keeps `lib/core/mcp_client.py` as the client.
- **New repos** under `github.com/jaknow01/`: `jarvis-mcp-kit` + nine `mcp-*` servers, each
  with CI building an image to GHCR.
- **Dependencies**: `mcp` SDK (already added on the pilot branch) in Jarvis and every server;
  servers no longer depend on LlamaIndex.
- **Operations**: per-service databases and logins, k8s Secrets per service, CronJobs for
  collectors, Ingress + cert-manager for public servers, a WireGuard peer + firewall rule on
  the home router, Docker on the Raspberry Pi.
- **Prerequisite**: `web_agent` currently exists only as uncommitted work on the
  `web-browsing` branch; it must be merged before `mcp-web` can be extracted.
