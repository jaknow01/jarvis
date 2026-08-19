"""Tests for env-configurable connection tunables in lib.tuya_link."""
import lib.tuya_link as tl
from lib.tuya_link import get_idle_release, DEFAULT_IDLE_RELEASE, IDLE_RELEASE_ENV


def test_idle_release_defaults_when_unset(monkeypatch):
    monkeypatch.delenv(IDLE_RELEASE_ENV, raising=False)
    assert get_idle_release() == DEFAULT_IDLE_RELEASE


def test_idle_release_reads_env_seconds(monkeypatch):
    monkeypatch.setenv(IDLE_RELEASE_ENV, "45")
    assert get_idle_release() == 45.0


def test_idle_release_accepts_float(monkeypatch):
    monkeypatch.setenv(IDLE_RELEASE_ENV, "12.5")
    assert get_idle_release() == 12.5


def test_idle_release_falls_back_on_garbage(monkeypatch):
    monkeypatch.setenv(IDLE_RELEASE_ENV, "not-a-number")
    assert get_idle_release() == DEFAULT_IDLE_RELEASE


def test_idle_release_falls_back_on_nonpositive(monkeypatch):
    monkeypatch.setenv(IDLE_RELEASE_ENV, "0")
    assert get_idle_release() == DEFAULT_IDLE_RELEASE
    monkeypatch.setenv(IDLE_RELEASE_ENV, "-10")
    assert get_idle_release() == DEFAULT_IDLE_RELEASE


def test_idle_release_blank_is_default(monkeypatch):
    monkeypatch.setenv(IDLE_RELEASE_ENV, "   ")
    assert get_idle_release() == DEFAULT_IDLE_RELEASE
