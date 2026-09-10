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

### Requirement: Enabled agents form a union unless explicitly disabled

The system SHALL treat an agent as part of a principal's roster when any applicable
layer enables it and no higher-precedence layer explicitly disables it. Because `unset`
never disables, the roster SHALL be the union of agents enabled across the user's
groups, from which an explicit `enabled = false` in a higher-precedence layer (a
higher-priority group, or the user) removes an agent.

#### Scenario: Roster is the union across groups

- **WHEN** a user's group A enables `iot_operator` and group B does not mention it
- **THEN** the user's roster includes `iot_operator`

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
