"""Postgres-backed conversation-history store.

Mirrors the interface of the JSON ``ConversationStore`` in
``lib/domain/conversation.py`` (load_history / record_turn), so it is a drop-in
backend selected by ``lib.domain.conversation`` when ``DATABASE_URL`` is set.

A conversation is resolved/created by its unique ``channel_key``; each turn appends
exactly two message rows (the user's message and the assistant reply), with a
per-conversation monotonic ``turn_index`` and the assistant row stamped with the
run's MLflow trace id (empty when tracing is off).
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime

from app.db.connection import connection

logger = logging.getLogger(__name__)


class PostgresConversationStore:
    # -- helpers --------------------------------------------------------------
    def _resolve_conversation(self, conn, channel_key: str) -> str:
        """Return the conversation id for ``channel_key``, creating it if needed."""
        row = conn.execute(
            "SELECT id FROM conversations WHERE channel_key = %s", (channel_key,)
        ).fetchone()
        if row:
            return row[0]
        conv_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO conversations (id, channel_key, created_at, last_active_at) "
            "VALUES (%s, %s, now(), now())",
            (conv_id, channel_key),
        )
        return conv_id

    # -- reads ----------------------------------------------------------------
    def load_history(self, channel_key: str) -> list[dict]:
        with connection() as conn:
            conv = conn.execute(
                "SELECT id FROM conversations WHERE channel_key = %s", (channel_key,)
            ).fetchone()
            if not conv:
                return []
            rows = conn.execute(
                "SELECT role, content FROM messages WHERE conversation_id = %s "
                "ORDER BY turn_index, created_at",
                (conv[0],),
            ).fetchall()
        return [{"role": r[0], "content": r[1]} for r in rows]

    def load_summary(self, channel_key: str) -> tuple[str, int]:
        """Return ``(summary, summary_upto)`` for the channel (``("", 0)`` if absent)."""
        with connection() as conn:
            row = conn.execute(
                "SELECT summary, summary_upto FROM conversations WHERE channel_key = %s",
                (channel_key,),
            ).fetchone()
        if not row:
            return "", 0
        return (row[0] or ""), int(row[1] or 0)

    def load_history_and_summary(self, channel_key: str) -> tuple[list[dict], str, int]:
        """Return ``(history, summary, summary_upto)`` over a SINGLE connection — the
        hot-path read the engine does each turn. Folds what used to be two separate
        ``load_history`` + ``load_summary`` connections (each a connect/auth/close) into one:
        one lookup for the conversation row (id + summary), one for its messages."""
        with connection() as conn:
            conv = conn.execute(
                "SELECT id, summary, summary_upto FROM conversations WHERE channel_key = %s",
                (channel_key,),
            ).fetchone()
            if not conv:
                return [], "", 0
            conv_id, summary, summary_upto = conv
            rows = conn.execute(
                "SELECT role, content FROM messages WHERE conversation_id = %s "
                "ORDER BY turn_index, created_at",
                (conv_id,),
            ).fetchall()
        history = [{"role": r[0], "content": r[1]} for r in rows]
        return history, (summary or ""), int(summary_upto or 0)

    # -- writes ---------------------------------------------------------------
    def save_summary(self, channel_key: str, summary: str, summary_upto: int) -> None:
        """Persist the rolling summary + coverage pointer, creating the row if needed."""
        with connection() as conn:
            conv_id = self._resolve_conversation(conn, channel_key)
            conn.execute(
                "UPDATE conversations SET summary = %s, summary_upto = %s WHERE id = %s",
                (summary or "", int(summary_upto), conv_id),
            )

    def record_turn(self, channel_key: str, user_text: str, assistant_text: str,
                    trace_id: str = "") -> None:
        now = datetime.now()
        with connection() as conn:
            conv_id = self._resolve_conversation(conn, channel_key)
            base = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE conversation_id = %s", (conv_id,)
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO messages (id, conversation_id, role, content, turn_index, "
                "created_at, mlflow_trace_id) VALUES (%s, %s, 'user', %s, %s, %s, %s)",
                (str(uuid.uuid4()), conv_id, user_text or "", base, now, ""),
            )
            conn.execute(
                "INSERT INTO messages (id, conversation_id, role, content, turn_index, "
                "created_at, mlflow_trace_id) VALUES (%s, %s, 'assistant', %s, %s, %s, %s)",
                (str(uuid.uuid4()), conv_id, assistant_text or "", base + 1, now, trace_id or ""),
            )
            conn.execute(
                "UPDATE conversations SET last_active_at = %s WHERE id = %s",
                (now, conv_id),
            )

    def reset(self, channel_key: str) -> bool:
        """Clear a channel's messages and rolling summary (admin ``/reset``).

        Returns True if the conversation existed. Deletes the message rows and blanks the
        summary but keeps the (empty) conversation row so its id stays stable.
        """
        with connection() as conn:
            row = conn.execute(
                "SELECT id FROM conversations WHERE channel_key = %s", (channel_key,)
            ).fetchone()
            if not row:
                return False
            conv_id = row[0]
            conn.execute("DELETE FROM messages WHERE conversation_id = %s", (conv_id,))
            conn.execute(
                "UPDATE conversations SET summary = '', summary_upto = 0, "
                "last_active_at = now() WHERE id = %s",
                (conv_id,),
            )
        return True
