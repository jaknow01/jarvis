"""Shared fixtures: a hermetic model profile and no named model servers, so tests never
depend on config/models/, config/providers.yaml or the developer's MODEL_PROFILE."""
import pytest

from lib.core.model_profiles import AgentModel, ModelProfile, using_profile

TEST_PROFILE = ModelProfile(name="test", default=AgentModel(model="openai/gpt-4.1-mini"))


@pytest.fixture(autouse=True)
def _test_model_profile(monkeypatch, tmp_path):
    monkeypatch.setenv("PROVIDERS_CONFIG", str(tmp_path / "no-providers.yaml"))
    with using_profile(TEST_PROFILE):
        yield TEST_PROFILE
