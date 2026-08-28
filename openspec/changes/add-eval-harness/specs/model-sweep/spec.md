## ADDED Requirements

### Requirement: Grid sweep over a single role

The sweep engine SHALL, for a chosen role, iterate a grid of `{model, reasoning_effort}`
candidates while holding all other roles at a fixed baseline configuration. Each grid point
SHALL be applied via the per-agent environment overrides consumed by the existing
`LLM_BY_AGENT` resolution, so no production code changes are needed to switch models.

#### Scenario: Only the swept role varies

- **WHEN** the sweep runs a grid for `composer` with three candidate models
- **THEN** each run changes only the composer's model/reasoning and every other role stays
  on the fixed baseline

#### Scenario: Reasoning effort is part of the grid

- **WHEN** a grid point specifies a reasoning effort (e.g. `low` vs `high`)
- **THEN** the run applies that effort to the swept role using the project's unified
  reasoning vocabulary, translated per provider

### Requirement: Constant judge across the sweep

The sweep SHALL use one fixed, strong judge model for all metrics across every grid point,
so quality scores are comparable between candidates.

#### Scenario: Judge does not change between candidates

- **WHEN** two candidate models for the same role are evaluated
- **THEN** both are scored by the same judge model configuration

### Requirement: Quality-versus-cost comparison table

The sweep SHALL aggregate the per-golden metric scores and attributed costs into a single
comparison table per role, listing each candidate's quality metrics and its cost, so an
optimal configuration can be selected. The table SHALL be emitted to the file-based report.

#### Scenario: One row per candidate

- **WHEN** a sweep over K candidates for a role completes
- **THEN** the comparison table contains K rows, each with the candidate's aggregated
  quality metrics and total/average cost

#### Scenario: Reproducible over a fixed dataset

- **WHEN** the same sweep is run twice over the same persisted goldens
- **THEN** it evaluates the same set of inputs for each candidate (differences come only
  from model nondeterminism, not from a changing dataset)
