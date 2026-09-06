"""Tests for cancelling scheduled items — especially conditional watches — through the
scheduler_agent tools (no network, no model, no DB: a JSON store on tmp is injected)."""
import asyncio

from lib.core import Ctx, bind_app_context, reset_app_context
from lib.domain.scheduler import JobStore, build_job, build_watch, public_view
from lib.agents.scheduler_agent import tools as st


def _watch(conv):
    return build_watch(
        message="USD spadł poniżej 3.90", source="finance.fx", params={"pair": "USD"},
        check_code="def check(data):\n    return True", poll_interval=60,
        channel="messenger", target="psid", conversation_id=conv,
    )


def _run(coro):
    return asyncio.run(coro)


def _with_ctx(tmp_path, monkeypatch, conv="messenger:psid"):
    monkeypatch.setattr(st, "scheduler_store", JobStore(path=tmp_path / "jobs.json"))
    ctx = Ctx()
    ctx.conversation_id = conv
    return bind_app_context(ctx)


def test_public_view_exposes_kind():
    assert public_view(_watch("messenger:psid"))["kind"] == "watch"
    assert public_view(build_job("b", "log", None, "repl", cron_expr="0 8 * * *"))["kind"] == "cron"


def test_list_includes_watches_with_kind(tmp_path, monkeypatch):
    tok = _with_ctx(tmp_path, monkeypatch)
    try:
        st.scheduler_store.add(_watch("messenger:psid"))
        listed = _run(st.list_scheduled_jobs.async_fn(None))
        assert listed["count"] == 1
        assert listed["jobs"][0]["kind"] == "watch"
    finally:
        reset_app_context(tok)


def test_delete_watch_by_id(tmp_path, monkeypatch):
    tok = _with_ctx(tmp_path, monkeypatch)
    try:
        w = st.scheduler_store.add(_watch("messenger:psid"))
        res = _run(st.delete_scheduled_job.async_fn(None, w["id"]))
        assert res.get("deleted") == w["id"]
        assert res["remaining"] == 0
        assert st.scheduler_store.get(w["id"]) is None
    finally:
        reset_app_context(tok)


def test_delete_by_unique_prefix(tmp_path, monkeypatch):
    tok = _with_ctx(tmp_path, monkeypatch)
    try:
        w = st.scheduler_store.add(_watch("messenger:psid"))
        res = _run(st.delete_scheduled_job.async_fn(None, w["id"][:8]))  # prefix
        assert res.get("deleted") == w["id"]
    finally:
        reset_app_context(tok)


def test_delete_miss_returns_active_list_not_success(tmp_path, monkeypatch):
    tok = _with_ctx(tmp_path, monkeypatch)
    try:
        w = st.scheduler_store.add(_watch("messenger:psid"))
        res = _run(st.delete_scheduled_job.async_fn(None, "job_doesnotexist"))
        assert "deleted" not in res
        assert "Error" in res
        assert [j["id"] for j in res["active_jobs"]] == [w["id"]]
        assert st.scheduler_store.get(w["id"]) is not None  # nothing was removed
    finally:
        reset_app_context(tok)


def test_delete_only_targets_current_conversation(tmp_path, monkeypatch):
    # A watch in another conversation must not be silently matched/deleted from here.
    tok = _with_ctx(tmp_path, monkeypatch, conv="messenger:mine")
    try:
        other = st.scheduler_store.add(_watch("messenger:someone_else"))
        res = _run(st.delete_scheduled_job.async_fn(None, other["id"]))
        assert "Error" in res and "deleted" not in res
        assert st.scheduler_store.get(other["id"]) is not None
    finally:
        reset_app_context(tok)
