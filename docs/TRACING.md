# Tracing: agent observability with MLflow

## Why

Jarvis is a multi-agent network: the coordinator delegates to sub-agents, which
call function-tools, sometimes in parallel. When a run stalls, loops, or returns a
wrong answer, the logs alone don't show **what ran, in what order, and with which
inputs/outputs**. MLflow Tracing captures every coordinator run as a hierarchical
**trace** — coordinator → sub-agent → tool — with per-span timing, arguments and
results, so you can see the call tree and pinpoint where things go wrong.

This is a **developer aid**. It is fully out-of-band: enabling tracing does not
change how any agent behaves, and any failure (MLflow down/unreachable) is logged
and swallowed so it can never block the assistant.

## How it's wired

```
app/main.py         load_dotenv() -> setup_tracing()   (once, at startup)
lib/tracing.py      setup_tracing(): set_tracking_uri + set_experiment
                    + mlflow.openai.autolog()  (registers the Agents SDK
                    trace processor; captures agent/tool spans)
docker-compose.yml  "mlflow" service: MLflow tracking server + UI
```

The OpenAI Agents SDK has its own tracing pipeline (traces/spans). MLflow's
`mlflow.openai.autolog()` plugs a trace processor into that pipeline, so every
agent run and tool call is exported to the MLflow server automatically — no
changes to agent/tool code.

### Lightweight client, full server
The app depends only on **`mlflow-tracing`** (the lightweight tracing-only client;
~10 small deps, no numpy/pandas/scipy — deliberately, to avoid churning the
lockfile). It logs traces to the tracking server **over HTTP**; it has no local
file/sqlite store. The full MLflow server (UI + backend store) runs as its own
container from the official image.

## Configuration (`.env`)

| Var | Meaning | Default |
|-----|---------|---------|
| `TRACING_ENABLED` | Master on/off switch (mirrors the `AGENT_<NAME>_ENABLED` convention). A falsy value (`0/false/no/off`) **hard-disables** tracing even when a URI is set. | enabled |
| `MLFLOW_TRACKING_URI` | Where to send traces. **Unset/empty ⇒ tracing disabled** (no-op). | — (disabled) |
| `MLFLOW_EXPERIMENT` | Experiment name traces are grouped under. | `jarvis` |

Effective state:

| `TRACING_ENABLED` | `MLFLOW_TRACKING_URI` | Result |
|---|---|---|
| unset / true | set | tracing **on** |
| falsy (`0/false/no/off`) | set | tracing **off** (hard switch) |
| any | empty | tracing **off** (no target) |

- Local run (app on host): `MLFLOW_TRACKING_URI=http://localhost:5001`
- Inside docker-compose: the app service already gets `MLFLOW_TRACKING_URI=http://mlflow:5000`
  (compose-internal host `mlflow`, port 5000).

## The docker-compose `mlflow` service

```bash
# start just the tracking server (UI at http://localhost:5001)
docker compose up -d mlflow
# or bring up the whole stack (app already points at the mlflow service)
docker compose up -d
```

- **UI:** http://localhost:5001 → **Traces** tab (host port is **5001**, not 5000,
  to dodge the macOS AirPlay/ControlCenter conflict on :5000; the container still
  listens on 5000 internally).
- **Backend store:** SQLite at `/mlflow/store/mlflow.db`, artifacts under
  `/mlflow/artifacts`, both on the named volume `mlflow_data` (traces survive
  restarts).

## Verifying it works

Live smoke test (needs the server; kept out of the pytest suite):

```bash
docker compose up -d mlflow
MLFLOW_TRACKING_URI=http://localhost:5001 poetry run python smoke-tests/mlflow_trace.py
```

It emits a synthetic coordinator→sub-agent→tool trace via the Agents SDK's own
tracing primitives (no OpenAI key/model call needed), reads it back over HTTP, and
asserts the nested spans landed. Exit code 0 = success.

Network-free unit tests for the guard logic live in `tests/test_tracing.py`
(`poetry run pytest tests/test_tracing.py`).

## Notes / gotchas

- With `TRACING_ENABLED` falsy, or no `MLFLOW_TRACKING_URI`, `setup_tracing()` is a
  no-op — nothing is installed, no overhead. Real runs opt in by setting the URI;
  `TRACING_ENABLED=false` is the kill-switch that leaves the URI in place.
- The Agents SDK also has its own default OpenAI trace exporter; when no OpenAI key
  is present it logs a harmless `"skipping trace export"` line. MLflow tracing is
  independent of that.
- Turn tracing off at runtime with `mlflow.openai.autolog(disable=True)`.
