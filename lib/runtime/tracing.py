"""Agent observability via MLflow Tracing.

Enables MLflow auto-tracing for the OpenAI Agents SDK so every coordinator run,
sub-agent call and function-tool invocation is captured as a hierarchical trace
(what ran, in what order, with inputs/outputs and latency). This is a developer
aid for debugging where a run stalls or goes wrong.

Wiring is intentionally out-of-band: enabling tracing does not change how agents
behave. It is controlled entirely by environment variables and any failure
(missing/broken MLflow server) is logged and swallowed so it can never block the
assistant.

Environment variables:
    TRACING_ENABLED      Master on/off switch, mirroring the AGENT_<NAME>_ENABLED
                         convention. Enabled by default; set to a falsy value
                         (0/false/no/off) to hard-disable tracing even when a
                         tracking URI is set.
    MLFLOW_TRACKING_URI  Where to log traces (e.g. http://localhost:5000 for the
                         docker-compose MLflow server). When unset or empty,
                         tracing is disabled and setup_tracing() is a no-op.
    MLFLOW_EXPERIMENT    Experiment name traces are grouped under. Optional;
                         defaults to "jarvis".

The app depends only on the lightweight ``mlflow-tracing`` client; the full
MLflow server (UI + backend store) runs as its own container.
"""

import logging
import os

logger = logging.getLogger(__name__)

DEFAULT_EXPERIMENT = "jarvis"


def tracing_enabled() -> bool:
    """Master on/off switch, controlled by TRACING_ENABLED. Enabled by default;
    a falsy value (0/false/no/off) hard-disables tracing regardless of the
    tracking URI. Mirrors ``agent_enabled()`` in lib/agents.py."""
    raw = os.getenv("TRACING_ENABLED")
    if raw is None or raw.strip() == "":
        return True
    return raw.strip().lower() not in ("0", "false", "no", "off")


def setup_tracing() -> bool:
    """Enable MLflow auto-tracing for the OpenAI Agents SDK.

    Call once at startup, after ``load_dotenv()``. Returns True when tracing was
    enabled, False when it was disabled (master switch off or no tracking URI) or
    failed to initialize.
    """
    if not tracing_enabled():
        logger.info("TRACING_ENABLED is falsy; agent tracing disabled")
        return False

    tracking_uri = os.getenv("MLFLOW_TRACKING_URI", "").strip()
    if not tracking_uri:
        logger.info("MLFLOW_TRACKING_URI unset; agent tracing disabled")
        return False

    experiment = os.getenv("MLFLOW_EXPERIMENT", "").strip() or DEFAULT_EXPERIMENT

    try:
        import mlflow

        mlflow.set_tracking_uri(tracking_uri)
        mlflow.set_experiment(experiment)
        # Registers the Agents SDK trace processor; captures agent/tool spans.
        mlflow.openai.autolog()
        logger.info(
            f"MLflow tracing enabled (experiment='{experiment}') -> {tracking_uri}"
        )
        return True
    except Exception:
        # Never let observability plumbing take down the assistant.
        logger.exception("Failed to enable MLflow tracing; continuing without it")
        return False
