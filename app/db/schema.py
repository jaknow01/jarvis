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
]


def init_db() -> None:
    """Create all tables/indexes if they do not exist."""
    with connection() as conn:
        for stmt in SCHEMA_STATEMENTS:
            conn.execute(stmt)
    logger.info("Database schema ensured")
