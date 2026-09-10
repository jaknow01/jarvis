## ADDED Requirements

### Requirement: Per-turn context is scoped to the acting principal

The system SHALL bind, for the duration of a turn, a shared run context scoped to the
acting principal, so that every tool in the turn reads state belonging to that
principal and never to another user. State written by one tool SHALL remain visible to
later tools within the same turn, and SHALL NOT leak into a concurrent turn for a
different principal.

#### Scenario: Concurrent turns do not cross-contaminate

- **WHEN** two users' turns run concurrently
- **THEN** each turn's tools see only their own principal's context, with no bleed
  between them

### Requirement: Context is composed of a group scope and a user scope

The system SHALL compose the per-turn context from two scopes: a **group scope**
holding shared physical resources (smart devices, device preferences, known addresses,
maps) loaded per `group_id`, and a **user scope** holding per-user cognition (long-term
memory, conversation history and its compression, and the resolved profile) keyed per
`user_id`. The group scope MAY be cached and reused across turns and users of the same
group; the user scope SHALL reflect the acting user.

#### Scenario: Household members share physical resources

- **WHEN** two users in the same group both ask to control a household device
- **THEN** both resolve the same group-scoped device inventory

#### Scenario: Cognition stays per user

- **WHEN** two users in the same group take turns
- **THEN** each sees only their own memory and conversation history, not the other's

#### Scenario: Group scope is loaded by group, not per user

- **WHEN** a user's turn begins and the group scope for their group is already cached
- **THEN** the turn reuses the cached group scope rather than reloading shared
  resources per user
