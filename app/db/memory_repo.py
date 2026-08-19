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
from lib.memory import (
    VALID_SOURCES,
    VALID_CONFIDENCE,
    SUMMARY_CAP,
    summarize,
)

logger = logging.getLogger(__name__)

_COLS = "id, text, category, source, confidence, created_at, updated_at"


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
    def all(self) -> list[dict]:
        with connection() as conn:
            rows = conn.execute(
                f"SELECT {_COLS} FROM memory_entries ORDER BY created_at, id"
            ).fetchall()
        return [_row(r) for r in rows]

    def by_category(self, category: str) -> list[dict]:
        with connection() as conn:
            rows = conn.execute(
                f"SELECT {_COLS} FROM memory_entries WHERE category = %s ORDER BY created_at, id",
                (category,),
            ).fetchall()
        return [_row(r) for r in rows]

    def summary(self, cap: int = SUMMARY_CAP) -> str:
        return summarize(self.all(), cap)

    # -- writes ---------------------------------------------------------------
    def add(self, text: str, category: str = "preferences",
            source: str = "user", confidence: str = "high") -> dict:
        text = (text or "").strip()
        if not text:
            raise ValueError("memory text cannot be empty")
        category = (category or "preferences").strip() or "preferences"
        source = source if source in VALID_SOURCES else "user"
        confidence = confidence if confidence in VALID_CONFIDENCE else "high"
        now = datetime.now()
        with connection() as conn:
            existing = conn.execute(
                "SELECT id FROM memory_entries WHERE lower(text) = lower(%s) AND category = %s",
                (text, category),
            ).fetchone()
            if existing:  # exact-text dedup within category: refresh instead of duplicating
                conn.execute(
                    "UPDATE memory_entries SET updated_at = %s, source = %s, confidence = %s WHERE id = %s",
                    (now, source, confidence, existing[0]),
                )
                row = conn.execute(f"SELECT {_COLS} FROM memory_entries WHERE id = %s", (existing[0],)).fetchone()
                return _row(row)
            entry_id = "mem_" + uuid.uuid4().hex[:8]
            conn.execute(
                f"INSERT INTO memory_entries ({_COLS}) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (entry_id, text, category, source, confidence, now, now),
            )
            row = conn.execute(f"SELECT {_COLS} FROM memory_entries WHERE id = %s", (entry_id,)).fetchone()
        return _row(row)

    def update(self, entry_id: str, text: Optional[str] = None,
               category: Optional[str] = None, confidence: Optional[str] = None) -> Optional[dict]:
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
        with connection() as conn:
            updated = conn.execute(
                f"UPDATE memory_entries SET {', '.join(sets)} WHERE id = %s RETURNING {_COLS}",
                params,
            ).fetchone()
        return _row(updated) if updated else None

    def delete(self, entry_id: str) -> bool:
        with connection() as conn:
            cur = conn.execute("DELETE FROM memory_entries WHERE id = %s", (entry_id,))
            return cur.rowcount > 0
