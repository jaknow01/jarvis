## ADDED Requirements

### Requirement: Users and groups with multi-group membership

The system SHALL model users, groups, and a many-to-many membership between them, so a
user MAY belong to multiple groups. Each group SHALL carry an integer `priority` used
to order groups during profile resolution.

#### Scenario: A user belongs to multiple groups

- **WHEN** a user is a member of both the `dom` group and the `dzieci` group
- **THEN** profile resolution for that user considers the configuration of both groups

#### Scenario: Groups carry a priority

- **WHEN** two groups are defined with different `priority` values
- **THEN** the lower-priority group is applied before the higher-priority one during
  resolution, so the higher-priority group's explicit settings win on conflict

### Requirement: Owner-only management of users and groups

The system SHALL expose owner-only administrative commands on the `/` command surface
to manage identities, users, groups, and membership without invoking the agent stack.
At minimum it SHALL support adding a whitelisted identity, listing users, assigning a
user to groups, and creating/configuring groups. These commands SHALL be handled
deterministically (no LLM) and SHALL be restricted to the owner.

#### Scenario: Owner adds and assigns a user

- **WHEN** the owner issues the command to add a new identity and then assign that
  user to one or more groups
- **THEN** the whitelist and membership are updated, and the new user can message the
  assistant and receive that membership's roster

#### Scenario: Non-owner cannot manage users

- **WHEN** a non-owner user sends a user/group management command
- **THEN** the system does not perform the management action

### Requirement: Profile inspection command shows layered resolution

The system SHALL provide an owner-only command that reports, for a given user, the
resolved roster and per-agent model/reasoning together with which layer (`default`, a
named group, or `user`) set each effective value, to make resolution debuggable.

#### Scenario: Owner inspects a user's resolved profile

- **WHEN** the owner runs the profile-inspection command for a user
- **THEN** the system returns the effective roster and, per agent field, the layer
  that produced the winning value
