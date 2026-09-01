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
    # Cached mini-league squad snapshots (one JSONB blob per league+gameweek). Scanning a
    # league's ownership means fetching every manager's picks — dozens of API calls — which
    # is the fpl_agent's biggest latency cost and is asked about repeatedly ("who else has
    # X", "how many own Y"). Picks for a gameweek are frozen once its deadline passes, so a
    # snapshot is safe to reuse; a fetch timestamp drives TTL-based refresh. `complete` marks
    # whether the whole league was captured (vs capped at `max_managers`) so a later, larger
    # scan knows whether the cache still suffices. National/global leagues are never warmed in
    # the background (see lib/runtime/fpl_cache_runner.py); they only land here if queried.
    """
    CREATE TABLE IF NOT EXISTS fpl_league_squads (
        league_id    BIGINT NOT NULL,
        gameweek     INTEGER NOT NULL,
        league_name  TEXT,
        max_managers INTEGER NOT NULL,
        complete     BOOLEAN NOT NULL DEFAULT FALSE,
        data         JSONB NOT NULL,
        fetched_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (league_id, gameweek)
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
    # Conditional "watch" jobs (kind="watch", see lib/agents/scheduler_agent/watch_*.py): poll a named
    # data source every `poll_interval` seconds, evaluate `check_code` against the fetched
    # data, and deliver (edge-trigger via `last_result`, tri-state: NULL=unknown/true/false).
    # Added idempotently so init_db() stays safe on pre-existing tables. The delivery text
    # reuses the existing `prompt` column.
    """
    ALTER TABLE scheduled_jobs ADD COLUMN IF NOT EXISTS source TEXT
    """,
    """
    ALTER TABLE scheduled_jobs ADD COLUMN IF NOT EXISTS params JSONB
    """,
    """
    ALTER TABLE scheduled_jobs ADD COLUMN IF NOT EXISTS check_code TEXT
    """,
    """
    ALTER TABLE scheduled_jobs ADD COLUMN IF NOT EXISTS poll_interval INTEGER
    """,
    """
    ALTER TABLE scheduled_jobs ADD COLUMN IF NOT EXISTS last_result BOOLEAN
    """,
    # Conversation history (owned by us now that previous_response_id / the Responses
    # API continuity is gone). A conversation is identified by a stable per-channel
    # key (e.g. 'repl', 'messenger:{psid}') so channels/users never share history.
    """
    CREATE TABLE IF NOT EXISTS conversations (
        id             UUID PRIMARY KEY,
        channel_key    TEXT NOT NULL UNIQUE,
        created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
        last_active_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    # Only the user's message and the composer's final reply are stored per turn
    # (never the internal coordinator/sub-agent/tool exchanges). `turn_index` orders
    # messages within a conversation; `mlflow_trace_id` links a reply to its run.
    """
    CREATE TABLE IF NOT EXISTS messages (
        id              UUID PRIMARY KEY,
        conversation_id UUID NOT NULL REFERENCES conversations (id) ON DELETE CASCADE,
        role            TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
        content         TEXT NOT NULL,
        turn_index      INTEGER NOT NULL,
        created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
        mlflow_trace_id TEXT
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS messages_by_conversation
        ON messages (conversation_id, turn_index)
    """,
    # Rolling-window context engineering: a per-conversation running summary of the
    # turns that have fallen outside the verbatim window, updated incrementally as
    # exchanges age out (see lib/runtime/history_window.py). `summary_upto` is the
    # number of message rows already folded into `summary` (kept in step with
    # `turn_index`), so only the freshly-evicted exchange is compressed each turn.
    # ADD COLUMN IF NOT EXISTS keeps init_db() idempotent on pre-existing tables.
    """
    ALTER TABLE conversations ADD COLUMN IF NOT EXISTS summary TEXT NOT NULL DEFAULT ''
    """,
    """
    ALTER TABLE conversations ADD COLUMN IF NOT EXISTS summary_upto INTEGER NOT NULL DEFAULT 0
    """,
]


def init_db() -> None:
    """Create all tables/indexes if they do not exist."""
    with connection() as conn:
        for stmt in SCHEMA_STATEMENTS:
            conn.execute(stmt)
    logger.info("Database schema ensured")
