"""Postgres-backed scheduled-jobs store.

Mirrors the interface of the JSON ``JobStore`` in ``lib/scheduler.py`` (all / active /
for_conversation / due / get / add / save / update / delete), so it is a drop-in
backend selected by ``lib.scheduler`` when ``DATABASE_URL`` is set.

Timestamp columns (run_at/next_run_at/last_run_at/created_at) are TIMESTAMPTZ; they are
returned to callers as ISO strings so the dict shape matches the JSON store exactly.
``until`` stays TEXT to preserve a date-only inclusive boundary as the user gave it.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from app.db.connection import connection
from lib.scheduler import parse_dt

logger = logging.getLogger(__name__)

_COLS = (
    "id, prompt, channel, target, conversation_id, kind, cron_expr, run_at, "
    "next_run_at, until_spec, max_runs, run_count, last_run_at, status, created_at"
)

# Time fields stored as TIMESTAMPTZ (converted to/from ISO strings at the boundary).
_TS_FIELDS = ("run_at", "next_run_at", "last_run_at", "created_at")


def _iso(v):
    return v.isoformat() if hasattr(v, "isoformat") else v


def _row(r) -> dict:
    return {
        "id": r[0],
        "prompt": r[1],
        "channel": r[2],
        "target": r[3],
        "conversation_id": r[4],
        "kind": r[5],
        "cron_expr": r[6],
        "run_at": _iso(r[7]),
        "next_run_at": _iso(r[8]),
        "until": r[9],
        "max_runs": r[10],
        "run_count": r[11],
        "last_run_at": _iso(r[12]),
        "status": r[13],
        "created_at": _iso(r[14]),
    }


def _to_ts(value):
    """ISO string -> aware datetime for a TIMESTAMPTZ column (None passes through)."""
    return parse_dt(value) if value else None


def _params(job: dict) -> tuple:
    return (
        job["id"], job["prompt"], job["channel"], job.get("target"),
        job["conversation_id"], job["kind"], job.get("cron_expr"),
        _to_ts(job.get("run_at")), _to_ts(job.get("next_run_at")), job.get("until"),
        job.get("max_runs"), job.get("run_count", 0), _to_ts(job.get("last_run_at")),
        job.get("status", "active"), _to_ts(job.get("created_at")),
    )


class PostgresJobStore:
    # -- reads ----------------------------------------------------------------
    def all(self) -> list[dict]:
        with connection() as conn:
            rows = conn.execute(
                f"SELECT {_COLS} FROM scheduled_jobs ORDER BY created_at, id"
            ).fetchall()
        return [_row(r) for r in rows]

    def active(self) -> list[dict]:
        with connection() as conn:
            rows = conn.execute(
                f"SELECT {_COLS} FROM scheduled_jobs WHERE status = 'active' "
                "ORDER BY next_run_at, id"
            ).fetchall()
        return [_row(r) for r in rows]

    def for_conversation(self, conversation_id: str) -> list[dict]:
        with connection() as conn:
            rows = conn.execute(
                f"SELECT {_COLS} FROM scheduled_jobs "
                "WHERE status = 'active' AND conversation_id = %s ORDER BY next_run_at, id",
                (conversation_id,),
            ).fetchall()
        return [_row(r) for r in rows]

    def due(self, now: datetime) -> list[dict]:
        with connection() as conn:
            rows = conn.execute(
                f"SELECT {_COLS} FROM scheduled_jobs "
                "WHERE status = 'active' AND next_run_at IS NOT NULL AND next_run_at <= %s "
                "ORDER BY next_run_at, id",
                (now,),
            ).fetchall()
        return [_row(r) for r in rows]

    def get(self, job_id: str) -> Optional[dict]:
        with connection() as conn:
            row = conn.execute(
                f"SELECT {_COLS} FROM scheduled_jobs WHERE id = %s", (job_id,)
            ).fetchone()
        return _row(row) if row else None

    # -- writes ---------------------------------------------------------------
    def add(self, job: dict) -> dict:
        with connection() as conn:
            conn.execute(
                f"INSERT INTO scheduled_jobs ({_COLS}) VALUES "
                "(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                _params(job),
            )
        return job

    def save(self, job: dict) -> dict:
        """Upsert a whole job (used after a run advances next_run_at/status)."""
        with connection() as conn:
            conn.execute(
                f"INSERT INTO scheduled_jobs ({_COLS}) VALUES "
                "(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (id) DO UPDATE SET "
                "prompt = EXCLUDED.prompt, channel = EXCLUDED.channel, "
                "target = EXCLUDED.target, conversation_id = EXCLUDED.conversation_id, "
                "kind = EXCLUDED.kind, cron_expr = EXCLUDED.cron_expr, "
                "run_at = EXCLUDED.run_at, next_run_at = EXCLUDED.next_run_at, "
                "until_spec = EXCLUDED.until_spec, max_runs = EXCLUDED.max_runs, "
                "run_count = EXCLUDED.run_count, last_run_at = EXCLUDED.last_run_at, "
                "status = EXCLUDED.status",
                _params(job),
            )
        return job

    def update(self, job_id: str, **fields) -> Optional[dict]:
        fields = {k: v for k, v in fields.items() if v is not None}
        if not fields:
            return self.get(job_id)
        sets, params = [], []
        for k, v in fields.items():
            col = "until_spec" if k == "until" else k
            sets.append(f"{col} = %s")
            params.append(_to_ts(v) if k in _TS_FIELDS else v)
        params.append(job_id)
        with connection() as conn:
            row = conn.execute(
                f"UPDATE scheduled_jobs SET {', '.join(sets)} WHERE id = %s "
                f"RETURNING {_COLS}",
                params,
            ).fetchone()
        return _row(row) if row else None

    def delete(self, job_id: str) -> bool:
        with connection() as conn:
            cur = conn.execute("DELETE FROM scheduled_jobs WHERE id = %s", (job_id,))
            return cur.rowcount > 0
