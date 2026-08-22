"""Database schema (DDL) and initialization.

Idempotent: every statement is ``CREATE ... IF NOT EXISTS`` so ``init_db()`` is safe
to call on every startup.
"""
from __future__ import annotations

import logging

from app.db.connection import connection

logger = logging.getLogger(__name__)

SCHEMA_STATEMENTS = [
    # general long-term memory (the active, agent-written store)
    """
    CREATE TABLE IF NOT EXISTS memory_entries (
        id          TEXT PRIMARY KEY,
        text        TEXT NOT NULL,
        category    TEXT NOT NULL,
        source      TEXT NOT NULL,
        confidence  TEXT NOT NULL,
        created_at  TIMESTAMPTZ NOT NULL,
        updated_at  TIMESTAMPTZ NOT NULL
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS memory_entries_text_category
        ON memory_entries (lower(text), category)
    """,
    # smart-lighting device inventory (currently read from disk; migrated for future use)
    """
    CREATE TABLE IF NOT EXISTS smart_devices (
        dev_id      TEXT PRIMARY KEY,
        custom_name TEXT,
        room        TEXT,
        zones       JSONB,
        local_ip    TEXT,
        local_key   TEXT,
        version     TEXT,
        params      JSONB
    )
    """,
    # domain preference/memory documents kept whole as JSONB (freeform, edited rarely)
    """
    CREATE TABLE IF NOT EXISTS device_preferences (
        name       TEXT PRIMARY KEY,
        data       JSONB NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS maps_memory (
        name       TEXT PRIMARY KEY,
        data       JSONB NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    # Fantasy Premier League reference data (the bootstrap-static snapshot: teams,
    # players, positions, gameweeks). Cached wholesale as JSONB with a fetch timestamp
    # so the fpl_agent can resolve player/team ids without hitting the API every turn.
    """
    CREATE TABLE IF NOT EXISTS fpl_reference (
        id         TEXT PRIMARY KEY,
        data       JSONB NOT NULL,
        fetched_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    # Scheduled jobs written by the scheduler_agent: a natural-language prompt for
    # Jarvis, when to run it (one-shot run_at or a cron_expr), and where to deliver
    # the reply (channel + target). The runner fires due jobs out of band. `until` is
    # stored as TEXT (`until_spec`) to preserve a date-only inclusive boundary as given.
    """
    CREATE TABLE IF NOT EXISTS scheduled_jobs (
        id              TEXT PRIMARY KEY,
        prompt          TEXT NOT NULL,
        channel         TEXT NOT NULL,
        target          TEXT,
        conversation_id TEXT NOT NULL,
        kind            TEXT NOT NULL,
        cron_expr       TEXT,
        run_at          TIMESTAMPTZ,
        next_run_at     TIMESTAMPTZ,
        until_spec      TEXT,
        max_runs        INTEGER,
        run_count       INTEGER NOT NULL DEFAULT 0,
        last_run_at     TIMESTAMPTZ,
        status          TEXT NOT NULL DEFAULT 'active',
        created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS scheduled_jobs_due
        ON scheduled_jobs (status, next_run_at)
    """,
]


def init_db() -> None:
    """Create all tables/indexes if they do not exist."""
    with connection() as conn:
        for stmt in SCHEMA_STATEMENTS:
            conn.execute(stmt)
    logger.info("Database schema ensured")
