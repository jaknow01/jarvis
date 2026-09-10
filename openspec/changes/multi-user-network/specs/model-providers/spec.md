## MODIFIED Requirements

### Requirement: Per-agent model configuration with defaults

Every agent (coordinator, composer, and each sub-agent) SHALL resolve its model and
reasoning from the acting principal's resolved profile (see the
`agent-roster-resolution` capability). The profile's `default` layer SHALL be backed by
the existing environment configuration — `AGENT_<NAME>_MODEL` falling back to a global
`DEFAULT_MODEL` — so a deployment with no per-principal configuration resolves exactly
the same model per agent as before. Group and user layers, when present, override the
`default` layer per the layered last-non-null-wins rule. Resolution SHALL be applied
per agent so different agents can run on different providers within the same turn, and
so different principals can run the same agent on different models.

#### Scenario: Agent-specific model overrides the default

- **WHEN** `DEFAULT_MODEL` is set and `AGENT_NEWS_AGENT_MODEL` is also set to a
  different value, with no group/user override
- **THEN** the news agent uses its agent-specific model while other agents use the
  default

#### Scenario: Unset agent model falls back to the default

- **WHEN** `AGENT_WEATHER_AGENT_MODEL` is unset and `DEFAULT_MODEL` is set, with no
  group/user override
- **THEN** the weather agent is built from `DEFAULT_MODEL`

#### Scenario: Two agents on different providers in one turn

- **WHEN** the coordinator is configured with an Anthropic model and a sub-agent with an
  OpenAI model
- **THEN** each runs on its own provider within the same turn

#### Scenario: Two principals run the same agent on different models

- **WHEN** user X's profile resolves `news_agent` to an OpenAI model and user Y's
  profile (via a group or user layer) resolves it to an Anthropic model
- **THEN** the same `news_agent` runs on X's model during X's turn and on Y's model
  during Y's turn

#### Scenario: No per-principal config preserves env-driven behavior

- **WHEN** a deployment defines no group or user `agent_config` rows
- **THEN** every agent's model resolves solely from `AGENT_<NAME>_MODEL` / `DEFAULT_MODEL`
  exactly as before this change
