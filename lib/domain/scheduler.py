"""Scheduled-jobs store + schedule maths for the ``scheduler_agent``.

A job is a **natural-language task for Jarvis** (``prompt``) plus *when* to run it
and *where* to deliver the reply. When it fires, the scheduler runner feeds the
prompt back into the same ``lib.engine.handle_message`` the REPL/Messenger use — a
system-originated turn — and pushes the reply to the job's channel. See
``lib/scheduler_runner.py`` and ``docs/SCHEDULER.md``.

Storage mirrors ``lib/memory.py``: an on-disk JSON store by default, swapped for a
Postgres backend (``app/db/scheduler_repo.py``) when ``DATABASE_URL`` is set. This
module owns storage/CRUD and the schedule computation; the agent-facing tools live
in ``lib/tools.py``.

Times are stored as timezone-aware ISO-8601 strings in the scheduler timezone
(``SCHEDULER_TIMEZONE``, default Europe/Warsaw), so both backends round-trip the
same shape.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from croniter import croniter

logger = logging.getLogger(__name__)

JOBS_PATH = Path("data/scheduler_data/jobs.json")
DEFAULT_TIMEZONE = "Europe/Warsaw"

VALID_KINDS = ("once", "cron")
VALID_STATUSES = ("active", "done", "disabled")
VALID_CHANNELS = ("messenger", "log")


# -- time helpers -------------------------------------------------------------

def tz() -> ZoneInfo:
    """The scheduler's timezone (env SCHEDULER_TIMEZONE, default Europe/Warsaw)."""
    return ZoneInfo(os.getenv("SCHEDULER_TIMEZONE") or DEFAULT_TIMEZONE)


def now_tz() -> datetime:
    return datetime.now(tz())


def parse_dt(value: str, end_of_day: bool = False) -> datetime:
    """Parse an ISO date/datetime string into a tz-aware datetime.

    A bare date ('2026-09-22') is anchored to the scheduler tz — to start of day,
    or end of day (23:59:59) when ``end_of_day`` (used for the inclusive ``until``
    boundary so a job scheduled for that day still fires).
    """
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        if end_of_day and len(value) <= 10:  # date-only -> inclusive end of that day
            dt = dt.replace(hour=23, minute=59, second=59)
        dt = dt.replace(tzinfo=tz())
    return dt


# -- schedule computation -----------------------------------------------------

def _first_run(kind: str, cron_expr: Optional[str], run_at: Optional[str],
               delay_minutes: Optional[int], base: datetime) -> datetime:
    if kind == "cron":
        return croniter(cron_expr, base).get_next(datetime)
    if delay_minutes is not None:
        return base + timedelta(minutes=int(delay_minutes))
    return parse_dt(run_at)


def next_after(job: dict, base: datetime) -> Optional[datetime]:
    """The next fire time strictly after ``base``, or None for a spent one-shot.

    For cron jobs this is computed from ``base`` (i.e. *now*), not from the previous
    scheduled slot — so occurrences missed while the service was down are skipped
    rather than replayed in a burst (see docs/SCHEDULER.md, restart-safety)."""
    if job.get("kind") == "cron":
        return croniter(job["cron_expr"], base).get_next(datetime)
    return None  # one-shot: nothing after it fires


def is_expired(job: dict, candidate: Optional[datetime]) -> bool:
    """Whether the job should stop, given its next candidate run and limits."""
    if candidate is None:
        return True
    until = job.get("until")
    if until and candidate > parse_dt(until, end_of_day=True):
        return True
    max_runs = job.get("max_runs")
    if max_runs is not None and job.get("run_count", 0) >= int(max_runs):
        return True
    return False


def describe_schedule(job: dict) -> str:
    """Human-readable one-liner for a job's schedule (internal / for the composer)."""
    if job.get("kind") == "cron":
        base = f"cron '{job['cron_expr']}' ({os.getenv('SCHEDULER_TIMEZONE') or DEFAULT_TIMEZONE})"
        extras = []
        if job.get("until"):
            extras.append(f"until {job['until']}")
        if job.get("max_runs") is not None:
            extras.append(f"max {job['max_runs']} runs")
        return base + (", " + ", ".join(extras) if extras else "")
    return f"one-shot at {job.get('run_at') or job.get('next_run_at')}"


def build_job(prompt: str, channel: str, target: Optional[str], conversation_id: str,
              cron_expr: Optional[str] = None, run_at: Optional[str] = None,
              delay_minutes: Optional[int] = None, until: Optional[str] = None,
              max_runs: Optional[int] = None) -> dict:
    """Validate inputs and assemble a job dict with its first ``next_run_at`` computed.

    Exactly one of ``cron_expr`` / ``run_at`` / ``delay_minutes`` must be given; it
    decides the kind (cron vs one-shot). Raises ``ValueError`` on invalid input."""
    prompt = (prompt or "").strip()
    if not prompt:
        raise ValueError("prompt cannot be empty")
    if channel not in VALID_CHANNELS:
        raise ValueError(f"unknown channel '{channel}'")

    given = [k for k, v in
             (("cron_expr", cron_expr), ("run_at", run_at), ("delay_minutes", delay_minutes))
             if v is not None]
    if len(given) != 1:
        raise ValueError(
            "provide exactly one of cron_expr, run_at, delay_minutes "
            f"(got: {given or 'none'})"
        )

    kind = "cron" if cron_expr is not None else "once"
    if kind == "cron" and not croniter.is_valid(cron_expr):
        raise ValueError(f"invalid cron expression: '{cron_expr}'")
    if delay_minutes is not None and int(delay_minutes) < 0:
        raise ValueError("delay_minutes cannot be negative")
    if until is not None:
        parse_dt(until, end_of_day=True)  # validate parseability early

    base = now_tz()
    first = _first_run(kind, cron_expr, run_at, delay_minutes, base)

    return {
        "id": "job_" + uuid.uuid4().hex[:8],
        "prompt": prompt,
        "channel": channel,
        "target": target,
        "conversation_id": conversation_id,
        "kind": kind,
        "cron_expr": cron_expr,
        "run_at": first.isoformat() if kind == "once" else None,
        "next_run_at": first.isoformat(),
        "until": until,
        "max_runs": int(max_runs) if max_runs is not None else None,
        "run_count": 0,
        "last_run_at": None,
        "status": "active",
        "created_at": base.isoformat(timespec="seconds"),
    }


def public_view(job: dict) -> dict:
    """Model/user-facing projection of a job — never exposes the raw target (PSID)."""
    return {
        "id": job["id"],
        "prompt": job["prompt"],
        "schedule": describe_schedule(job),
        "next_run": job.get("next_run_at"),
        "channel": job.get("channel"),
        "status": job.get("status"),
    }


# -- JSON store ---------------------------------------------------------------

class JobStore:
    """On-disk JSON store; atomic writes guarded by a lock (like ``MemoryStore``)."""

    def __init__(self, path: Path = JOBS_PATH):
        self.path = Path(path)
        self._lock = threading.Lock()

    def _load(self) -> dict:
        if not self.path.exists():
            return {"jobs": []}
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.error(f"scheduler: could not read {self.path}: {e}")
            return {"jobs": []}
        if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
            logger.error(f"scheduler: unexpected shape in {self.path}; ignoring")
            return {"jobs": []}
        return data

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        tmp.replace(self.path)  # atomic swap

    # -- reads ----------------------------------------------------------------
    def all(self) -> list[dict]:
        return self._load()["jobs"]

    def active(self) -> list[dict]:
        return [j for j in self.all() if j.get("status") == "active"]

    def for_conversation(self, conversation_id: str) -> list[dict]:
        return [j for j in self.active() if j.get("conversation_id") == conversation_id]

    def due(self, now: datetime) -> list[dict]:
        out = []
        for j in self.active():
            nxt = j.get("next_run_at")
            if nxt and parse_dt(nxt) <= now:
                out.append(j)
        return out

    def get(self, job_id: str) -> Optional[dict]:
        return next((j for j in self.all() if j.get("id") == job_id), None)

    # -- writes ---------------------------------------------------------------
    def add(self, job: dict) -> dict:
        with self._lock:
            data = self._load()
            data["jobs"].append(job)
            self._save(data)
        return job

    def save(self, job: dict) -> dict:
        """Replace an existing job (by id) wholesale — used after a run advances it."""
        with self._lock:
            data = self._load()
            data["jobs"] = [job if j.get("id") == job["id"] else j for j in data["jobs"]]
            self._save(data)
        return job

    def update(self, job_id: str, **fields) -> Optional[dict]:
        with self._lock:
            data = self._load()
            for j in data["jobs"]:
                if j.get("id") == job_id:
                    j.update({k: v for k, v in fields.items() if v is not None})
                    self._save(data)
                    return j
        return None

    def delete(self, job_id: str) -> bool:
        with self._lock:
            data = self._load()
            remaining = [j for j in data["jobs"] if j.get("id") != job_id]
            if len(remaining) != len(data["jobs"]):
                data["jobs"] = remaining
                self._save(data)
                return True
        return False


def _select_backend():
    """Postgres when DATABASE_URL is set (checked after .env load), else JSON file."""
    try:
        from app.db import connection as dbconn
        if dbconn.is_configured():
            from app.db.schema import init_db
            from app.db.scheduler_repo import PostgresJobStore
            init_db()
            logger.info("Scheduler store backend: Postgres")
            return PostgresJobStore()
    except Exception as e:  # noqa: BLE001 - never let storage selection crash the app
        logger.error(f"Postgres scheduler backend unavailable ({e}); falling back to JSON file")
    logger.info("Scheduler store backend: JSON file")
    return JobStore()


class _JobStoreProxy:
    """Delegates to the backend chosen lazily on first use (so .env is respected)."""

    def __init__(self):
        self._impl = None

    def _store(self):
        if self._impl is None:
            self._impl = _select_backend()
        return self._impl

    def all(self) -> list[dict]:
        return self._store().all()

    def active(self) -> list[dict]:
        return self._store().active()

    def for_conversation(self, conversation_id: str) -> list[dict]:
        return self._store().for_conversation(conversation_id)

    def due(self, now: datetime) -> list[dict]:
        return self._store().due(now)

    def get(self, job_id: str) -> Optional[dict]:
        return self._store().get(job_id)

    def add(self, job: dict) -> dict:
        return self._store().add(job)

    def save(self, job: dict) -> dict:
        return self._store().save(job)

    def update(self, job_id: str, **fields):
        return self._store().update(job_id, **fields)

    def delete(self, job_id: str) -> bool:
        return self._store().delete(job_id)


# module-wide singleton (backend chosen on first use)
store = _JobStoreProxy()
