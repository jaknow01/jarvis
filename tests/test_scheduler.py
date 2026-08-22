"""Tests for the scheduler: schedule maths, the JSON store, and the runner tick.

No network, no model, no database — the runner's invoke/deliver are injected spies.
"""
import asyncio
from datetime import timedelta

import pytest

from lib import scheduler
from lib.scheduler import (
    JobStore,
    build_job,
    is_expired,
    next_after,
    now_tz,
    parse_dt,
)
from lib import scheduler_runner
from lib.tools import TOOLS_BY_AGENT


# -- build_job / validation ---------------------------------------------------

def test_build_job_delay_is_one_off():
    job = build_job("kawa", "log", None, "repl", delay_minutes=120)
    assert job["kind"] == "once"
    assert job["cron_expr"] is None
    delta = parse_dt(job["next_run_at"]) - now_tz()
    assert timedelta(minutes=119) < delta <= timedelta(minutes=120, seconds=5)


def test_build_job_cron_is_recurring():
    job = build_job("brief", "log", None, "repl", cron_expr="0 8 * * *")
    assert job["kind"] == "cron"
    assert parse_dt(job["next_run_at"]) > now_tz()


def test_build_job_requires_exactly_one_trigger():
    with pytest.raises(ValueError):
        build_job("x", "log", None, "repl")  # none
    with pytest.raises(ValueError):
        build_job("x", "log", None, "repl", delay_minutes=5, cron_expr="0 8 * * *")  # two


def test_build_job_rejects_bad_cron_and_channel():
    with pytest.raises(ValueError):
        build_job("x", "log", None, "repl", cron_expr="not a cron")
    with pytest.raises(ValueError):
        build_job("x", "carrier-pigeon", None, "repl", delay_minutes=1)


def test_build_job_empty_prompt_rejected():
    with pytest.raises(ValueError):
        build_job("   ", "log", None, "repl", delay_minutes=1)


# -- next_after / is_expired --------------------------------------------------

def test_next_after_cron_is_in_future_and_none_for_once():
    base = now_tz()
    cron = build_job("b", "log", None, "repl", cron_expr="* * * * *")
    assert next_after(cron, base) > base
    once = build_job("r", "log", None, "repl", delay_minutes=10)
    assert next_after(once, base) is None


def test_is_expired_on_until_and_max_runs():
    base = now_tz()
    job = build_job("b", "log", None, "repl", cron_expr="* * * * *",
                    until=(base - timedelta(days=1)).date().isoformat())
    # next candidate is in the future, but the until boundary is already past
    assert is_expired(job, next_after(job, base)) is True

    capped = build_job("b", "log", None, "repl", cron_expr="* * * * *", max_runs=2)
    capped["run_count"] = 2
    assert is_expired(capped, next_after(capped, base)) is True

    fresh = build_job("b", "log", None, "repl", cron_expr="* * * * *")
    assert is_expired(fresh, next_after(fresh, base)) is False

    # a spent one-off (no next candidate) is always expired
    assert is_expired(build_job("r", "log", None, "repl", delay_minutes=1), None) is True


# -- JSON store ---------------------------------------------------------------

def _store(tmp_path):
    return JobStore(path=tmp_path / "jobs.json")


def test_store_crud_and_filters(tmp_path):
    st = _store(tmp_path)
    j1 = st.add(build_job("a", "log", None, "repl", delay_minutes=5))
    j2 = st.add(build_job("b", "messenger", "psid1", "messenger:psid1", cron_expr="0 8 * * *"))

    assert {j["id"] for j in st.all()} == {j1["id"], j2["id"]}
    assert {j["id"] for j in st.active()} == {j1["id"], j2["id"]}
    assert [j["id"] for j in st.for_conversation("messenger:psid1")] == [j2["id"]]
    assert st.get(j1["id"])["prompt"] == "a"

    st.update(j1["id"], prompt="a2")
    assert st.get(j1["id"])["prompt"] == "a2"

    assert st.delete(j1["id"]) is True
    assert st.get(j1["id"]) is None
    assert st.delete("job_nope") is False


def test_store_due_respects_next_run_at(tmp_path):
    st = _store(tmp_path)
    soon = st.add(build_job("soon", "log", None, "repl", delay_minutes=0))
    later = st.add(build_job("later", "log", None, "repl", delay_minutes=60))
    due = st.due(now_tz() + timedelta(seconds=5))
    ids = {j["id"] for j in due}
    assert soon["id"] in ids
    assert later["id"] not in ids


# -- runner tick --------------------------------------------------------------

def _spies():
    calls = {"invoke": [], "deliver": []}

    async def invoke(job):
        calls["invoke"].append(job["id"])
        return f"reply for {job['id']}"

    async def deliver(job, reply):
        calls["deliver"].append((job["id"], reply))

    return calls, invoke, deliver


def test_tick_fires_due_once_job_and_marks_done(tmp_path):
    st = _store(tmp_path)
    job = st.add(build_job("remind", "log", None, "repl", delay_minutes=0))
    calls, invoke, deliver = _spies()

    fired = asyncio.run(scheduler_runner.tick_once(st, invoke, deliver,
                                                   now=now_tz() + timedelta(seconds=1)))
    assert fired == 1
    assert calls["invoke"] == [job["id"]]
    assert calls["deliver"] == [(job["id"], f"reply for {job['id']}")]

    stored = st.get(job["id"])
    assert stored["status"] == "done"
    assert stored["next_run_at"] is None
    assert stored["run_count"] == 1


def test_tick_rearms_recurring_job(tmp_path):
    st = _store(tmp_path)
    job = build_job("brief", "log", None, "repl", cron_expr="* * * * *")
    # force it due by backdating next_run_at
    job["next_run_at"] = (now_tz() - timedelta(minutes=1)).isoformat()
    st.add(job)
    calls, invoke, deliver = _spies()

    now = now_tz()
    fired = asyncio.run(scheduler_runner.tick_once(st, invoke, deliver, now=now))
    assert fired == 1

    stored = st.get(job["id"])
    assert stored["status"] == "active"
    assert parse_dt(stored["next_run_at"]) > now
    assert stored["run_count"] == 1


def test_tick_recurring_stops_at_max_runs(tmp_path):
    st = _store(tmp_path)
    job = build_job("brief", "log", None, "repl", cron_expr="* * * * *", max_runs=1)
    job["next_run_at"] = (now_tz() - timedelta(minutes=1)).isoformat()
    st.add(job)
    _, invoke, deliver = _spies()

    asyncio.run(scheduler_runner.tick_once(st, invoke, deliver, now=now_tz()))
    stored = st.get(job["id"])
    assert stored["status"] == "done"
    assert stored["run_count"] == 1


# -- registry -----------------------------------------------------------------

def test_scheduler_agent_tools_registered():
    names = {getattr(t, "name", "") for t in TOOLS_BY_AGENT["scheduler_agent"]}
    assert {
        "create_scheduled_job",
        "list_scheduled_jobs",
        "delete_scheduled_job",
        "update_scheduled_job",
    } <= names
