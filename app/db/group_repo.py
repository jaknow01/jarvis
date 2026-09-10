"""Postgres-backed groups + membership.

Mirrors the JSON ``GroupStore`` in ``lib/domain/principals.py``. A group carries a
``priority`` used to order groups during profile resolution (higher priority applied
later, so it wins on conflict). Membership is many-to-many.
"""
from __future__ import annotations

import logging

import psycopg

from app.db.connection import connection

logger = logging.getLogger(__name__)


def _group_row(r) -> dict:
    return {"id": r[0], "name": r[1], "priority": r[2]}


class PostgresGroupStore:
    # -- reads ----------------------------------------------------------------
    def get_group(self, group_id: str) -> dict | None:
        with connection() as conn:
            row = conn.execute(
                "SELECT id, name, priority FROM groups WHERE id = %s", (group_id,)
            ).fetchone()
        return _group_row(row) if row else None

    def get_group_by_name(self, name: str) -> dict | None:
        with connection() as conn:
            row = conn.execute(
                "SELECT id, name, priority FROM groups WHERE name = %s", (name,)
            ).fetchone()
        return _group_row(row) if row else None

    def list_groups(self) -> list[dict]:
        with connection() as conn:
            rows = conn.execute(
                "SELECT id, name, priority FROM groups ORDER BY priority, name"
            ).fetchall()
        return [_group_row(r) for r in rows]

    def groups_for_user(self, user_id: str) -> list[dict]:
        """The user's groups, sorted by ascending priority (resolution order)."""
        with connection() as conn:
            rows = conn.execute(
                """
                SELECT g.id, g.name, g.priority
                FROM groups g JOIN user_groups ug ON ug.group_id = g.id
                WHERE ug.user_id = %s
                ORDER BY g.priority, g.name
                """,
                (user_id,),
            ).fetchall()
        return [_group_row(r) for r in rows]

    def members_of(self, group_id: str) -> list[str]:
        with connection() as conn:
            rows = conn.execute(
                "SELECT user_id FROM user_groups WHERE group_id = %s", (group_id,)
            ).fetchall()
        return [r[0] for r in rows]

    # -- writes ---------------------------------------------------------------
    def add_group(self, group_id: str, name: str, priority: int = 0) -> dict:
        with connection() as conn:
            conn.execute(
                """
                INSERT INTO groups (id, name, priority) VALUES (%s, %s, %s)
                ON CONFLICT (name) DO UPDATE SET priority = EXCLUDED.priority
                """,
                (group_id, name, int(priority)),
            )
            row = conn.execute(
                "SELECT id, name, priority FROM groups WHERE name = %s", (name,)
            ).fetchone()
        return _group_row(row)

    def set_priority(self, group_id: str, priority: int) -> bool:
        with connection() as conn:
            cur = conn.execute(
                "UPDATE groups SET priority = %s WHERE id = %s", (int(priority), group_id)
            )
            return cur.rowcount > 0

    def add_membership(self, user_id: str, group_id: str) -> bool:
        try:
            with connection() as conn:
                conn.execute(
                    "INSERT INTO user_groups (user_id, group_id) VALUES (%s, %s)",
                    (user_id, group_id),
                )
            return True
        except psycopg.errors.UniqueViolation:
            return False

    def remove_membership(self, user_id: str, group_id: str) -> bool:
        with connection() as conn:
            cur = conn.execute(
                "DELETE FROM user_groups WHERE user_id = %s AND group_id = %s",
                (user_id, group_id),
            )
            return cur.rowcount > 0
