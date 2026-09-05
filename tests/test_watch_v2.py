"""Tests for watch v2: change detection via the injected `_previous` reading, and repeating
watches that stay armed and notify on every edge. Uses the real StubEvaluator (so the
predicate actually runs) and a source whose value we mutate between polls."""
import asyncio

from lib.domain.scheduler import build_watch, now_tz
from lib.runtime.scheduler_runner import _run_watch
from lib.agents.scheduler_agent.watch_evaluator import StubEvaluator
from lib.agents.scheduler_agent.watch_sources import register_source


# A source whose "points" value we bump between polls to simulate live data changing.
_STATE = {"points": 10}


async def _points_fetch(_params):
    return {"points": _STATE["points"]}


register_source("test.points", _points_fetch)

# Fire when points increased versus the previous poll (guarding the first-poll None baseline).
_CHANGE_CODE = (
    "def check(data):\n"
    "    prev = get(data, \"_previous\")\n"
    "    if prev == None:\n"
    "        return False\n"
    "    return get(data, \"points\", 0) > get(prev, \"points\", 0)\n"
)


class _Store:
    def save(self, job):
        return job


def _box():
    b = []

    async def deliver(job, text):
        b.append(text)
    return b, deliver


def _watch(repeat):
    return build_watch(
        message="NEW POINTS", source="test.points", params={}, check_code=_CHANGE_CODE,
        poll_interval=10, channel="log", target=None, conversation_id="repl", repeat=repeat,
    )


def _poll(job, deliver, points):
    _STATE["points"] = points
    asyncio.run(_run_watch(_Store(), StubEvaluator(), deliver, job, now_tz()))


def test_build_watch_v2_fields():
    j = _watch(repeat=True)
    assert j["repeating"] is True
    assert j["last_data"] is None
    assert _watch(repeat=False)["repeating"] is False


def test_first_poll_has_no_baseline_and_does_not_fire():
    job = _watch(repeat=True)
    box, deliver = _box()
    _poll(job, deliver, 10)
    assert box == []                      # no _previous yet -> predicate False
    assert job["last_data"] == {"points": 10}  # baseline remembered (raw, no _previous key)
    assert "_previous" not in job["last_data"]


def test_change_detection_fires_on_increase():
    job = _watch(repeat=True)
    box, deliver = _box()
    _poll(job, deliver, 10)   # baseline
    _poll(job, deliver, 10)   # unchanged -> no fire
    assert box == []
    _poll(job, deliver, 15)   # increased -> fire
    assert box == ["NEW POINTS"]
    assert job["last_data"] == {"points": 15}


def test_repeating_watch_stays_armed_and_fires_again():
    job = _watch(repeat=True)
    box, deliver = _box()
    _poll(job, deliver, 10)   # baseline
    _poll(job, deliver, 15)   # fire #1
    assert job["status"] == "active" and job["next_run_at"] is not None
    _poll(job, deliver, 15)   # unchanged -> resets edge
    _poll(job, deliver, 20)   # fire #2
    assert box == ["NEW POINTS", "NEW POINTS"]
    assert job["status"] == "active"


def test_one_shot_change_watch_completes_after_first_fire():
    job = _watch(repeat=False)
    box, deliver = _box()
    _poll(job, deliver, 10)   # baseline
    _poll(job, deliver, 15)   # fire once
    assert box == ["NEW POINTS"]
    assert job["status"] == "done" and job["next_run_at"] is None
