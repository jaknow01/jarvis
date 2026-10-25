## Context

Today every tool lives in `lib/agents/<domain>/tools.py`, registered into `TOOLS_BY_AGENT`
by import side effect and run in-process by LlamaIndex `FunctionAgent`s. Besides the agents,
three other parts of Jarvis reach into tool code directly:

- `scheduler_agent/watch_sources.py` polls tool coroutines from `TOOLS_BY_AGENT` (finance,
  weather, fpl, news, maps) as watch data sources;
- `lib/runtime/observations_runner.py` and `fpl_cache_runner.py` import `fpl_api` and
  `finance_agent.history` for daily collection and cache warming, and own the
  `observations` / `fpl_*` tables in Jarvis's Postgres;
- `evals/harness/mocks.py` swaps tool bodies inside `TOOLS_BY_AGENT`.

Tool state is spread across the per-turn `Ctx`: devices and known addresses live in the
group (household) scope, `user_id` in the user scope, `time_date_now` and `scratch` are
per-turn helpers. Secrets for every external API sit in Jarvis's single `.env`.

The `mcp-fpl-pilot` branch already contains a working slice of the target: an API-key
gateway and client registry (`lib/runtime/mcp_server.py`, `mcp_keys.py`), FPL caller
identity (`fpl_agent/identity.py`), a self-service `/mcp/join` page, a Jarvis-side MCP
client (`lib/core/mcp_client.py`) and `FPL_TOOLS_SOURCE=mcp`. It is deployed and works.

Stakeholders: the owner (operator, single developer), household members using Jarvis, and
friends using `mcp-fpl` from their own Claude apps. Target platform: a k3s cluster in a
different physical location than the home; the home has a router running a WireGuard server
reachable from the internet, and a Raspberry Pi on the LAN.

## Goals / Non-Goals

**Goals:**
- Every tool domain is an independent, publicly hosted repo producing an MCP server image
  that any MCP client can use with a key.
- Jarvis keeps only its harness and talks to tools exclusively over MCP.
- Each service owns its secrets and data; no service reads another's database.
- `mcp-home` stays inside the home network, reachable from the cluster with least privilege.
- Evals remain offline and deterministic.
- All nine domains move in one change (big-bang cut-over of Jarvis), with internal ordering
  only for buildability.

**Non-Goals:**
- Exposing `memory_operator` or `scheduler_agent` as MCP servers (they are harness).
- OAuth / a central authorization server — keys remain the auth mechanism.
- Horizontal scaling of servers (rate limits and caches stay per-process; one replica).
- Migrating existing FPL/finance history rows (rebuilt from APIs instead).
- A real browser (Playwright) for `mcp-web`, or new tools in any domain.
- Publishing packages to PyPI.

## Decisions

### D1. Polyrepo + a small shared library installed from git tags

Repos under `github.com/jaknow01/`: `jarvis`, `jarvis-mcp-kit`, `mcp-fpl`, `mcp-finance`,
`mcp-weather`, `mcp-news`, `mcp-maps`, `mcp-calendar`, `mcp-home`, `mcp-web`, `mcp-math`.
Servers depend on the kit by git URL pinned to a tag
(`jarvis-mcp-kit @ git+https://github.com/jaknow01/jarvis-mcp-kit@v0.1.0`). All repos are
public — code holds no secrets — so image builds need no credentials. Renovate (or manual
bumps) moves servers to new kit tags.

*Alternatives:* one `jarvis-tools` monorepo (contradicts "separate projects", couples
releases); copying the common code into each repo (security fixes would drift across nine
copies); PyPI (publishing overhead for a personal project).

### D2. Kit contents and boundaries

`jarvis-mcp-kit` provides: `serve(tools, name, instructions, …)` building a stateless
Streamable-HTTP MCP app from plain documented async functions; the gateway (header / `X-API-Key`
/ key-in-URL auth, 401/403/404, path rewrite); `ClientRegistry` (YAML file with mtime reload
+ optional issued-key store protocol `active() -> rows`); trusted `X-On-Behalf-Of`
resolution exposed to tools via a contextvar (`current_principal()`); `RateLimiter`;
key-redaction logging setup and uvicorn launcher with access log off; `/health`; `whoami`; a
`keys` CLI (`new`, `hash`, `list`). It contains **no** domain logic: the FPL `/join` page and
FPL identity live in `mcp-fpl`.

Servers write tools as plain async functions without LlamaIndex. The pilot's adapter from
`FunctionTool` metadata is replaced by the kit deriving the schema from the function
signature (via the MCP SDK's `MCPServer` function tooling) and the description from the
docstring — the docstrings move unchanged.

*Alternative:* keep LlamaIndex `FunctionTool` in servers — rejected: an unnecessary heavy
dependency for servers, and it ties servers to Jarvis's framework.

### D3. Tool contract stays name- and schema-compatible

Each server keeps the exact tool names and parameters Jarvis has today (minus `ctx`), so
existing watches, eval fixtures, prompts and the users' habits keep working. Tool guides
move from Jarvis sub-agent prompts into the server: general guidance in MCP
`instructions`, everything needed to call a tool correctly in its own description (Claude
apps may not show server instructions to the model).

### D4. Jarvis sub-agents from configuration

A new `config/subagents.yaml` (committed; no secrets) declares each tool sub-agent:

```yaml
fpl_agent:
  description: "Fantasy Premier League: fixtures, squads, live, mini-league…"  # coordinator-facing
  url_env: MCP_FPL_URL          # e.g. http://mcp-fpl.mcp.svc.cluster.local/mcp/fpl
  key_env: MCP_FPL_KEY
  identity: none                # none | user | group
  persona: ""                   # optional extra prompt text
```

A generic factory builds a `FunctionAgent` per entry: tools from the cached MCP listing
(`McpToolset`, already in `lib/core/mcp_client.py`), system prompt = environment preamble +
server `instructions` + persona + `SUBAGENT_TOOL_RULES`, model via `LLM_BY_AGENT[name]`
(profiles and per-principal overrides unchanged). Agent names stay (`fpl_agent`,
`iot_operator`, …) so `principals.yaml`, model profiles and roster logic keep working.
`memory_operator` and `scheduler_agent` keep their in-repo code.

The engine refreshes toolsets before building the coordinator (pilot behaviour); a
sub-agent whose server has never listed successfully is omitted from that turn's roster
(instead of the pilot's local fallback, which no longer exists).

*Alternative:* keep thin per-domain Python packages in Jarvis — rejected: no behaviour left
to hold, only prompts that now belong to servers.

### D5. Identity forwarding: household for places and devices, user for calendar

Multi-user Jarvis already keeps devices and known addresses in the **group** scope (shared
household) and calendar/memory per **user**. Each sub-agent config declares `identity`; the
MCP client adds `X-On-Behalf-Of: <group_id|user_id>` to every call. The kit honours the
header only for clients with `trusted: true` (Jarvis's key); other clients act as
themselves. Servers key their data by that principal id (`mcp-maps`: places per principal;
`mcp-home`: device registry per principal; `mcp-calendar`: CalDAV account per principal).

Because `McpToolset` is shared across turns, the principal travels in a contextvar set by
the engine for the turn and read by the toolset when it opens the HTTP client.

*Alternatives:* a key per user/household (Jarvis would hold N keys per server); signed JWTs
from Jarvis (stronger, but over-engineered for one trusted caller inside the cluster).

### D6. Each service that needs a database runs its own Postgres container

*(Revised with the owner: maximum modularity.)* `jarvis`, `mcp-fpl` and `mcp-finance` each run
their own Postgres StatefulSet (one database, one login, own PVC) next to the service, so any
service can be moved, rebuilt or deleted without touching another's data. `mcp-fpl` tables: `fpl_reference`,
`fpl_league_squads`, `fpl_league_history`, `mcp_api_keys`; `mcp-finance`: `observations`.
Schemas are created by the service on start (`CREATE … IF NOT EXISTS`, as today).
History is rebuilt by each service's `backfill` command (FPL per gameweek; finance a year of
daily rows). Granularity loss accepted: the FPL backfill restores one snapshot per gameweek,
not the previous daily snapshots. `mcp-maps` / `mcp-home` / `mcp-calendar` need no database:
their per-principal registries are small config files/Secrets.

*Alternatives:* one shared instance with a database per service (fewer containers, but couples
operations and failure domains — rejected by the owner); SQLite per service; a shared schema in
Jarvis's DB (breaks "deploy anywhere").

### D7. Collectors as one-shot commands → k8s CronJobs

Each server image gets CLI subcommands besides `serve`: `mcp-fpl collect` (daily league
snapshot, 04:00), `mcp-fpl warm-cache` (league squads, every 3 h), `mcp-fpl backfill`,
`mcp-finance collect` (23:00), `mcp-finance backfill`. In k3s they run as CronJobs with
`concurrencyPolicy: Forbid`; in plain Docker the owner can use cron. The in-process loops
and Jarvis's `/collect` admin command are removed.

### D8. FPL self-service keys live in `mcp-fpl`, owner pinged through a webhook

The pilot's `/mcp/join` moves to `mcp-fpl` (issued keys in its own `mcp_api_keys` table).
Owner notification becomes an HTTP POST to `JOIN_NOTIFY_WEBHOOK_URL` with a bearer token.
Jarvis exposes `POST /internal/notify` (token-protected, cluster-internal) that forwards the
text to the owner on Messenger, so `mcp-fpl` contains no Messenger code. Revocation moves to
the server: an authenticated admin tool or CLI `mcp-fpl keys revoke <entry>`; Jarvis's
`/mcp` admin command calls that over MCP using an admin-flagged key.

*Alternative:* `mcp-fpl` calling the Messenger Send API directly — would spread the page
token to a public-facing service.

### D9. Home access: WireGuard gateway pod, router firewall, NetworkPolicy

```
k3s ── jarvis pod ──(NetworkPolicy: only jarvis)──► home-gateway pod
                                                     │ WireGuard peer "k3s" (userspace,
                                                     │ e.g. wireguard-go, no NET_ADMIN on node)
                                                     │ + TCP forwarder → <pi-lan-ip>:8010
        ═══════════════ internet / UDP ══════════════╪═══════════
home router (WireGuard server): peer "k3s" AllowedIPs = its tunnel IP;
  firewall: from peer "k3s" ACCEPT only → <pi-lan-ip>:8010, DROP everything else
                                                     ▼
Raspberry Pi: docker run mcp-home (key auth, Tuya on LAN)
```

`home-gateway` is exposed as ClusterIP service `mcp-home` in the `jarvis` namespace, so
Jarvis's config URL is an ordinary in-cluster URL. The WireGuard private key is a k8s
Secret. The Pi is not a cluster node and has no cluster credentials.

*Alternatives:* Pi as a k3s node (rejected by the owner: home must stay separate); exposing
`mcp-home` on a public port (light control reachable from the whole internet behind only a
key); node-level WireGuard (every pod on the node could reach home).

### D10. Exposure model: Cloudflare Tunnel, two public hostnames

The cluster is reached from the internet only through the owner's Cloudflare Tunnel
(`cloudflared` running as a pod, public hostnames managed in the Cloudflare dashboard) on
`1nogaulica-2czerwonydywan.win`: `mcp-fpl.<domain>` → `mcp-fpl.mcp.svc` (friends' Claude apps
use `/mcp/<key>`, the key page is `/join`; kit v0.3.0 adds these short paths) and
`jarvis.<domain>` restricted to `^/webhook` (Messenger). TLS terminates at Cloudflare; no
Ingress or cert-manager. Everything else is ClusterIP-only; `/internal/notify` is never public.
Namespaces: `mcp` (servers) and `jarvis` (Jarvis, its Postgres, home-gateway).

*Alternative:* Traefik Ingress + cert-manager — unnecessary with the tunnel already in place.

### D14. GitOps: private manifests repo, CI bumps image tags, Sealed Secrets

All manifests live in a private repo `jaknow01/homelab-gitops` (Argo CD app-of-apps, Kustomize
per component), not in the public code repos: it holds cluster details and sealed secrets, and
deploy commits stay out of code history. Each code repo's CI, on a `v*` tag, builds a multi-arch
image into GHCR (private packages, pulled with a `ghcr-pull` secret per namespace) and commits
the new `newTag` into the component's `kustomization.yaml` (repo secret `GITOPS_TOKEN`; skipped
with a warning when absent). Secrets are SealedSecrets produced with `scripts/seal.sh`; the
Sealed Secrets controller is itself an Argo application (sync wave 0).

*Alternatives:* manifests in each code repo (public exposure of cluster details, CI committing
into the repo it builds from); Argo CD Image Updater (fewer secrets in CI, but another
controller and git write-back credentials in the cluster); SOPS / External Secrets (more moving
parts for a single-owner cluster).

### D11. Watches call tools over MCP

`watch_sources.py` keeps its three-state contract but resolves a source id
`"<agent>:<tool>"` (e.g. `fpl_agent:get_my_fpl_squad`) to a call on that sub-agent's
`McpToolset`, with the watch owner's principal forwarded per D5. The catalogue of watchable
sources is generated from the configured servers' tools whose annotations say read-only.
Existing stored watch source ids are mapped to the new form by a one-time store migration
(they already name the tool; only the prefix format may change).

### D12. Evals against a schema snapshot

`evals/fixtures/mcp_tools.json` holds each server's `instructions` and tool list. A command
(`python -m evals.snapshot_tools`) refreshes it from live servers. In eval runs, the
sub-agent factory builds tools from the snapshot with bodies replaced by the existing
fixtures, preserving routing capture. No server is needed to run evals.

### D13. Per-turn helpers stay on the Jarvis side

`weather_agent`'s "call date tool first" guard and `web_agent`'s per-turn page/search
budgets (`Ctx.scratch`) are assistant policy, not tool logic. The weather guard is dropped
(the environment preamble already carries the date); the web budgets move into a generic
per-turn call-budget option of the sub-agent config (`max_calls_per_turn: {read_page: 6,
web_search: 4}`) enforced by the Jarvis-side toolset wrapper.

## Risks / Trade-offs

- [Big-bang cut-over of nine domains] → servers are built and verified one by one against
  Jarvis on a feature branch (each via its config entry) before the old packages are
  deleted; the old Jarvis image is kept for rollback.
- [Network hop on every tool call; ~3 HTTP requests per call with the current SDK] →
  in-cluster latency is milliseconds, negligible next to LLM calls; keep ClusterIP traffic
  for Jarvis even for public servers.
- [Kit version drift across nine repos] → kit stays small and stable; Renovate PRs; servers
  pin exact tags.
- [Trusted on-behalf-of key leak lets the holder act as any household] → the trusted key
  exists only in Jarvis's Secret, is used only on in-cluster URLs, and every call is logged
  with client and principal; rotate via the clients file.
- [Home link or Pi down] → only the home sub-agent degrades (tool error); covered by spec.
- [Tool contract drift between server and stored watches / eval fixtures] → names and
  schemas are frozen by D3; the schema snapshot diff in CI shows changes.
- [FPL daily history granularity lost on rebuild] → accepted by the owner; daily snapshots
  resume from deployment.
- [`web_agent` is uncommitted on `web-browsing`] → merge it to `main` first; `mcp-web`
  extraction is blocked until then.
- [Public repos reveal architecture, e.g. the watch sandbox] → the sandbox stays in
  Jarvis (already gitignored); servers contain only API clients.

## Migration Plan

1. Merge `web-browsing` and `mcp-fpl-pilot` into `main`; branch the change from there.
2. Create `jarvis-mcp-kit`, tag `v0.1.0`.
3. Create the nine server repos, move code + docstrings + tests, add CI; publish images.
4. In Jarvis: generic config-defined sub-agents, identity forwarding, MCP watch sources,
   `/internal/notify`, eval snapshot; verify each domain against its server.
5. Provision Postgres databases/logins; deploy servers to k3s; run backfills; create
   CronJobs.
6. Set up the home: WireGuard peer + firewall rule on the router, `mcp-home` on the Pi,
   `home-gateway` pod + NetworkPolicy.
7. Deploy the new Jarvis; delete the old domain packages, runners, tables (after a grace
   period) and pilot modules.

Rollback: redeploy the previous Jarvis image (still contains in-process tools) and its
`.env`; servers can keep running unused. Jarvis's old tables are dropped only after the new
setup has run cleanly for a while.

## Open Questions

- Which userspace WireGuard client for `home-gateway` (wireguard-go + socat, `boringtun`, or
  a ready image such as `linuxserver/wireguard` with limited capabilities)? Depends on what
  the k3s nodes permit.
- Router model: the exact firewall syntax for "peer → Pi:8010 only" depends on it (OpenWrt,
  MikroTik, UniFi…).
- Public hostname for `mcp-fpl` after moving to k3s: keep `ogarniacz.duckdns.org` or a new
  domain? Existing friends' connector URLs contain the host — a change means re-adding
  connectors (keys can stay).
- Does `mcp-calendar` stay single-account (owner's iCloud) at first, with per-user accounts
  later?
