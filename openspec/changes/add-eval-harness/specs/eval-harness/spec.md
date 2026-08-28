## ADDED Requirements

### Requirement: Isolated evaluation module

The system SHALL provide an evaluation harness as a standalone `evals/` module with its
own `pyproject.toml` and virtual environment, keeping `deepeval` and its transitive
dependencies out of the production `poetry.lock`. The evals project SHALL declare `jarvis`
as a path dependency so the harness imports `lib/` and runs the real agents without
duplicating the production dependency list.

#### Scenario: Eval dependencies do not touch production lockfile

- **WHEN** the evals environment is installed and `deepeval` is added or upgraded
- **THEN** only the `evals/` lockfile changes and the production `poetry.lock` is unchanged

#### Scenario: Harness imports production code

- **WHEN** the harness runs inside the evals venv
- **THEN** it can import `lib/` (e.g. `from lib.runtime.engine import handle_message`) and
  execute a real agent turn without a separate production install step

### Requirement: Non-invasive LlamaIndex instrumentation

The harness SHALL capture component-level spans (coordinator, sub-agents, LLM calls, tool
calls) by registering deepeval's instrumentation against the LlamaIndex dispatcher
(`instrument_llama_index(get_dispatcher())`) at evaluation time only. It MUST NOT require
adding `@observe` decorators or any deepeval import to production code under `lib/` or
`app/`.

#### Scenario: Production code is unchanged

- **WHEN** the change is implemented
- **THEN** no file under `lib/` or `app/` imports `deepeval` or is decorated for evaluation

#### Scenario: Per-agent spans are captured

- **WHEN** the harness instruments the dispatcher and runs one turn through the coordinator
- **THEN** the resulting trace contains distinguishable spans for the coordinator and for
  each sub-agent that was invoked, each attributable to a specific agent role

### Requirement: Real-agent driver over goldens

The harness SHALL provide a driver that, for a given evaluation configuration, runs the
real agent network via the production entry point (`engine.handle_message`) against each
golden's input and collects the produced trace and final reply. The driver SHALL apply a
configuration by setting the per-agent environment overrides (`AGENT_<NAME>_MODEL`,
`AGENT_<NAME>_REASONING_EFFORT`) before the agent is built, relying on the existing
config-driven `LLM_BY_AGENT` resolution rather than modifying production code.

#### Scenario: Configuration override is honored without code changes

- **WHEN** the driver sets `AGENT_WEATHER_AGENT_MODEL` for a run and executes a golden
- **THEN** the `weather_agent` runs on the overridden model for that run and the override
  does not leak into other runs

#### Scenario: A golden produces a scored turn

- **WHEN** the driver executes a golden input end to end
- **THEN** it returns the final reply plus the captured trace, ready for metric scoring

### Requirement: Reference-free agentic metrics per role

The harness SHALL score each role with metrics that require no ground-truth labels:
`TaskCompletion` for trajectory/outcome alignment, `ArgumentCorrectness` for tool-argument
quality, `ToolUse` for tool selection, and a Polish-language `GEval` on the composer's final
reply (relevance, faithfulness to gathered data, absence of hallucination, tone/language).
`ToolCorrectness` (which requires `expected_tools`) SHALL be optional and applied only to a
subset that carries such labels.

#### Scenario: Scoring without expected labels

- **WHEN** a golden has an input but no `expected_output` or `expected_tools`
- **THEN** the reference-free metrics still produce scores for the relevant roles

#### Scenario: Composer answer is evaluated in Polish

- **WHEN** the composer produces a final reply
- **THEN** the `GEval` criteria evaluate it for Polish-language relevance, faithfulness to
  the gathered sub-agent data, and absence of invented content

#### Scenario: Optional tool-correctness on a labeled subset

- **WHEN** a golden carries `expected_tools`
- **THEN** `ToolCorrectness` is additionally computed for it, and its absence on other
  goldens does not fail the run

### Requirement: Synthetic dataset generation

The harness SHALL generate goldens using deepeval's `Synthesizer`, and SHALL support an
owner persona (Warsaw, Polish) with scenarios to drive multi-turn / task-completion cases.
Generated datasets SHALL be persisted under `evals/datasets/` so runs are reproducible.

#### Scenario: Goldens are generated and reused

- **WHEN** dataset generation runs
- **THEN** goldens are written under `evals/datasets/` and a later run can load the same
  goldens without regenerating them

### Requirement: File-based reporting

The harness SHALL write results to files under `evals/` as both machine-readable JSON and a
human-readable markdown table, without depending on an external cloud platform or the
production Postgres database. The report SHALL present per-role, per-model quality metrics
alongside cost.

#### Scenario: Run produces JSON and markdown

- **WHEN** an evaluation run completes
- **THEN** a JSON results file and a markdown quality-versus-cost table are written under
  `evals/`

#### Scenario: No external result sink required

- **WHEN** the harness runs without cloud credentials or a database connection
- **THEN** reporting still succeeds against local files only
