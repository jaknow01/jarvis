## ADDED Requirements

### Requirement: Per-principal profile resolved by layered last-non-null-wins

The system SHALL resolve a principal's profile — the set of enabled subagents and each
agent's `model` and `reasoning` — by merging configuration layers in the fixed order
`[default, groups sorted by ascending priority, user]` and taking, per field, the last
non-null value across those layers. A field left unset (`null`) in a layer SHALL NOT
affect the merged value for that field.

#### Scenario: User layer overrides everything

- **WHEN** the `default` layer, a group, and the user layer all set a value for the
  same agent field
- **THEN** the user layer's value wins

#### Scenario: Higher-priority group breaks a group conflict

- **WHEN** two of a user's groups set different `model` values for the same agent and
  the user layer leaves it unset
- **THEN** the value from the group with the higher `priority` wins

#### Scenario: Unset does not override

- **WHEN** a higher layer leaves an agent's `model` unset while a lower layer sets it
- **THEN** the merged profile keeps the lower layer's value

### Requirement: The roster is opt-in — agents are off until explicitly enabled

The system SHALL treat every agent as OFF by default and part of a principal's roster
only when some applicable layer explicitly sets `enabled = true` and no higher-precedence
layer disables it. `unset` neither activates nor disables. The roster is therefore the
**union of agents explicitly enabled** across the user's layers, minus explicit
`enabled = false` in a higher-precedence layer (a higher-priority group, or the user).
To preserve the pre-multi-user single-user behavior, the migrated owner's `default` group
SHALL be seeded at startup with every subagent enabled, so an un-configured owner keeps a
full roster; a newly-added user with no groups SHALL have an empty roster.

#### Scenario: Nothing is enabled without a config entry

- **WHEN** a user is in a group that has no `agent_config` rows and no other layer enables
  any agent
- **THEN** the user's roster is empty

#### Scenario: An agent is included only when a layer enables it

- **WHEN** a user's group A sets `iot_operator.enabled = true` and group B does not mention
  it
- **THEN** the user's roster includes `iot_operator` and excludes agents no layer enabled

#### Scenario: The owner keeps a full roster with no explicit config

- **WHEN** the migrated owner (member of the seeded `default` group) resolves a profile
  and has written no per-agent config
- **THEN** every subagent is in the owner's roster (from the seeded `default` group)

#### Scenario: User explicitly removes an agent

- **WHEN** a group enables `finance_agent` and the user layer sets
  `finance_agent.enabled = false`
- **THEN** `finance_agent` is not in that user's roster

#### Scenario: Higher-priority group disables a lower group's agent

- **WHEN** a low-priority group enables `news_agent` and a higher-priority group sets
  `news_agent.enabled = false`
- **THEN** `news_agent` is not in the roster (absent a user-layer override)

### Requirement: The coordinator is built from the resolved profile

The system SHALL build the coordinator for a turn from the acting principal's resolved
profile: it SHALL expose only the rostered subagents as tools and SHALL build each
agent's LLM from the profile's per-agent `model`/`reasoning`. The deterministic
composer SHALL run for every principal regardless of the profile; only the
coordinator's roster and models vary per principal.

#### Scenario: Two users get different rosters in the same deployment

- **WHEN** user X's profile rosters `weather_agent` and `fpl_agent` while user Y's
  profile also rosters `finance_agent`
- **THEN** X's coordinator exposes two subagent tools and Y's exposes three, each on
  the models their profile resolved

#### Scenario: Composer runs for every principal

- **WHEN** any principal completes a turn
- **THEN** the composer writes the final Polish reply, independent of that principal's
  roster
