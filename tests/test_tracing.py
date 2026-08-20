"""Tests for the MLflow tracing setup (network-free).

These exercise only the wiring/guard logic in lib.tracing. The live end-to-end
check that traces actually reach a running MLflow server is a smoke test
(smoke-tests/mlflow_trace.py), kept out of the pytest suite because it needs a
server and pulls a Docker image.
"""
from lib import tracing


def test_disabled_when_tracking_uri_unset(monkeypatch):
    monkeypatch.delenv("TRACING_ENABLED", raising=False)
    monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)
    assert tracing.setup_tracing() is False


def test_disabled_when_tracking_uri_blank(monkeypatch):
    monkeypatch.delenv("TRACING_ENABLED", raising=False)
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "   ")
    assert tracing.setup_tracing() is False


def test_default_experiment_name():
    assert tracing.DEFAULT_EXPERIMENT == "jarvis"


def test_tracing_enabled_default_when_unset(monkeypatch):
    monkeypatch.delenv("TRACING_ENABLED", raising=False)
    assert tracing.tracing_enabled() is True


def test_tracing_enabled_blank_is_enabled(monkeypatch):
    monkeypatch.setenv("TRACING_ENABLED", "   ")
    assert tracing.tracing_enabled() is True


def test_tracing_enabled_falsy_values(monkeypatch):
    for val in ("0", "false", "FALSE", "no", "off", " Off "):
        monkeypatch.setenv("TRACING_ENABLED", val)
        assert tracing.tracing_enabled() is False, val


def test_tracing_enabled_truthy_values(monkeypatch):
    for val in ("1", "true", "yes", "on"):
        monkeypatch.setenv("TRACING_ENABLED", val)
        assert tracing.tracing_enabled() is True, val


def test_master_switch_off_beats_set_uri(monkeypatch):
    monkeypatch.setenv("TRACING_ENABLED", "false")
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "http://localhost:5001")
    assert tracing.setup_tracing() is False
