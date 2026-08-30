"""Unit tests for the watch branch of the scheduler runner (lib/runtime/scheduler_runner.py).

Drives `_run_watch` with a fake store, scripted evaluator, registered fake sources, and a
fake deliver — no network, no model. Covers edge-trigger, three-state, termination, and the
delivery-failure retry path."""
import asyncio

from lib.domain.scheduler import build_watch, now_tz, parse_dt
from lib.runtime.scheduler_runner import _run_watch
from lib.agents.scheduler_agent.watch_sources import register_source


# fake sources: one returning clean data, one returning an error dict (-> unknown)
async def _ok_fetch(_p):
    return {"v": 1}


async def _err_fetch(_p):
    return {"Error": "boom"}


register_source("w.ok", _ok_fetch)
register_source("w.err", _err_fetch)


class FakeStore:
    def __init__(self):
        self.saves = 0

    def save(self, job):
        self.saves += 1
        return job


class ScriptedEvaluator:
    """Returns/raises scripted results per call, ignoring the actual code/data."""
    def __init__(self, results):
        self._results = list(results)
        self.calls = 0

    async def evaluate(self, code, data):
        r = self._results[self.calls]
        self.calls += 1
        if isinstance(r, Exception):
            raise r
        return r


def _watch(source="w.ok"):
    return build_watch(
        message="MSG", source=source, params={}, check_code="def check(data): return True",
        poll_interval=10, channel="log", target=None, conversation_id="repl",
    )


def _delivered_box():
    box = []

    async def deliver(job, text):
        box.append(text)
    return box, deliver


def _run(job, ev, deliver, store=None):
    asyncio.run(_run_watch(store or FakeStore(), ev, deliver, job, now_tz()))


def test_fires_once_on_true_then_terminates():
    job = _watch("w.ok")
    box, deliver = _delivered_box()
    _run(job, ScriptedEvaluator([True]), deliver)
    assert box == ["MSG"]
    assert job["last_result"] is True
    assert job["status"] == "done" and job["next_run_at"] is None


def test_false_delivers_nothing_and_rearms():
    job = _watch("w.ok")
    box, deliver = _delivered_box()
    now = now_tz()
    asyncio.run(_run_watch(FakeStore(), ScriptedEvaluator([False]), deliver, job, now))
    assert box == []
    assert job["last_result"] is False and job["status"] == "active"
    assert parse_dt(job["next_run_at"]) > now


def test_unknown_fetch_skips_without_evaluating():
    job = _watch("w.err")             # error dict -> unknown before evaluation
    box, deliver = _delivered_box()
    ev = ScriptedEvaluator([])        # must never be called
    _run(job, ev, deliver)
    assert box == [] and ev.calls == 0
    assert job["last_result"] is None and job["status"] == "active"


def test_evaluator_exception_is_unknown():
    job = _watch("w.ok")
    box, deliver = _delivered_box()
    _run(job, ScriptedEvaluator([RuntimeError("boom")]), deliver)
    assert box == []
    assert job["last_result"] is None and job["status"] == "active"


def test_sequence_false_false_true_fires_once():
    job = _watch("w.ok")
    box, deliver = _delivered_box()
    ev = ScriptedEvaluator([False, False, True])
    store = FakeStore()
    for _ in range(3):
        if job["status"] != "active":
            break
        asyncio.run(_run_watch(store, ev, deliver, job, now_tz()))
    assert box == ["MSG"] and job["status"] == "done"


def test_delivery_failure_does_not_consume_edge():
    job = _watch("w.ok")
    delivered = []
    calls = {"n": 0}

    async def flaky_deliver(j, text):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("network")
        delivered.append(text)

    ev = ScriptedEvaluator([True, True])
    # first poll: delivery raises -> edge NOT consumed, still active, last_result not True
    asyncio.run(_run_watch(FakeStore(), ev, flaky_deliver, job, now_tz()))
    assert delivered == [] and job["status"] == "active" and job["last_result"] is not True
    # second poll: delivery succeeds -> fires and completes
    asyncio.run(_run_watch(FakeStore(), ev, flaky_deliver, job, now_tz()))
    assert delivered == ["MSG"] and job["status"] == "done"
