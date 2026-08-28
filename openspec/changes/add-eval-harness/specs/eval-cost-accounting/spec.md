## ADDED Requirements

### Requirement: Per-agent token attribution

The harness SHALL attribute input and output token usage to the specific agent role that
produced each LLM call, using the hierarchical spans captured from the LlamaIndex
dispatcher (the active agent identifies the span). Attribution MUST work regardless of the
underlying provider (OpenAI, Anthropic, OpenRouter, groq, xAI, openai-compat).

#### Scenario: Tokens split across roles in one turn

- **WHEN** a single turn invokes the coordinator and two sub-agents on different models
- **THEN** the harness reports separate input/output token counts for the coordinator and
  for each of the two sub-agents

#### Scenario: Provider-agnostic counting

- **WHEN** a role runs on a non-OpenAI provider (e.g. an OpenRouter model)
- **THEN** its token usage is still attributed to that role from the span data

### Requirement: Configurable price table and cost computation

The harness SHALL compute cost in USD by multiplying attributed token counts by per-token
prices resolved from a configurable table (`evals/price_table.yaml`) keyed by
`provider/model`. When a model is missing from the table, the harness SHALL surface the gap
(reporting the model as unpriced) rather than silently reporting zero cost.

#### Scenario: Cost from token counts and price table

- **WHEN** a role used N input and M output tokens on a priced `provider/model`
- **THEN** its reported cost equals `N * cost_in + M * cost_out` from the table, in USD

#### Scenario: Missing price is surfaced

- **WHEN** a role ran on a `provider/model` absent from the price table
- **THEN** the report flags that model as unpriced instead of reporting `0` cost

### Requirement: Judge cost accounted separately

The harness SHALL account for the LLM-as-judge (metric evaluation) token usage and cost
separately from the agent-under-test cost, so agent cost comparisons are not polluted by
evaluation overhead.

#### Scenario: Agent cost excludes judge cost

- **WHEN** a run uses a judge model to score metrics
- **THEN** the reported per-role agent cost does not include the judge's tokens, and the
  judge cost is reported as its own line item
