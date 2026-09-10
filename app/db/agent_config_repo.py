"""Postgres-backed layered agent config.

One table (``agent_config``) holds all three resolution layers, discriminated by
``scope_type`` (``default`` | ``group`` | ``user``). ``scope_id`` is the group/user id;
for the ``default`` layer it is ``''``. ``enabled``/``model``/``reasoning`` are nullable
so "unset" is representable — the roster union semantics depend on unset never disabling
an agent (see the ``agent-roster-resolution`` capability).

Mirrors the JSON ``AgentConfigStore`` in ``lib/domain/agent_config.py``.
"""
from __future__ import annotations

import logging

from app.db.connection import connection

logger = logging.getLogger(__name__)


class PostgresAgentConfigStore:
    def get_layer(self, scope_type: str, scope_id: str = "") -> dict:
        """Return ``{agent_name: {enabled, model, reasoning}}`` for one layer."""
        with connection() as conn:
            rows = conn.execute(
                """
                SELECT agent_name, enabled, model, reasoning
                FROM agent_config WHERE scope_type = %s AND scope_id = %s
                """,
                (scope_type, scope_id),
            ).fetchall()
        return {
            r[0]: {"enabled": r[1], "model": r[2], "reasoning": r[3]} for r in rows
        }

    def set(self, scope_type: str, scope_id: str, agent_name: str,
            enabled=None, model=None, reasoning=None) -> None:
        """Upsert one agent's config in one layer. Only the provided fields are written;
        others keep their existing value (merge-update)."""
        with connection() as conn:
            conn.execute(
                """
                INSERT INTO agent_config (scope_type, scope_id, agent_name, enabled, model, reasoning)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (scope_type, scope_id, agent_name) DO UPDATE SET
                    enabled   = COALESCE(EXCLUDED.enabled,   agent_config.enabled),
                    model     = COALESCE(EXCLUDED.model,     agent_config.model),
                    reasoning = COALESCE(EXCLUDED.reasoning, agent_config.reasoning),
                    updated_at = now()
                """,
                (scope_type, scope_id, agent_name, enabled, model, reasoning),
            )

    def delete(self, scope_type: str, scope_id: str, agent_name: str) -> bool:
        with connection() as conn:
            cur = conn.execute(
                """
                DELETE FROM agent_config
                WHERE scope_type = %s AND scope_id = %s AND agent_name = %s
                """,
                (scope_type, scope_id, agent_name),
            )
            return cur.rowcount > 0
