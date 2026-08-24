## ADDED Requirements

### Requirement: An agent's provider is selected from its model prefix

Each agent's model SHALL be configured as a `provider/model` string, and the system
SHALL parse the provider from the portion before the **first** `/`. A model string
without a `/` SHALL resolve to the default provider `openai`. The remainder after the
first `/` SHALL be used verbatim as the provider's model id (so OpenRouter model ids
that themselves contain slashes are preserved).

#### Scenario: Prefixed model selects a provider

- **WHEN** an agent's model is `anthropic/claude-sonnet-4-20250514`
- **THEN** the system builds that agent's LLM with the Anthropic provider and model id
  `claude-sonnet-4-20250514`

#### Scenario: Provider id with a slash in the model is preserved

- **WHEN** an agent's model is `openrouter/deepseek/deepseek-r1`
- **THEN** the provider is `openrouter` and the model id passed to it is
  `deepseek/deepseek-r1`

#### Scenario: Bare model defaults to OpenAI

- **WHEN** an agent's model is `gpt-5.6-luna` with no provider prefix
- **THEN** the system builds that agent's LLM with the OpenAI provider

### Requirement: Each supported provider builds its native LLM

The system SHALL construct a provider-appropriate LlamaIndex LLM per provider: OpenAI
via the Responses API (so function tools and reasoning work together), Anthropic via
its native integration (supporting extended thinking), and any OpenAI-compatible
endpoint (e.g. OpenRouter, Groq, xAI, local servers) via an OpenAI-like client pointed
at the provider's base URL. An unknown provider prefix SHALL be rejected with a clear
error rather than silently defaulting.

#### Scenario: OpenAI agent uses the Responses API path

- **WHEN** an agent is configured with an `openai/…` model
- **THEN** its LLM calls the OpenAI Responses endpoint, preserving combined
  tools-and-reasoning support

#### Scenario: OpenAI-compatible provider uses its base URL

- **WHEN** an agent is configured with an `openrouter/…` model
- **THEN** its LLM is an OpenAI-compatible client targeting the OpenRouter base URL and
  authenticated with the OpenRouter key

#### Scenario: Unknown provider is rejected

- **WHEN** an agent's model prefix names a provider the system does not support
- **THEN** the system raises a clear configuration error naming the unknown provider

### Requirement: Per-agent model configuration with defaults

Every agent (coordinator, composer, and each sub-agent) SHALL resolve its model from
`AGENT_<NAME>_MODEL`, falling back to a global `DEFAULT_MODEL` when unset. The
configuration keys SHALL follow the same `AGENT_<NAME>_…` convention as the existing
enable/disable toggles. Resolution SHALL be applied per agent so different agents can
run on different providers within the same turn.

#### Scenario: Agent-specific model overrides the default

- **WHEN** `DEFAULT_MODEL` is set and `AGENT_NEWS_AGENT_MODEL` is also set to a
  different value
- **THEN** the news agent uses its agent-specific model while other agents use the
  default

#### Scenario: Unset agent model falls back to the default

- **WHEN** `AGENT_WEATHER_AGENT_MODEL` is unset and `DEFAULT_MODEL` is set
- **THEN** the weather agent is built from `DEFAULT_MODEL`

#### Scenario: Two agents on different providers in one turn

- **WHEN** the coordinator is configured with an Anthropic model and a sub-agent with an
  OpenAI model
- **THEN** each runs on its own provider within the same turn

### Requirement: Legacy model configuration remains honored

The system SHALL continue to honor the pre-existing configuration variables as
fallbacks so existing deployments keep working: `COORDINATOR_MODEL` and
`COORDINATOR_REASONING_EFFORT` back the coordinator's new keys, and
`OPENAI_DEFAULT_MODEL` backs `DEFAULT_MODEL` (a bare legacy value being treated as an
OpenAI model).

#### Scenario: Old coordinator variable still applies

- **WHEN** only `COORDINATOR_MODEL` is set (the new `AGENT_COORDINATOR_MODEL` is unset)
- **THEN** the coordinator is built from `COORDINATOR_MODEL`

#### Scenario: Old default model still applies as OpenAI

- **WHEN** only `OPENAI_DEFAULT_MODEL=gpt-5.6-luna` is set (no `DEFAULT_MODEL`)
- **THEN** agents without an explicit model use the OpenAI provider with that model

### Requirement: Reasoning effort is provider-normalized

The system SHALL expose one reasoning-effort vocabulary per agent
(`off`/`minimal`/`low`/`medium`/`high`, including the existing disable sentinel) via
`AGENT_<NAME>_REASONING_EFFORT`, and SHALL translate it into each provider's native
form: an effort literal for OpenAI, a token budget for Anthropic extended thinking, and
a reasoning-effort field for OpenAI-compatible providers. A disabled/`off` effort SHALL
omit provider reasoning options so the model runs without configured reasoning.

#### Scenario: Effort maps to an OpenAI effort literal

- **WHEN** an `openai/…` agent has reasoning effort `high`
- **THEN** its LLM is configured with the OpenAI reasoning effort `high`

#### Scenario: Effort maps to an Anthropic thinking budget

- **WHEN** an `anthropic/…` agent has reasoning effort `medium`
- **THEN** its LLM enables extended thinking with the token budget mapped from `medium`

#### Scenario: Disabled effort omits reasoning

- **WHEN** an agent's reasoning effort is set to a disable sentinel (e.g. `off`)
- **THEN** the agent's LLM is built without any reasoning option and runs with the
  model's own default behavior

### Requirement: Anthropic thinking budget is configurable and constraint-safe

For Anthropic agents the effort-to-budget mapping SHALL come from a default table and
SHALL be overridable per agent via `AGENT_<NAME>_THINKING_BUDGET`. When extended
thinking is enabled the system SHALL enforce the provider's constraints, keeping
`max_tokens` greater than the thinking budget and setting `temperature` to the value the
provider requires for thinking.

#### Scenario: Per-agent budget override

- **WHEN** an Anthropic agent sets `AGENT_<NAME>_THINKING_BUDGET` to an explicit value
- **THEN** that value is used as the thinking budget instead of the default-table value
  for its effort

#### Scenario: Budget stays within provider limits

- **WHEN** an Anthropic agent enables thinking with a given budget
- **THEN** the agent's LLM is configured with `max_tokens` greater than the budget and
  the temperature the provider requires for thinking
