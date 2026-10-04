# Jarvis evaluation harness

An **isolated, non-invasive** harness (built on [deepeval](https://deepeval.com)) that
measures the agent network's **quality** and **token cost per role**, and sweeps candidate
models per role to pick an optimal quality × cost point.

Nothing here is imported by production code under `lib/` or `app/`; the harness only imports
*from* them. It has its own virtualenv so `deepeval` never touches the production
`poetry.lock`.

## What it measures

Jarvis has three evaluation levels, mirroring its architecture (coordinator → sub-agents
as tools → composer):

| Level | Actor | What we score |
|------|-------|----------------|
| 1 — routing | coordinator | did it call the right sub-agents? (`routing_correctness`, optional) |
| 2 — execution | sub-agent | did it call the right real tools? (`execution_correctness`, optional) |
| 3 — composition | composer | is the final Polish reply relevant + faithful to gathered data? (`composer_quality_pl`, always) |

Alongside quality, every run captures **per-agent token usage** (provider-agnostic) and
converts it to **USD** via `price_table.yaml`. The LLM judge's own cost is accounted
separately from agent cost.

## Setup

```bash
bash evals/scripts/bootstrap.sh            # builds evals/.venv (python3.12 by default)
# or pin the interpreter explicitly:
bash evals/scripts/bootstrap.sh python3.12
```

The bootstrap installs the production project as an editable path dependency
(`pip install -e ..` → prod deps + `lib` importable) and then this eval project
(`deepeval`). Requires the repo `.env` with provider keys and a judge model:

```bash
EVAL_JUDGE_MODEL=gpt-4o        # one strong, constant judge for the whole sweep
```

Agent models come from the same model profiles the app uses (`config/models/<name>.yaml`,
selected by `MODEL_PROFILE`, or per sweep with `profile:`) — the harness merges each
candidate's role override on top and forces that profile for the run, no code changes.

## Quick health check

```bash
evals/.venv/bin/python -m evals.spike "Jaka jest pogoda w Warszawie i ile kosztuje dolar?"
```

Prints per-role token usage, the two-level tool calls, and per-role USD cost — the capture
mechanism the whole harness relies on. No judge, so it's cheap.

## Running a sweep

Sweeps are defined by a small **YAML config** (`evals/sweeps/*.yaml`) so experiments live in
data — easy to review, diff, and modify. See `evals/sweeps/composer_example.yaml` and the
schema in `harness/sweep_config.py`.

```bash
evals/.venv/bin/python -m evals.scripts.run_sweep evals/sweeps/composer_example.yaml
# or by bare name (resolves to evals/sweeps/<name>.yaml):
evals/.venv/bin/python -m evals.scripts.run_sweep composer_example
```

The config sets the swept `role`, `candidates` (model + optional `reasoning_effort`), the
constant `judge_model`, `mock`, the `dataset`, an optional `baseline` (fixed models for the
other roles), and an optional subset (`limit` / `only_ids`) for cheap first runs.

Every candidate is evaluated on the **same** goldens (fair comparison), scored by the
constant judge, and written to `evals/reports/<name>.json` + `<name>.md`. The markdown is the
quality × cost table: one row per candidate, metric means + pass rates next to the swept
role's mean USD and the total USD per turn. Only the swept role varies; every other role
stays on the baseline. Sweep any role (`coordinator`, `weather_agent`, …) the same way.

Programmatic use: `SweepConfig.load(path)` → `run_sweep_from_config(cfg)`.

### One runner for every kind of test

`python -m evals.run <config> [--only KIND]` runs all agent tests from one config, or just
one kind:

```bash
python -m evals.run evals/sweeps/composer_example.yaml                 # everything configured
python -m evals.run evals/sweeps/composer_example.yaml --only goldens   # single/multi-hop only
python -m evals.run evals/sweeps/composer_example.yaml --only scripted  # deterministic convos only
python -m evals.run evals/sweeps/composer_example.yaml --only simulated # dynamic convos only
```

| kind | what | config field it needs |
|------|------|-----------------------|
| `goldens` | single/multi-hop questions | `dataset:` (e.g. `labeled`) |
| `scripted` | deterministic conversations | `conversations:` (e.g. `conversations`) |
| `simulated` | dynamic, LLM-driven conversations | `simulations:` + `simulator_model:` |

Goldens + scripted share one **deterministic** report (`<name>-deterministic`, or
`-goldens` / `-scripted` when run alone). Simulated is reported separately
(`<name>-simulated`) — it is non-deterministic and runs via the simulator's own event loop.

### Where conversation model-sweeps are controlled

The **same** `role` + `candidates` in the sweep config drive the sweep for *all three kinds*.
So to compare models on conversations, you list your candidate models once and add the
relevant dataset field:

- scripted-conversation sweep → add `conversations: conversations`;
- simulated-conversation sweep → add `simulations: simulations` and `simulator_model:` (the
  LLM that role-plays the user; defaults to the base profile's `default` model).

Each candidate is evaluated on the same conversations, per-turn **and** at the conversation
level (`conversation_quality_pl`, `knowledge_retention`), with per-turn cost summed.

## Mocked tools (deterministic benchmark)

By default every eval run **mocks the agents' tools** (`harness/mocks.py`, `mock=True`):

- **No side effects** — no real reminders created, lights toggled, or memory written.
- **Deterministic** — each tool returns a fixed realistic fixture (Warsaw weather 16°C,
  EUR/PLN 4.30, …), so the **model is the only variable** across a comparison.
- **Measurement stays intact** — mocks reuse each real tool's exact name/description/schema,
  so the agent still *decides* which tool to call; only the body is inert. Routing and
  execution correctness are captured exactly as before, and the composer still gets plausible
  data (so `composer_quality_pl` remains meaningful and reproducible).

This encodes the separation the benchmark is built on: *does the agent reach for the right
tool?* (measured here) is distinct from *does the tool work?* (a separate integration-test
concern). Pass `mock=False` to `run_golden` / `sweep_role` / `run_conversation` for a "live"
run against the real tools and APIs.

## Multi-turn conversations

Beyond single-turn Q&A, the harness runs **scripted conversations** — a fixed sequence of
user turns through one `conversation_id`, so the app replays accumulating history each turn
(`harness/conversation.py`, seeds in `harness/conversations_seed.py`). Same models, same
turns, mocked tools => a deterministic multi-turn benchmark.

Two scoring layers:
- **Per-turn** — the single-turn metric set on each turn (`composer_quality_pl` + optional
  routing/execution).
- **Conversation-level** — deepeval multi-turn metrics: `conversation_quality_pl`
  (`ConversationalGEval`: Polish role adherence + cross-turn context) and
  `knowledge_retention` (recall facts stated earlier — relevant to `memory_operator`).

Dynamic **simulated** conversations (`harness/simulator.py`) are the complementary,
non-deterministic layer: deepeval's `ConversationSimulator` role-plays the owner (persona +
scenario) and talks to the assistant until the expected outcome is reached. The simulated
user runs on any provider via the adapter (e.g. deepseek), and each conversation is converted
into the same `ConversationRun`, so `score_conversation` scores it identically. Run it from a
sync context (the simulator manages its own loop); conversations run sequentially
(`max_concurrent=1`) to keep the global tool-mock and per-turn capture clean.

## Datasets

- **Seed** (`evals/harness/datasets.py` → `ensure_seed()`): a small curated base in Polish,
  tailored to the owner (Warsaw, PLN). A few goldens carry `expected_routing` to exercise the
  optional routing metric.
- **Synthetic** (`generate_goldens(n, model, name=...)`): a thin wrapper over deepeval's
  `Synthesizer`, generating fresh persona-styled inputs. Reference-free (input only); label a
  subset by hand for the optional ToolCorrectness metrics.

Everything under `evals/datasets/` is JSON, so it can be reviewed and edited as data
(the code seeds are written to JSON on first use via `ensure_*`, and loaders read the JSON):

- `labeled.json` — the 42 single/multi-hop goldens (with `expected_routing` / `expected_execution`).
- `conversations.json` — scripted multi-turn conversations (`ensure_scripted()` / `load_conversations()`).
- `simulations.json` — dynamic simulation scenarios: scenario + expected_outcome
  (`ensure_simulations()` / `load_sim_scenarios()`, fed to the simulator).

## How it works (and one gotcha)

- **Token/cost capture** uses our own LlamaIndex event + span handlers
  (`harness/instrument.py`). deepeval flushes and clears its own traces after each run when
  there is no Confident AI key, so its offline trace store is empty — capturing usage
  ourselves is both robust and provider-agnostic (verified for OpenAI Responses *and*
  OpenAI-compatible/OpenRouter). Attribution walks each LLM span up to the nearest named
  `FunctionAgent.run` span (`instance.name` = role); a sub-agent nested in the coordinator's
  tool call resolves to the sub-agent.
- **Metrics** are scored with deepeval's async `a_measure` (not the sync `measure`), which
  avoids `nest_asyncio` re-entrancy — that combination breaks under Python 3.14's asyncio.
- **Prices** are maintained by hand in `price_table.yaml` (USD per 1M tokens) because
  deepeval's automatic cost only covers OpenAI/Anthropic/Gemini and would leave
  OpenRouter/groq/xai uncovered. **Verify the numbers against each provider before trusting a
  comparison.** Models missing from the table are reported `unpriced`, never silently zero.

## Layout

```
evals/
  pyproject.toml        deepeval + pyyaml; prod deps via editable path install (see bootstrap)
  price_table.yaml      provider/model -> USD per 1M tokens (hand-maintained; verify!)
  spike.py              cheap capture health-check on the real network
  harness/
    instrument.py       per-agent token + tool-call capture (own LlamaIndex handlers)
    cost.py             token counts -> USD via the price table
    config.py           EvalConfig + env overrides (apply/restore), JudgeConfig
    driver.py           run one golden through the real engine.handle_message, under capture
    metrics.py          reference-free metric set (GEval PL + optional ToolCorrectness)
    datasets.py         owner-persona seed + deepeval Synthesizer wrapper + load/save
    sweep.py            grid one role over {model, effort}, aggregate quality × cost
    report.py           JSON + Markdown quality × cost tables
  scripts/
    bootstrap.sh        build the isolated venv
    run_sweep.py        example sweep CLI
  datasets/             persisted goldens (JSON)
  reports/              JSON + Markdown outputs
```
