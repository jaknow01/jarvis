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
    # Watch v2: change-detection + repeating. `last_data` remembers the previous poll's raw
    # source reading so a predicate can compare against it (injected as data["_previous"]),
    # enabling "notify when X changed / new points appeared". `repeating` keeps the watch armed
    # after it fires (notify on every future edge) instead of completing after the first.
    """
    ALTER TABLE scheduled_jobs ADD COLUMN IF NOT EXISTS last_data JSONB
    """,
    """
    ALTER TABLE scheduled_jobs ADD COLUMN IF NOT EXISTS repeating BOOLEAN NOT NULL DEFAULT FALSE
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
    # ---- multi-user network: identities, users, groups, membership, layered config ----
    # A user is the stable principal (cognition owner). Identities map a channel-specific
    # sender to a user; the (channel, external_id) PK IS the whitelist — an inbound pair
    # absent from this table is ignored (no auto-provisioning). See docs/MULTI_USER.md.
    """
    CREATE TABLE IF NOT EXISTS users (
        id           TEXT PRIMARY KEY,
        display_name TEXT,
        status       TEXT NOT NULL DEFAULT 'active',
        created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS user_identities (
        user_id     TEXT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
        channel     TEXT NOT NULL,
        external_id TEXT NOT NULL,
        created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (channel, external_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS user_identities_by_user ON user_identities (user_id)
    """,
    # Groups are the shared-resource (household) unit AND a config layer. `priority`
    # orders groups during profile resolution (higher priority applied later → wins).
    """
    CREATE TABLE IF NOT EXISTS groups (
        id         TEXT PRIMARY KEY,
        name       TEXT NOT NULL UNIQUE,
        priority   INTEGER NOT NULL DEFAULT 0,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    # Many-to-many membership: a user may belong to multiple groups.
    """
    CREATE TABLE IF NOT EXISTS user_groups (
        user_id  TEXT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
        group_id TEXT NOT NULL REFERENCES groups (id) ON DELETE CASCADE,
        PRIMARY KEY (user_id, group_id)
    )
    """,
    # One table holds all three config layers, discriminated by `scope_type`
    # (default | group | user). `scope_id` is the group/user id; for the `default` layer
    # it is '' (empty), so it can stay in the PK (Postgres PK columns are NOT NULL).
    # `enabled`/`model`/`reasoning` are NULLABLE so "unset" is representable — the union
    # roster semantics depend on unset never disabling an agent (see agent-roster-resolution).
    """
    CREATE TABLE IF NOT EXISTS agent_config (
        scope_type TEXT NOT NULL CHECK (scope_type IN ('default','group','user')),
        scope_id   TEXT NOT NULL DEFAULT '',
        agent_name TEXT NOT NULL,
        enabled    BOOLEAN,
        model      TEXT,
        reasoning  TEXT,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (scope_type, scope_id, agent_name)
    )
    """,
    # Per-user scoping of cognition stores. Added idempotently; `user_id` is nullable so the
    # additive migration can run before the backfill. The memory dedup index moves from a
    # global (text, category) to a per-user one so two users may hold the same fact. COALESCE
    # keeps uniqueness well-defined for any not-yet-backfilled NULL rows.
    """
    ALTER TABLE memory_entries ADD COLUMN IF NOT EXISTS user_id TEXT
    """,
    """
    DROP INDEX IF EXISTS memory_entries_text_category
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS memory_entries_user_text_category
        ON memory_entries (COALESCE(user_id, ''), lower(text), category)
    """,
    """
    ALTER TABLE scheduled_jobs ADD COLUMN IF NOT EXISTS user_id TEXT
    """,
    """
    CREATE INDEX IF NOT EXISTS scheduled_jobs_by_user ON scheduled_jobs (user_id)
    """,
]


def init_db() -> None:
    """Create all tables/indexes if they do not exist."""
    with connection() as conn:
        for stmt in SCHEMA_STATEMENTS:
            conn.execute(stmt)
    logger.info("Database schema ensured")
