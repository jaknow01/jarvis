"""Postgres-backed users + channel identities (the whitelist).

Mirrors the interface of the JSON ``UserStore`` in ``lib/domain/principals.py`` so it
is a drop-in backend selected when ``DATABASE_URL`` is set. The ``user_identities``
table's ``(channel, external_id)`` primary key IS the whitelist: an inbound pair with
no row here does not resolve to a user and is ignored.
"""
from __future__ import annotations

import logging
from datetime import datetime

import psycopg

from app.db.connection import connection

logger = logging.getLogger(__name__)


def _user_row(r) -> dict:
    def iso(v):
        return v.isoformat(timespec="seconds") if hasattr(v, "isoformat") else v
    return {"id": r[0], "display_name": r[1], "status": r[2], "created_at": iso(r[3])}


class PostgresUserStore:
    # -- reads ----------------------------------------------------------------
    def resolve_user(self, channel: str, external_id: str) -> str | None:
        with connection() as conn:
            row = conn.execute(
                "SELECT user_id FROM user_identities WHERE channel = %s AND external_id = %s",
                (channel, external_id),
            ).fetchone()
        return row[0] if row else None

    def get_user(self, user_id: str) -> dict | None:
        with connection() as conn:
            row = conn.execute(
                "SELECT id, display_name, status, created_at FROM users WHERE id = %s",
                (user_id,),
            ).fetchone()
        return _user_row(row) if row else None

    def list_users(self) -> list[dict]:
        with connection() as conn:
            rows = conn.execute(
                "SELECT id, display_name, status, created_at FROM users ORDER BY created_at, id"
            ).fetchall()
        return [_user_row(r) for r in rows]

    def identities_for(self, user_id: str) -> list[dict]:
        with connection() as conn:
            rows = conn.execute(
                "SELECT channel, external_id FROM user_identities WHERE user_id = %s ORDER BY channel",
                (user_id,),
            ).fetchall()
        return [{"channel": r[0], "external_id": r[1]} for r in rows]

    # -- writes ---------------------------------------------------------------
    def add_user(self, user_id: str, display_name: str | None, status: str = "active") -> dict:
        now = datetime.now()
        with connection() as conn:
            conn.execute(
                """
                INSERT INTO users (id, display_name, status, created_at)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    display_name = EXCLUDED.display_name, status = EXCLUDED.status
                """,
                (user_id, display_name, status, now),
            )
        return {"id": user_id, "display_name": display_name, "status": status,
                "created_at": now.isoformat(timespec="seconds")}

    def add_identity(self, user_id: str, channel: str, external_id: str) -> bool:
        """Whitelist a (channel, external_id) → user. Returns False if the pair already
        maps somewhere (uniqueness protects against ambiguous identities)."""
        try:
            with connection() as conn:
                conn.execute(
                    """
                    INSERT INTO user_identities (user_id, channel, external_id)
                    VALUES (%s, %s, %s)
                    """,
                    (user_id, channel, external_id),
                )
            return True
        except psycopg.errors.UniqueViolation:
            return False
