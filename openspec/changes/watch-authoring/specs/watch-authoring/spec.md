## ADDED Requirements

### Requirement: Generator emits Starlark by convention

The authoring pipeline SHALL generate the predicate as Starlark defining `check(data) -> bool`,
prompting the generator model with the language constraints (what Starlark forbids), the injected
helpers available (e.g. `get(data, "a.b", default)`), and the calling convention, so the produced
code targets the sandbox contract.

#### Scenario: Generated predicate follows the convention

- **WHEN** the pipeline generates a predicate for an intent
- **THEN** the output is Starlark defining `def check(data)` returning a boolean, using only allowed
  constructs and injected helpers

### Requirement: Deterministic normalizer for common Python-isms

Before validation, the pipeline SHALL run a deterministic (non-LLM) normalizer that mechanically
rewrites the most common Python constructs a model reaches for but Starlark rejects (for example
f-strings to `.format()`), reducing avoidable repair round-trips. The normalizer is intentionally
minimal and MUST NOT alter predicate semantics.

#### Scenario: Common Python-ism is normalized without an LLM

- **WHEN** generated code contains a construct the normalizer handles (e.g. an f-string)
- **THEN** it is rewritten to a Starlark-valid equivalent before validation, with no LLM call and no
  change in meaning

### Requirement: Validation runs in the sandbox

The pipeline SHALL validate candidate code by calling the `predicate-sandbox` `validate` mode; it
MUST NOT maintain a separate local Starlark. On failure, the sandbox's error text SHALL be captured
for repair.

#### Scenario: Invalid code is caught by the sandbox

- **WHEN** normalized code is validated
- **THEN** validation is performed by the sandbox, and on failure its error message is captured

### Requirement: Bounded iterative repair with a dedicated model, fail-closed

When validation fails, the pipeline SHALL send the broken code, the sandbox error text, the intent
summary, and the Starlark rules to a dedicated repair model (resolved via
`LLM_BY_AGENT["watch_coder"]`), then re-validate — iterating if the repair still fails. The loop
MUST be bounded by a maximum number of attempts; if it is not valid within the cap, the pipeline
SHALL fail closed (it MUST NOT arm the watch) and surface the situation to the user.

#### Scenario: Repair converges within the cap

- **WHEN** the first candidate is invalid and a repair produces valid code within the attempt cap
- **THEN** the pipeline proceeds with the repaired, validated code

#### Scenario: Repair exhausts the cap

- **WHEN** repair still yields invalid code after the maximum attempts
- **THEN** no watch is armed and the user is asked to clarify the condition, rather than storing broken
  or partial code

#### Scenario: Repair model is separately configurable

- **WHEN** `AGENT_WATCH_CODER_MODEL` is set
- **THEN** the repair step uses that model, independent of the generator model

### Requirement: Semantic smoke-test with user confirmation

After the code validates, the pipeline SHALL fetch the watch's source once (via the `watch-sources`
registry) and run `evaluate(code, sample)` in the sandbox, presenting the concrete result to the user
for confirmation before the watch is armed. A watch SHALL NOT be armed without this confirmation step.

#### Scenario: Current result is shown before arming

- **WHEN** validated code is smoke-tested against a freshly fetched sample
- **THEN** the user is shown what the predicate currently returns (and on what data) and asked to confirm
  before the watch is stored/armed

#### Scenario: Fetch unavailable during smoke-test

- **WHEN** the source fetch for the smoke-test is unavailable (unknown)
- **THEN** the pipeline reports it and does not silently arm a watch it could not smoke-test

### Requirement: Two-step creation — prepare (no arm) then arm on confirmation

Watch creation SHALL be split into a prepare step and an arm step so confirmation can happen
between them. A `prepare_watch` tool SHALL run the authoring pipeline (normalize → validate →
repair → smoke-test) and return the validated code plus the current smoke result **without
arming**. A `create_watch` (arm) tool SHALL, after the user confirms, re-validate the code as a
safety net and store the watch only if it validates. This supersedes the verbatim-storage
behavior introduced in `watch-execution`.

#### Scenario: Prepare validates and smoke-tests but does not arm

- **WHEN** `prepare_watch` succeeds
- **THEN** it returns the validated `check_code` and the current predicate result, and no watch
  is stored yet

#### Scenario: Arm stores only validating code

- **WHEN** `create_watch` is called to arm a watch
- **THEN** the predicate is re-validated and the watch is stored only if it validates; otherwise
  an error is returned and nothing is armed
