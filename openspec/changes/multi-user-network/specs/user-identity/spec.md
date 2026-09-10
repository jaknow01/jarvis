## ADDED Requirements

### Requirement: Inbound identity resolves to a user via a manual whitelist

The system SHALL resolve every inbound message's `(channel, external_id)` pair to a
`user_id` by looking it up in a `user_identities` whitelist. A pair present in the
whitelist SHALL yield its `user_id`; a pair absent from the whitelist SHALL be
rejected and the message ignored, with no conversation, memory, or agent run created.
The system SHALL NOT auto-provision users from unknown senders.

#### Scenario: Known sender is resolved

- **WHEN** a Messenger message arrives from an `external_id` that has a
  `user_identities` row for channel `messenger`
- **THEN** the system resolves it to that row's `user_id` and proceeds to run the turn
  for that user

#### Scenario: Unknown sender is ignored

- **WHEN** a message arrives from an `external_id` with no matching `user_identities`
  row for its channel
- **THEN** the system ignores the message, runs no agent, and stores nothing

#### Scenario: The same person across channels maps to one user

- **WHEN** two `user_identities` rows for the same `user_id` exist, one for channel
  `messenger` and one for a future channel
- **THEN** messages on either channel resolve to the same `user_id` and share that
  user's memory and profile

### Requirement: A channel/external-id pair identifies at most one user

The system SHALL enforce uniqueness of `(channel, external_id)` in the whitelist so a
single sender identity can never resolve to two different users.

#### Scenario: Duplicate identity is rejected at write time

- **WHEN** an attempt is made to add a `user_identities` row whose `(channel,
  external_id)` already exists
- **THEN** the system rejects the addition rather than creating an ambiguous mapping

### Requirement: The legacy single-sender allowlist is migrated into the whitelist

The system SHALL treat the pre-existing `MESSENGER_ALLOWED_SENDER_ID` owner as a
seeded `user_identities` row (channel `messenger`) for the owner user, so the current
single-user deployment keeps working after the change without manual reconfiguration.
`MESSENGER_ALLOWED_SENDER_ID` SHALL no longer act as the runtime access gate.

#### Scenario: Owner keeps access after migration

- **WHEN** the migration runs on a deployment whose only configured sender is
  `MESSENGER_ALLOWED_SENDER_ID`
- **THEN** that sender resolves to the owner `user_id` via `user_identities` and can
  message the assistant exactly as before
