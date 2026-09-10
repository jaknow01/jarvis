## ADDED Requirements

### Requirement: Long-term memory is scoped per user

The system SHALL scope long-term memory entries to a `user_id`. Reads (including the
memory profile injected into the coordinator prompt) and writes performed by the
`memory_operator` SHALL be confined to the acting user's entries. There SHALL be no
group-level or shared memory.

#### Scenario: Memory read returns only the user's entries

- **WHEN** the coordinator injects the memory profile for user X
- **THEN** it contains only user X's entries, none belonging to another user

#### Scenario: Memory write is attributed to the acting user

- **WHEN** the `memory_operator` stores a preference during user X's turn
- **THEN** the entry is persisted under user X and is not visible to any other user

#### Scenario: Uniqueness is scoped per user

- **WHEN** user X and user Y each save the same preference text in the same category
- **THEN** both are stored (uniqueness is enforced per `user_id`, not globally)

### Requirement: Conversation history and its compression are per user

The system SHALL keep each user's conversation history — and its rolling-window summary
and coverage pointer — isolated to that user, so continuity and compression never mix
across users.

#### Scenario: History does not cross users

- **WHEN** the engine builds the bounded chat history for a user's turn
- **THEN** it draws only from that user's conversation, with that user's rolling
  summary

### Requirement: Scheduled jobs are owned by a user and run as that user

The system SHALL associate each scheduled job with a `user_id`. When a due job fires as
a proactive `origin="system"` turn, the system SHALL run it under its owning user's
resolved profile and principal-scoped context, and SHALL deliver the result to that
user.

#### Scenario: A proactive job runs under its owner's profile

- **WHEN** a scheduled job owned by user X fires
- **THEN** the turn resolves user X's roster, context, and memory, and the reply is
  delivered to user X

#### Scenario: A user's jobs are listed and cancelled per user

- **WHEN** a user lists or cancels their scheduled jobs via the scheduler
- **THEN** only that user's jobs are visible and eligible for cancellation
