"""Live smoke test for MLflow agent tracing.

Verifies the full path the app uses in production: the lightweight
``mlflow-tracing`` client → MLflow tracking server over HTTP. It enables tracing
via ``lib.tracing.setup_tracing()``, emits a synthetic OpenAI Agents SDK trace
(a coordinator run delegating to two sub-agents, each calling a function-tool),
then reads the trace back from the server and asserts the nested spans landed.

No OpenAI key or model call is needed — the trace is built from the SDK's own
tracing primitives, so this isolates the observability plumbing.

> Like the Tuya probes here, this hits a real service (the MLflow server) and is
> intentionally kept out of the pytest suite.

## Running it

    # 1. Start just the MLflow server (UI at http://localhost:5001)
    docker compose up -d mlflow

    # 2. Point the client at it and run the check
    MLFLOW_TRACKING_URI=http://localhost:5001 poetry run python smoke-tests/mlflow_trace.py

Exit code is 0 on success, 1 if no trace / expected spans were captured. Override
the experiment with MLFLOW_EXPERIMENT (defaults to the smoke experiment below).
Open the UI afterwards and check the "Traces" tab to eyeball the run.
"""
import os
import sys

SMOKE_EXPERIMENT = "jarvis_smoketest"


def main() -> int:
    if not os.getenv("MLFLOW_TRACKING_URI", "").strip():
        print("MLFLOW_TRACKING_URI is not set — start the server and export it first.")
        print("  docker compose up -d mlflow")
        print("  MLFLOW_TRACKING_URI=http://localhost:5001 poetry run python smoke-tests/mlflow_trace.py")
        return 1

    os.environ.setdefault("MLFLOW_EXPERIMENT", SMOKE_EXPERIMENT)
    experiment = os.environ["MLFLOW_EXPERIMENT"]

    from lib.tracing import setup_tracing

    if not setup_tracing():
        print("setup_tracing() returned False — MLflow server unreachable?")
        return 1

    from agents import trace
    from agents.tracing import custom_span, function_span

    # Synthetic run: coordinator -> two sub-agents -> one tool each.
    with trace("coordinator_run"):
        with custom_span("weather_agent"):
            with function_span("get_forecast"):
                pass
        with custom_span("finance_agent"):
            with function_span("get_exchange_rate"):
                pass

    import mlflow

    traces = mlflow.search_traces(
        return_type="list", flush=True, max_results=5,
        order_by=["timestamp_ms DESC"],
    )
    if not traces:
        print(f"FAIL: no traces found in experiment '{experiment}'")
        return 1

    span_names = [s.name for s in traces[0].data.spans]
    print(f"traces found: {len(traces)}")
    print(f"latest span names: {span_names}")

    expected = {"coordinator_run", "weather_agent", "get_forecast",
                "finance_agent", "get_exchange_rate"}
    missing = expected - set(span_names)
    if missing:
        print(f"FAIL: latest trace is missing spans: {sorted(missing)}")
        return 1

    print("OK: nested coordinator/agent/tool trace captured in MLflow over HTTP")
    return 0


if __name__ == "__main__":
    sys.exit(main())
