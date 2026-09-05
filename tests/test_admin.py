"""Tests for the admin / ops command surface (`lib/runtime/admin.py`).

Pure routing + formatting, plus a few handlers driven with fakes/monkeypatch so nothing
here needs network, a database, or API keys. Async handlers are run via ``asyncio.run``
(the project has no pytest-asyncio).
"""

import asyncio
import logging

import lib.agents  # noqa: F401  — populate the AGENTS registry for /agents, /models
from lib.runtime import admin, diag
import lib.runtime.scheduler_runner as scheduler_runner


def _run(cmd: str, conversation_id: str = "repl") -> str:
    return asyncio.run(admin.maybe_handle(cmd, conversation_id=conversation_id, channel="repl"))


# ---- routing ---------------------------------------------------------------------

def test_passthrough_for_non_command():
    assert asyncio.run(admin.maybe_handle("zwykła wiadomość", conversation_id="repl")) is None
    assert asyncio.run(admin.maybe_handle("", conversation_id="repl")) is None


def test_unknown_command():
    out = _run("/nope")
    assert "Nieznana komenda" in out
    assert "/help" in out


def test_help_lists_every_command():
    out = _run("/help")
    for name, cmd in admin._COMMANDS.items():
        if name == "help":
            continue
        assert cmd.usage in out, f"{cmd.usage} missing from /help"


def test_bare_slash_shows_help():
    assert "KOMENDY TECHNICZNE" in _run("/")


# ---- formatting helpers ----------------------------------------------------------

def test_fmt_delta():
    assert admin._fmt_delta(45) == "45s"
    assert admin._fmt_delta(125) == "2m 5s"
    assert admin._fmt_delta(3700) == "1h 1m"
    assert admin._fmt_delta(90000) == "1d 1h"


def test_mask_secrets_and_urls():
    assert admin._mask("OPENAI_API_KEY", "sk-abcdef1234") == "…1234"
    assert admin._mask("SHORT_TOKEN", "abc") == "****"
    assert admin._mask("DATABASE_URL", "postgresql://jarvis:secret@postgres:5432/db") == \
        "postgresql://jarvis:***@postgres:5432/db"
    assert admin._mask("DEFAULT_MODEL", "anthropic/claude") == "anthropic/claude"


def test_preview_truncates():
    assert admin._preview("a" * 100, limit=10) == "aaaaaaaaa…"
    assert admin._preview("short") == "short"


# ---- scheduler control -----------------------------------------------------------

def test_scheduler_pause_resume_roundtrip():
    try:
        assert "wstrzymany" in _run("/scheduler pause")
        assert scheduler_runner.is_paused() is True
        assert "⏸ wstrzymany" in _run("/status")
        assert "wznowiony" in _run("/scheduler resume").lower()
        assert scheduler_runner.is_paused() is False
    finally:
        scheduler_runner.resume()


# ---- loglevel --------------------------------------------------------------------

def test_loglevel_get_set_and_validate():
    original = logging.getLogger().level
    try:
        assert "Nieznany poziom" in _run("/loglevel bogus")
        assert "DEBUG" in _run("/loglevel debug")
        assert logging.getLogger().level == logging.DEBUG
        assert "Poziom logowania" in _run("/loglevel")
    finally:
        logging.getLogger().setLevel(original)


# ---- jobs (fake store) -----------------------------------------------------------

class _FakeStore:
    def __init__(self, jobs=None):
        self._jobs = jobs or []

    def active(self):
        return self._jobs

    def all(self):
        return self._jobs

    def get(self, job_id):
        return next((j for j in self._jobs if j["id"] == job_id), None)


def test_jobs_empty(monkeypatch):
    monkeypatch.setattr(admin, "store", _FakeStore([]))
    assert "Brak aktywnych zadań" in _run("/jobs")


def test_jobs_lists_timed_and_watch(monkeypatch):
    jobs = [
        {"id": "abc12345ff", "kind": "cron", "cron_expr": "0 7 * * *",
         "prompt": "daj mi poranny brief", "status": "active", "next_run_at": None},
        {"id": "watch999aa", "kind": "watch", "source": "fpl", "poll_interval": 3600,
         "prompt": "deadline!", "status": "active", "last_result": False, "next_run_at": None,
         "repeating": True},
    ]
    monkeypatch.setattr(admin, "store", _FakeStore(jobs))
    out = _run("/jobs")
    assert "abc12345" in out and "watch999" in out
    assert "poranny brief" in out
    # kind is explicit per item (cron vs watch, repeating vs one-shot)
    assert "🔁 cron" in out
    assert "👁 watch (powtarzalny)" in out


def test_jobs_all_shows_done_but_default_hides_it(monkeypatch):
    jobs = [
        {"id": "activewatch", "kind": "watch", "source": "fpl", "poll_interval": 60,
         "prompt": "aktywny", "status": "active", "last_result": None, "next_run_at": None,
         "repeating": False},
        {"id": "doneonce111", "kind": "once", "prompt": "zakonczone", "status": "done",
         "next_run_at": None},
    ]
    monkeypatch.setattr(admin, "store", _FakeStore(jobs))
    default = _run("/jobs")
    assert "activewatch"[:8] in default
    assert "doneonce"[:8] not in default          # done hidden by default
    all_out = _run("/jobs all")
    assert "doneonce"[:8] in all_out               # shown with 'all'
    assert "Zakończone" in all_out


# ---- reset (fake conversations) --------------------------------------------------

def test_reset_requires_confirm(monkeypatch):
    calls = []

    class _FakeConv:
        def reset(self, cid):
            calls.append(cid)
            return True

    monkeypatch.setattr(admin, "conversations", _FakeConv())
    assert "reset confirm" in _run("/reset")
    assert calls == []  # not reset without confirm
    assert "Wyczyszczono" in _run("/reset confirm", conversation_id="messenger:42")
    assert calls == ["messenger:42"]


# ---- agents / models (env-driven) ------------------------------------------------

def test_agents_reflects_enabled_flag(monkeypatch):
    monkeypatch.setenv("AGENT_NEWS_AGENT_ENABLED", "false")
    out = _run("/agents")
    assert "🔴 news_agent" in out
    assert "🟢 weather_agent" in out


def test_models_reports_resolved_config(monkeypatch):
    monkeypatch.setenv("DEFAULT_MODEL", "anthropic/claude-sonnet-5")
    out = _run("/models")
    assert "anthropic/claude-sonnet-5" in out


# ---- /stop -----------------------------------------------------------------------

def test_stop_when_idle():
    out = _run("/stop", conversation_id="repl:idle-xyz")
    assert "Nic teraz" in out


def test_stop_cancels_running_turn():
    async def scenario():
        cid = "messenger:stop-test"

        async def long_turn():
            await asyncio.sleep(30)

        task = asyncio.create_task(long_turn())
        await asyncio.sleep(0)  # let it start
        diag.register_running(cid, task)
        out = await admin.maybe_handle("/stop", conversation_id=cid, channel="messenger")
        try:
            await task
        except asyncio.CancelledError:
            pass
        diag.clear_running(cid, task)
        return out, task.cancelled()

    out, cancelled = asyncio.run(scenario())
    assert "Przerywam" in out
    assert cancelled is True
