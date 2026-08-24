## ADDED Requirements

### Requirement: Coordinator delegates to sub-agents exposed as tools

The coordinator SHALL be a LlamaIndex `FunctionAgent` whose tools are the enabled
sub-agents, each wrapped as a `FunctionTool` by a shared `agent_as_tool()` helper. The
coordinator SHALL route each request by selecting and calling the appropriate
sub-agent tool(s); it MUST NOT answer from its own built-in knowledge when a
sub-agent covers the domain.

#### Scenario: Single-domain request routed to one sub-agent

- **WHEN** the user asks a question covered by exactly one sub-agent (e.g. weather)
- **THEN** the coordinator calls that sub-agent's tool and uses its result, without
  invoking unrelated sub-agents

#### Scenario: Disabled sub-agent is not exposed

- **WHEN** `AGENT_<NAME>_ENABLED` is set to a falsy value for a sub-agent
- **THEN** that sub-agent is not registered as a coordinator tool and the coordinator
  cannot call it

### Requirement: Sub-agents run concurrently when the request spans domains

The runtime SHALL execute multiple tool calls emitted by the coordinator in a single
step concurrently rather than one after another. Sub-agent tools SHALL be
asynchronous so that concurrent execution yields wall-clock latency savings.

#### Scenario: Multi-domain request fans out

- **WHEN** the user asks a question that needs two or more domains (e.g. weather and
  news) and the coordinator issues the tool calls in one step
- **THEN** the sub-agent tools execute concurrently and the coordinator proceeds once
  all results are collected

#### Scenario: Same sub-agent invoked multiple times in parallel

- **WHEN** the coordinator issues several calls to the same sub-agent in one step
  (e.g. multiple distinct news queries)
- **THEN** those calls execute concurrently (subject to the runtime's concurrency cap)

### Requirement: Composer writes the final reply as a deterministic step

The final user-facing reply SHALL be produced by the composer agent, invoked
explicitly as the last step of a turn by the runtime — not left to the coordinator's
discretion and not via a framework handoff mechanism. The coordinator SHALL only
gather data and MUST NOT write the user-facing reply itself. The composer SHALL write
the reply in Polish, using only data gathered during the turn.

#### Scenario: Successful turn is composed

- **WHEN** the coordinator finishes gathering results for a turn
- **THEN** the runtime calls the composer with the gathered results and returns the
  composer's Polish reply as the turn's output

#### Scenario: A sub-agent failed during the turn

- **WHEN** a sub-agent tool reports an error or cannot complete its task
- **THEN** the runtime still invokes the composer, and the composer plainly informs
  the user in Polish what went wrong without technical jargon

### Requirement: Each agent runs on its own configured model

Each agent SHALL resolve its model and model settings (including reasoning effort
where applicable) per-agent from the `LLM_BY_AGENT` registry, rather than a single
model pinned for the whole run. The coordinator SHALL run on its dedicated reasoning
model as configured.

#### Scenario: Coordinator and a sub-agent use different models

- **WHEN** the coordinator and a sub-agent are configured with different models in
  `LLM_BY_AGENT`
- **THEN** each runs on its own configured model within the same turn

### Requirement: Shared context is reachable from every tool

Function-tools SHALL receive the shared run context via the LlamaIndex workflow
`Context`, exposing the same per-run scratch state the tools rely on today
(`devices`, `devices_preferences`, `time_date_now`, `known_adresses`,
`conversation_id`) and the Redis cache handle.

#### Scenario: A tool reads and writes shared state

- **WHEN** a tool stores intermediate state on the context (e.g. `time_date_now`) and
  a later tool in the same turn reads it
- **THEN** the later tool observes the value written earlier in that turn

### Requirement: Environment preamble is injected into every agent

Every agent's string instructions SHALL be prefixed with the shared environment
preamble (current date/time, timezone, base currency, locale) at build time, so
temporal and locale awareness reaches every agent.

#### Scenario: An agent is built

- **WHEN** an agent factory produces an agent with string instructions
- **THEN** the resulting instructions begin with the shared environment preamble
