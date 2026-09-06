"""Postgres-backed store for the generic daily time-series ("observations").

One row per metric per day: ``(series, obs_date, value, payload, source)`` with PK
``(series, obs_date)`` so daily collection and year-long backfill are idempotent — a
re-run upserts the same calendar day rather than duplicating it. ``value`` is the primary
scalar (cheap SQL aggregates); ``payload`` keeps the fuller reading as JSONB.

``lib.domain.observations`` is the caller: it reads through this repo when a database is
configured and falls back to the on-disk JSON store otherwise (mirrors ``memory_repo``).

Best-effort by design — the database is optional; callers select the backend up front and
this module is only used once Postgres is known to be configured.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Iterable, Optional

from psycopg.types.json import Json

from app.db.connection import connection
from app.db.schema import init_db

logger = logging.getLogger(__name__)

_schema_ready = False


def _ensure_schema() -> None:
    """Create the tables on first use (idempotent). Cheap after the first call."""
    global _schema_ready
    if not _schema_ready:
        init_db()
        _schema_ready = True


def upsert_many(rows: Iterable[dict]) -> int:
    """Upsert a batch of observation dicts. Each row: ``series`` (str), ``date``
    (``datetime.date`` or ISO ``YYYY-MM-DD`` str), ``value`` (float|None), optional
    ``payload`` (dict) and ``source`` (str). Returns the number of rows written."""
    _ensure_schema()
    rows = list(rows)
    if not rows:
        return 0
    with connection() as conn:
        for r in rows:
            conn.execute(
                """
                INSERT INTO observations (series, obs_date, value, payload, source)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (series, obs_date) DO UPDATE SET
                    value = EXCLUDED.value,
                    payload = EXCLUDED.payload,
                    source = EXCLUDED.source,
                    fetched_at = now()
                """,
                (
                    r["series"],
                    r["date"],
                    r.get("value"),
                    Json(r["payload"]) if r.get("payload") is not None else None,
                    r.get("source"),
                ),
            )
    return len(rows)


def read_series(series: str, start: Optional[date] = None, end: Optional[date] = None) -> list[dict]:
    """Return the rows for ``series`` between ``start`` and ``end`` (inclusive), oldest
    first, as ``{date, value, payload}`` dicts. ``start``/``end`` are optional bounds."""
    _ensure_schema()
    clauses = ["series = %s"]
    params: list = [series]
    if start is not None:
        clauses.append("obs_date >= %s")
        params.append(start)
    if end is not None:
        clauses.append("obs_date <= %s")
        params.append(end)
    sql = (
        "SELECT obs_date, value, payload FROM observations WHERE "
        + " AND ".join(clauses)
        + " ORDER BY obs_date ASC"
    )
    with connection() as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
    return [{"date": d, "value": v, "payload": p} for (d, v, p) in rows]


def latest_date(series: str) -> Optional[date]:
    """Most recent ``obs_date`` stored for ``series``, or None if the series is empty."""
    _ensure_schema()
    with connection() as conn:
        row = conn.execute(
            "SELECT max(obs_date) FROM observations WHERE series = %s", (series,)
        ).fetchone()
    return row[0] if row else None


def list_series() -> list[str]:
    """Distinct series keys currently stored, alphabetically."""
    _ensure_schema()
    with connection() as conn:
        rows = conn.execute("SELECT DISTINCT series FROM observations ORDER BY series").fetchall()
    return [r[0] for r in rows]
