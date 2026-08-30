"""Unit tests for conditional-watch storage/model (lib/domain/scheduler.py).

JSON backend only — no live database required."""
from datetime import timedelta

import pytest

from lib.domain.scheduler import (
    JobStore,
    build_watch,
    describe_schedule,
    now_tz,
    parse_dt,
    public_view,
)


def _watch(**kw):
    d = dict(
        message="MSG", source="finance.fx", params={"foreign_currency": "USD"},
        check_code="def check(data):\n    return data['rate'] < 3.9",
        poll_interval=60, channel="log", target=None, conversation_id="repl",
    )
    d.update(kw)
    return build_watch(**d)


def test_build_watch_has_watch_fields():
    j = _watch()
    assert j["kind"] == "watch"
    assert j["prompt"] == "MSG"            # delivery text reuses the prompt column
    assert j["source"] == "finance.fx"
    assert j["poll_interval"] == 60
    assert j["last_result"] is None        # tri-state: unknown at creation
    assert j["status"] == "active" and j["next_run_at"]


def test_build_watch_next_run_is_poll_interval_ahead():
    before = now_tz()
    j = _watch(poll_interval=120)
    delta = parse_dt(j["next_run_at"]) - before
    assert timedelta(seconds=110) <= delta <= timedelta(seconds=130)


@pytest.mark.parametrize("bad", [
    {"message": ""}, {"source": ""}, {"check_code": ""},
    {"poll_interval": 0}, {"poll_interval": -5}, {"channel": "nope"},
])
def test_build_watch_validation(bad):
    with pytest.raises(ValueError):
        _watch(**bad)


def test_watch_roundtrips_through_json_store(tmp_path):
    s = JobStore(path=tmp_path / "jobs.json")
    j = _watch()
    s.add(j)
    got = s.get(j["id"])
    assert got["source"] == "finance.fx"
    assert got["check_code"].startswith("def check")
    assert got["params"] == {"foreign_currency": "USD"}
    assert got["poll_interval"] == 60 and got["last_result"] is None


def test_watch_is_due_and_active(tmp_path):
    s = JobStore(path=tmp_path / "jobs.json")
    j = _watch(poll_interval=1)
    s.add(j)
    later = now_tz() + timedelta(seconds=5)
    assert any(x["id"] == j["id"] for x in s.due(later))
    assert any(x["id"] == j["id"] for x in s.active())


def test_public_view_hides_predicate_and_params():
    pv = public_view(_watch())
    assert "check_code" not in pv and "params" not in pv
    assert "watch on 'finance.fx'" in pv["schedule"]


def test_describe_schedule_for_watch():
    assert describe_schedule(_watch(poll_interval=30)) == "watch on 'finance.fx' every 30s"
