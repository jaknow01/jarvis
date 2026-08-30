## ADDED Requirements

### Requirement: Watch job type

The scheduler SHALL support a new job kind `watch` alongside the existing `once` and `cron`
kinds. A watch job SHALL persist its `source` (source-id), `params`, `check_code` (the
predicate as text), `message`, `poll_interval`, and `last_result`, using the same
JSON/Postgres backend selection as the existing scheduler store. Postgres columns MUST be
added idempotently (`ADD COLUMN IF NOT EXISTS`) so startup stays safe on pre-existing tables.

#### Scenario: Create a watch job

- **WHEN** a watch is created with a source, params, `check_code`, message, and poll_interval
- **THEN** it is stored with `kind = "watch"`, `last_result` initialized to unknown/none, and
  a `next_run_at` one poll_interval ahead

#### Scenario: Existing time-based jobs are unaffected

- **WHEN** a watch job and a `cron` job coexist in the store
- **THEN** the `cron` job's scheduling and firing behavior is unchanged by the presence of the watch

### Requirement: Interval polling of watches

The scheduler runner SHALL evaluate a watch when its `next_run_at` is due, then re-arm it one
`poll_interval` into the future, so an active watch is polled repeatedly at its configured
interval until it terminates.

#### Scenario: Watch is re-armed after a poll

- **WHEN** a watch is polled at time T and does not fire
- **THEN** its `next_run_at` is set to `T + poll_interval` and it remains active

### Requirement: Predicate evaluation contract

The runner SHALL evaluate a watch's condition by fetching the source data and passing
`{code, data}` to a predicate evaluator that returns a boolean (or an unknown result). The
evaluator is defined by the stable contract `{code, data} -> bool`; in this change it is an
in-process stub, and the production isolated implementation is provided by a separate change
(`watch-sandbox`) behind the same contract.

#### Scenario: Predicate evaluated against fetched data

- **WHEN** a watch is polled and the source returns data `D`
- **THEN** the evaluator is invoked with the watch's `check_code` and `D`, and returns a boolean

#### Scenario: Evaluator is swappable behind the contract

- **WHEN** the in-process stub evaluator is replaced by another implementation of `{code, data} -> bool`
- **THEN** the runner's behavior is unchanged as long as the contract is honored

### Requirement: Edge-triggered delivery

The runner SHALL deliver a watch's `message` exactly once, on the transition of the predicate
result from false to true, determined by comparing the current result to the stored
`last_result`. While the predicate stays true across subsequent polls, the runner MUST NOT
re-deliver.

#### Scenario: Fires once on false to true

- **WHEN** a watch whose `last_result` is false is polled and the predicate now returns true
- **THEN** the message is delivered once and `last_result` is updated to true

#### Scenario: No repeat while condition stays true

- **WHEN** a watch whose `last_result` is already true is polled and the predicate returns true again
- **THEN** no message is delivered

#### Scenario: No fire while condition stays false

- **WHEN** a watch whose `last_result` is false is polled and the predicate returns false
- **THEN** no message is delivered and `last_result` remains false

### Requirement: Unknown reads do not trigger

When a source read is unknown (the fetch failed or returned an error result) the runner SHALL
treat it as neither true nor false: it MUST NOT deliver a message, MUST NOT advance
`last_result`, and MUST leave the watch active to retry on the next poll.

#### Scenario: Failed fetch is not a false result

- **WHEN** a watch is polled and the source read is unknown (fetch error)
- **THEN** no message is delivered, `last_result` is unchanged, and the watch remains active

### Requirement: Termination after firing

A single-condition watch SHALL terminate after it delivers its message: its status becomes
complete and it is no longer polled. (Multi-step sequences are out of scope for this version.)

#### Scenario: Watch completes after delivering

- **WHEN** a single-condition watch delivers its message
- **THEN** its status is set to complete and it has no further `next_run_at`

### Requirement: create_watch tool stores the predicate verbatim

The `scheduler_agent` SHALL expose a `create_watch` tool taking `source`, `params`,
`check_code`, `message`, and `poll_interval`, and store the job with `check_code` persisted
as given. Generation, normalization, validation, and repair of `check_code` are out of scope
here (a separate `watch-authoring` change owns them).

#### Scenario: Tool persists the provided predicate text

- **WHEN** `create_watch` is called with a `check_code` string
- **THEN** a watch job is stored whose `check_code` equals the provided string, unmodified
