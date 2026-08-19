"""Tests for the SmartDevice bounded-retry policy, using a fake bulb (no hardware).

These lock in the owner's requirement: never hang, never silently drop — a failing
device yields a structured error after bounded retries, and a transient failure
recovers on a later attempt.
"""
import asyncio
import threading

import pytest

import lib.smart_device as sd
from lib.smart_device import SmartDevice, RGB


class FakeBulb:
    """Returns/raises a scripted sequence of results for status()/turn_on()/etc."""

    def __init__(self, script):
        self._script = list(script)
        self.calls = 0

    def _next(self):
        item = self._script[min(self.calls, len(self._script) - 1)]
        self.calls += 1
        if isinstance(item, Exception):
            raise item
        return item

    def status(self):
        return self._next()

    def turn_on(self):
        return self._next()

    def turn_off(self):
        return self._next()

    def set_colour(self, r, g, b):
        return self._next()


class FakeConn:
    def __init__(self, bulb):
        self.bulb = bulb
        self.lock = threading.Lock()
        self.last_used = 0.0


class FakeManager:
    """Stand-in for lib.tuya_link.manager: always returns the same fake conn."""

    def __init__(self, bulb):
        self.conn = FakeConn(bulb)
        self.invalidated = 0

    def get(self, dev_id, ip, local_key, version):
        return self.conn

    def invalidate(self, dev_id):
        self.invalidated += 1

    def rediscover_ip(self, dev_id, timeout=8):
        return None


def _device():
    return SmartDevice(name="Lamp", dev_id="id1", ip="10.0.0.9",
                       local_key="k", room="x", zones=["z"])


def _patch(monkeypatch, bulb):
    fm = FakeManager(bulb)
    monkeypatch.setattr(sd, "manager", fm)
    return fm


def test_status_success_is_translated(monkeypatch):
    _patch(monkeypatch, FakeBulb([{"dps": {"20": True, "21": "white", "22": 50}}]))
    out = asyncio.run(_device().get_status())
    ds = out["device_state"]
    assert ds["is_on"] is True
    assert ds["mode"] == "white"
    assert ds["brightness"] == 50
    assert "Error" not in ds


def test_persistent_failure_returns_structured_error_not_exception(monkeypatch):
    fm = _patch(monkeypatch, FakeBulb([RuntimeError("boom")]))
    out = asyncio.run(_device().get_status())
    ds = out["device_state"]
    assert "Error" in ds                      # never a silent success
    assert fm.invalidated >= sd.MAX_ATTEMPTS   # each failed attempt dropped the conn


def test_transient_failure_recovers_on_retry(monkeypatch):
    # first attempt raises, second returns a good status -> command lands
    _patch(monkeypatch, FakeBulb([TimeoutError("cold"), {"dps": {"20": False, "21": "colour"}}]))
    out = asyncio.run(_device().get_status())
    assert out["device_state"]["is_on"] is False
    assert out["device_state"]["mode"] == "colour"


def test_device_error_dict_is_treated_as_failure(monkeypatch):
    _patch(monkeypatch, FakeBulb([{"Error": "901"}]))
    out = asyncio.run(_device().get_status())
    assert "Error" in out["device_state"]


def test_change_color_requires_colour_mode(monkeypatch):
    # status reports 'white' mode -> colour change must be refused, not attempted
    _patch(monkeypatch, FakeBulb([{"dps": {"20": True, "21": "white"}}]))
    out = asyncio.run(_device().change_color(RGB(R=255, G=0, B=0)))
    assert "Failed" in out
