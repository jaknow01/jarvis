## ADDED Requirements

### Requirement: Conversations and messages are persisted per channel

The system SHALL persist each conversation and its messages in the primary data
store. A conversation SHALL be identified by a stable per-channel key (e.g. `repl`,
`messenger:{psid}`) so different channels and users never share history. Each stored
message SHALL record its role (`user` or `assistant`), its content, its order within
the conversation, and a creation timestamp.

#### Scenario: First turn creates a conversation

- **WHEN** a message arrives for a channel key that has no conversation yet
- **THEN** the system creates a conversation for that key before persisting the
  message

#### Scenario: Only the user message and final reply are stored

- **WHEN** a turn completes
- **THEN** the system persists exactly two messages for that turn — the user's
  message (role `user`) and the composer's final reply (role `assistant`) — and does
  not persist the internal coordinator/sub-agent/tool exchanges

### Requirement: Full conversation history is replayed as context each turn

On every turn the system SHALL load the full prior history of the conversation and
pass it to the coordinator as chat history, in chronological order. The system — not
the model provider — SHALL be the source of truth for conversation continuity; the
change MUST NOT rely on the OpenAI Responses API `previous_response_id`.

#### Scenario: Continuation references earlier context

- **WHEN** a user sends a follow-up that depends on something said earlier in the
  same conversation
- **THEN** the coordinator receives the earlier user and assistant messages as chat
  history and can resolve the reference

#### Scenario: Separate channels stay isolated

- **WHEN** two conversations exist under different channel keys
- **THEN** a turn on one channel is given only that channel's history

### Requirement: Assistant turns are linked to their MLflow trace

When tracing is enabled, the system SHALL record the MLflow trace id of the turn's
run on the persisted assistant message, so a stored reply can be traced back to its
run. When tracing is disabled, the assistant message SHALL still be persisted with an
empty trace id.

#### Scenario: Traced turn records its trace id

- **WHEN** tracing is enabled and a turn completes
- **THEN** the assistant message row for that turn carries the run's MLflow trace id

#### Scenario: Untraced turn persists without a trace id

- **WHEN** tracing is disabled and a turn completes
- **THEN** the assistant message row is persisted with no trace id and the turn
  otherwise behaves identically

### Requirement: History store selects backend like the other stores

The conversation store SHALL use Postgres when `DATABASE_URL` is configured and fall
back to an on-disk JSON store otherwise, following the same backend-selection pattern
as the memory and scheduler stores.

#### Scenario: Postgres backend

- **WHEN** `DATABASE_URL` is set
- **THEN** conversations and messages are read from and written to Postgres

#### Scenario: JSON fallback

- **WHEN** `DATABASE_URL` is not set
- **THEN** conversations and messages are read from and written to the on-disk JSON
  store, and turns behave identically
