## ADDED Requirements

### Requirement: Sub-agents are defined by configuration

Jarvis SHALL build each tool sub-agent from a configuration entry — name, coordinator-facing
description, MCP server URL, the env var holding its API key, optional persona text — instead
of a per-domain Python package. The sub-agent's model and reasoning SHALL keep resolving
through the model profiles and per-principal overrides. Adding a sub-agent for a new server
SHALL require no Python code.

#### Scenario: New server, no code

- **WHEN** the owner adds an entry for a new MCP server to the sub-agent configuration and
  enables it in a principal's roster
- **THEN** the coordinator gains that sub-agent with the server's tools on the next turn

### Requirement: The server's tools and instructions shape the sub-agent

At build time a sub-agent SHALL receive the server's current tool list (cached; refreshed in
the background at most every configured TTL) and its system prompt SHALL include the
server's `instructions`, followed by the persona text and the common sub-agent tool rules.

#### Scenario: Guide changes on the server

- **WHEN** the FPL server's instructions are updated and its listing cache expires
- **THEN** the next FPL sub-agent built by Jarvis carries the updated instructions without a
  Jarvis release

### Requirement: Unreachable server degrades only its sub-agent

If a server has never been listed successfully, its sub-agent SHALL be left out of the
coordinator's roster for that turn; if a call fails at run time, the tool SHALL return an
`ERROR:` string telling the agent the data is unavailable. Jarvis SHALL NOT fall back to
in-process tools. A failed listing SHALL keep the last good listing and retry after a delay.

#### Scenario: FPL server down at startup

- **WHEN** `mcp-fpl` is unreachable and has never been listed
- **THEN** the coordinator runs without the FPL sub-agent and other domains work normally

#### Scenario: Server goes down mid-day

- **WHEN** `mcp-weather` stops responding after a successful listing
- **THEN** the weather sub-agent keeps its tools, its calls return an unavailable error, and
  the reply tells the user weather data is unavailable

### Requirement: The acting principal is forwarded to servers

Each sub-agent configuration SHALL declare an identity scope — `none`, `user` or `group`.
For `user` Jarvis SHALL send the turn's `user_id`, and for `group` the turn's resource
group id (the household whose devices and places are shared), as `X-On-Behalf-Of` with its
trusted key on every call to that server. For `none` no header SHALL be sent.

#### Scenario: Household places

- **WHEN** Ala, a member of the `dom` group, asks the maps sub-agent for a route home
- **THEN** the request to `mcp-maps` carries `X-On-Behalf-Of: dom`, so she and the owner
  share the same "Home"

#### Scenario: Personal calendar

- **WHEN** Ala's turn calls the calendar sub-agent
- **THEN** the request to `mcp-calendar` carries `X-On-Behalf-Of: ala`

### Requirement: Watches poll tools over MCP

A conditional watch's data source SHALL be identified by a server and tool name plus
arguments, fetched by calling that tool over MCP with the watch owner forwarded as the user.
The catalogue of watchable sources SHALL be derived from the configured servers' read-only
tools. A failed fetch SHALL count as an unknown reading, never as a false predicate.

#### Scenario: FPL points watch

- **WHEN** a watch on `fpl:get_my_fpl_squad` polls
- **THEN** Jarvis calls that tool on `mcp-fpl` and evaluates the predicate on its result

#### Scenario: Source server unreachable

- **WHEN** the watch's server does not respond
- **THEN** the poll is recorded as unknown and the watch does not fire

### Requirement: Per-turn call budgets are enforced by Jarvis

Jarvis SHALL enforce per-turn call budgets set in a sub-agent configuration (a maximum
number of calls per tool, e.g. `read_page: 6`, `web_search: 4`): it SHALL count calls per
turn and, once a budget is spent, return an error telling the agent to answer from what it
already has, without calling the server.

#### Scenario: Web browsing budget

- **WHEN** the web sub-agent calls `read_page` a seventh time in one turn with a budget of 6
- **THEN** the call is not sent to `mcp-web` and returns a budget-exhausted error

### Requirement: Harness components stay in Jarvis

Jarvis SHALL keep its harness in its own repository, independent of any tool server's code:
the coordinator, composer, turn engine, conversation history and summarisation,
`memory_operator`, `scheduler_agent` with job firing and watch evaluation, admin commands
and channel adapters.

#### Scenario: Scheduler fires a brief

- **WHEN** a scheduled morning brief is due
- **THEN** Jarvis's scheduler runs a turn that calls the news and weather servers over MCP
  and delivers the reply as before
