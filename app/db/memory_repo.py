"""Postgres-backed long-term memory store.

Mirrors the interface of the JSON ``MemoryStore`` in ``lib/memory.py`` (all /
by_category / summary / add / update / delete), so it is a drop-in backend selected
by ``lib.memory`` when ``DATABASE_URL`` is set.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Optional

from app.db.connection import connection
from lib.domain.memory import (
    VALID_SOURCES,
    VALID_CONFIDENCE,
    SUMMARY_CAP,
    summarize,
)
from lib.domain.principals import OWNER_USER_ID

logger = logging.getLogger(__name__)

_COLS = "id, text, category, source, confidence, created_at, updated_at"

# Reusable per-user WHERE fragment: matches the requested user's rows, and also any
# not-yet-backfilled NULL rows when the requested user is the migrated owner.
_USER_WHERE = "(user_id = %s OR (user_id IS NULL AND %s = %s))"


def _user_params(user_id: str) -> tuple:
    return (user_id, user_id, OWNER_USER_ID)


def _row(r) -> dict:
    def iso(v):
        return v.isoformat(timespec="seconds") if hasattr(v, "isoformat") else v
    return {
        "id": r[0],
        "text": r[1],
        "category": r[2],
        "source": r[3],
        "confidence": r[4],
        "created_at": iso(r[5]),
        "updated_at": iso(r[6]),
    }


class PostgresMemoryStore:
    # -- reads ----------------------------------------------------------------
    def all(self, user_id: Optional[str] = None) -> list[dict]:
        with connection() as conn:
            if user_id is None:
                rows = conn.execute(
                    f"SELECT {_COLS} FROM memory_entries ORDER BY created_at, id"
                ).fetchall()
            else:
                rows = conn.execute(
                    f"SELECT {_COLS} FROM memory_entries WHERE {_USER_WHERE} ORDER BY created_at, id",
                    _user_params(user_id),
                ).fetchall()
        return [_row(r) for r in rows]

    def by_category(self, category: str, user_id: Optional[str] = None) -> list[dict]:
        return [e for e in self.all(user_id) if e.get("category") == category]

    def summary(self, cap: int = SUMMARY_CAP, user_id: Optional[str] = None) -> str:
        return summarize(self.all(user_id), cap)

    # -- writes ---------------------------------------------------------------
    def add(self, text: str, category: str = "preferences",
            source: str = "user", confidence: str = "high",
            user_id: Optional[str] = None) -> dict:
        text = (text or "").strip()
        if not text:
            raise ValueError("memory text cannot be empty")
        category = (category or "preferences").strip() or "preferences"
        source = source if source in VALID_SOURCES else "user"
        confidence = confidence if confidence in VALID_CONFIDENCE else "high"
        now = datetime.now()
        with connection() as conn:
            if user_id is None:
                existing = conn.execute(
                    "SELECT id FROM memory_entries WHERE lower(text) = lower(%s) AND category = %s",
                    (text, category),
                ).fetchone()
            else:  # exact-text dedup scoped to this user (+ legacy owner rows)
                existing = conn.execute(
                    f"SELECT id FROM memory_entries WHERE lower(text) = lower(%s) AND category = %s "
                    f"AND {_USER_WHERE}",
                    (text, category, *_user_params(user_id)),
                ).fetchone()
            if existing:  # refresh instead of duplicating
                conn.execute(
                    "UPDATE memory_entries SET updated_at = %s, source = %s, confidence = %s WHERE id = %s",
                    (now, source, confidence, existing[0]),
                )
                row = conn.execute(f"SELECT {_COLS} FROM memory_entries WHERE id = %s", (existing[0],)).fetchone()
                return _row(row)
            entry_id = "mem_" + uuid.uuid4().hex[:8]
            conn.execute(
                f"INSERT INTO memory_entries ({_COLS}, user_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (entry_id, text, category, source, confidence, now, now, user_id),
            )
            row = conn.execute(f"SELECT {_COLS} FROM memory_entries WHERE id = %s", (entry_id,)).fetchone()
        return _row(row)

    def update(self, entry_id: str, text: Optional[str] = None,
               category: Optional[str] = None, confidence: Optional[str] = None,
               user_id: Optional[str] = None) -> Optional[dict]:
        sets, params = [], []
        if text is not None:
            sets.append("text = %s")
            params.append(text.strip())
        if category is not None:
            sets.append("category = %s")
            params.append(category.strip())
        if confidence in VALID_CONFIDENCE:
            sets.append("confidence = %s")
            params.append(confidence)
        sets.append("updated_at = %s")
        params.append(datetime.now())
        params.append(entry_id)
        where = "id = %s"
        if user_id is not None:
            where += f" AND {_USER_WHERE}"
            params.extend(_user_params(user_id))
        with connection() as conn:
            updated = conn.execute(
                f"UPDATE memory_entries SET {', '.join(sets)} WHERE {where} RETURNING {_COLS}",
                params,
            ).fetchone()
        return _row(updated) if updated else None

    def delete(self, entry_id: str, user_id: Optional[str] = None) -> bool:
        with connection() as conn:
            if user_id is None:
                cur = conn.execute("DELETE FROM memory_entries WHERE id = %s", (entry_id,))
            else:
                cur = conn.execute(
                    f"DELETE FROM memory_entries WHERE id = %s AND {_USER_WHERE}",
                    (entry_id, *_user_params(user_id)),
                )
            return cur.rowcount > 0
