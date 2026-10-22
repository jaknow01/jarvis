## ADDED Requirements

### Requirement: One independent server per tool domain

Each tool domain SHALL be served by its own MCP server in its own public git repository —
`mcp-fpl`, `mcp-finance`, `mcp-weather`, `mcp-news`, `mcp-maps`, `mcp-calendar`, `mcp-home`,
`mcp-web`, `mcp-math` — built on `jarvis-mcp-kit`, with its own container image, its own
configuration and no import of Jarvis code. Each server SHALL expose the same tools (names,
parameters, behaviour) the corresponding Jarvis sub-agent had before extraction.

#### Scenario: A server runs without Jarvis

- **WHEN** a server is started from its own repository with only its own configuration
- **THEN** it serves its tools to any MCP client with a valid key, and Jarvis need not exist

#### Scenario: Tool parity

- **WHEN** a server's tool list is compared with the pre-extraction tools of its domain
- **THEN** every tool is present under the same name with an equivalent input schema

### Requirement: The server carries its own tool guide

Each server SHALL provide MCP `instructions` describing when and how to use its tools, and
each tool's description SHALL be sufficient on its own to call that tool correctly (clients
are not guaranteed to show server instructions to the model). The tool guide previously held
in the Jarvis sub-agent prompt SHALL move into the server.

#### Scenario: A fresh client understands the tools

- **WHEN** a client with no Jarvis-specific prompt (e.g. a Claude app) connects
- **THEN** the tool descriptions alone tell it what each tool returns and which parameters
  default to the caller's own data

### Requirement: Each server holds only its own external secrets

A server SHALL read the credentials for the external services it calls (e.g.
`OPENWEATHER_API_KEY`, `TAVILY_API_KEY`, `GOOGLE_MAPS_API_KEY`, iCloud app password, Tuya
local keys) from its own environment or mounted files, and SHALL NOT require any other
server's secrets. Secrets SHALL NOT be returned in tool results.

#### Scenario: Secrets are isolated

- **WHEN** the weather server's environment is inspected
- **THEN** it contains the OpenWeather key and its own MCP settings, and no Tavily, Maps,
  iCloud or Tuya credentials

### Requirement: Stateful servers own their data in a dedicated database

`mcp-fpl` and `mcp-finance` SHALL persist their caches and history in their own database
with their own login, on a shared Postgres instance, and SHALL NOT read or write Jarvis's
database or each other's. Data SHALL be (re)built from the upstream APIs by a backfill
command of the service; no data migration from Jarvis is required.

#### Scenario: Fresh deployment rebuilds history

- **WHEN** `mcp-fpl` starts on an empty database and its backfill command is run for the
  owner's league
- **THEN** per-gameweek league snapshots are rebuilt from the FPL API and the history tool
  answers from them

#### Scenario: Database isolation

- **WHEN** the `mcp_fpl` login tries to read the `mcp_finance` database
- **THEN** access is denied

### Requirement: Collectors run as one-shot commands

A server's periodic jobs SHALL be runnable as one-shot commands of that server's image — the
FPL daily league snapshot, FPL league-cache warming and the finance daily observations — so
they can be scheduled as k8s CronJobs and never run twice when the server has several
replicas.

#### Scenario: Daily FPL snapshot

- **WHEN** the CronJob runs the `collect` command of `mcp-fpl`
- **THEN** exactly one snapshot for the configured league and day is upserted

### Requirement: Personal-data servers scope data to the acting principal

`mcp-maps`, `mcp-calendar` and `mcp-home` SHALL resolve saved places, calendar accounts and
devices for the acting principal (the key's client, or the on-behalf-of id for trusted
keys — a household group for maps and home, a user for calendar) and SHALL NOT return another
principal's data. Saved addresses and device credentials SHALL stay server-side; tools SHALL
refer to them by alias or name only.

#### Scenario: Alias resolved per household

- **WHEN** Jarvis asks `mcp-maps` for a route from "Home" on behalf of group `dom`
- **THEN** the `dom` household's "Home" address is used, and the result labels the endpoint
  "Home" without the street address

#### Scenario: Unknown principal has no devices

- **WHEN** a trusted call on behalf of a principal with no configured devices lists devices
- **THEN** an empty list is returned, not the owner's devices

### Requirement: FPL identity defaults come from the caller

`mcp-fpl` SHALL resolve "my team" and "my league" from an explicit tool argument first, then
from the defaults linked to the acting client, and SHALL NOT fall back to any server-wide
owner id.

#### Scenario: Client without linked team

- **WHEN** a client with no linked entry id asks for "my squad" without an `entry_id`
- **THEN** the tool returns an error telling the caller to pass `entry_id`, not another
  manager's squad
