## ADDED Requirements

### Requirement: Named source registry reusing subagent fetchers

The system SHALL provide a registry mapping a `source-id` to a fetcher that returns a
structured `dict`, reusing the existing subagent data-fetching functions (e.g. finance, FX,
weather forecast, FPL reference) rather than introducing new external integrations. The
registry SHALL be the only entry point by which a watch obtains data.

#### Scenario: Source resolves to a subagent fetcher

- **WHEN** a watch names a registered `source-id` with `params`
- **THEN** the registry invokes the corresponding subagent fetcher with those params and
  returns the resulting `dict`

#### Scenario: Unknown source-id is rejected at creation

- **WHEN** a watch is created naming a `source-id` that is not in the registry
- **THEN** creation fails with a clear error rather than storing an unresolvable watch

### Requirement: Deterministic fetch without an LLM

Source fetching SHALL be deterministic and MUST NOT run an agent/LLM turn to obtain data —
the registry calls the data functions directly. Because the workflow `ctx` argument is
decorative for these fetchers (shared state is resolved from a contextvar, not the argument),
the registry MAY call them without a live workflow context.

#### Scenario: Fetch does not invoke a model

- **WHEN** a source is fetched during a poll
- **THEN** the data is obtained by a direct function call with no LLM/agent invocation

### Requirement: Three-state read result

A source read SHALL resolve to one of three states: `true` or `false` (the predicate applied
to a successfully fetched `dict`), or `unknown` when the fetch fails or returns an error
result (for example a `dict` carrying an `Error` field). The `unknown` state MUST be
distinguishable from `false` by the runner.

#### Scenario: Error result maps to unknown

- **WHEN** a source fetcher returns an error result (e.g. `{"Error": ...}`)
- **THEN** the read resolves to `unknown`, not to `false`

#### Scenario: Successful fetch yields a boolean via the predicate

- **WHEN** a source fetcher returns a valid data `dict`
- **THEN** the predicate is applied to it and the read resolves to `true` or `false`
